"""One disposable process per trial. Only the runner image imports GuideLLM."""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    temp.replace(path)


async def execute(spec, destination):
    from guidellm.benchmark.progress import BenchmarkerProgress
    from guidellm.benchmark.schemas import BenchmarkArgs, BenchmarkScenario
    from guidellm.logger import logger
    from guidellm.scheduler.constraints.request import MaxDurationConstraintArgs

    from .guidellm_extensions import SubmissionDuration, run_scenario

    logger.remove()  # Exceptions and backend logs may contain request bodies/credentials.

    state = {"status": "preparing", "current_step": 0, "completed_requests": 0, "stages": [], "progress": {}}

    def save():
        atomic_json(destination, state)

    class Progress(BenchmarkerProgress):
        async def on_initialize(self, profile):
            state["status"] = "running"
            save()

        async def on_benchmark_start(self, strategy):
            state["current_step"] += 1
            state["progress"] = {}
            save()

        async def on_benchmark_update(self, accumulator, scheduler_state):
            counts = accumulator.scheduler_metrics.requests_made.model_dump(mode="json")
            self.token_sources = accumulator.token_sources
            state["progress"] = {"counts": counts, "token_sources": dict(self.token_sources)}
            state["completed_requests"] = (
                sum(s["metrics"]["request_totals"]["total"] for s in state["stages"])
                + counts["total"]
                - self.token_sources.get("not_sent", 0)
            )
            now = time.monotonic()
            if now - getattr(self, "last_saved", 0) >= 0.5:
                save()
                self.last_saved = now

        async def on_benchmark_complete(self, benchmark):
            # Allowlist prevents backend config, request/response bodies, errors and secrets escaping.
            native = benchmark.model_dump(
                mode="json", include={"metrics", "scheduler_metrics", "start_time", "end_time", "duration"}
            )
            native["token_sources"] = dict(getattr(self, "token_sources", {}))
            native["strategy"] = benchmark.config.strategy.model_dump(mode="json")
            fields = (
                "prompt_tokens",
                "output_tokens",
                "request_latency",
                "time_to_first_token_ms",
                "time_per_output_token_ms",
                "inter_token_latency_ms",
            )
            native["coverage"] = {
                k: sum(getattr(r, k) is not None for r in benchmark.requests.successful) for k in fields
            }
            native["not_sent"] = native["token_sources"].get("not_sent", 0)
            # Error messages may contain prompts or upstream credentials: retain categories only.
            import re

            categories = {}
            for row in benchmark.requests.errored:
                match = re.search(r"(?:Client|Server) error '([0-9]{3})", row.info.error or "")
                error_class = re.match(r"([A-Za-z][A-Za-z0-9_]{0,60})\(", row.info.error or "")
                category = "http_" + match[1] if match else error_class[1] if error_class else "request_error"
                categories[category] = categories.get(category, 0) + 1
            native["error_categories"] = categories
            state["stages"].append(native)
            state["completed_requests"] = sum(
                s["metrics"]["request_totals"]["total"] for s in state["stages"]
            )
            save()
            if native["metrics"]["request_totals"]["errored"] >= spec.get("max_errors", 1):
                raise TrialErrors()

        async def on_finalize(self):
            save()

    class TrialErrors(Exception):
        pass

    save()
    try:
        spec["scenario"]["backend"]["kind"] = "llm_router_openai_http"
        spec["scenario"]["backend"]["submission_duration"] = spec.get("duration_seconds")
        spec["scenario"]["data_finalizer"] = {
            "kind": "llm_router_token_counted",
            "tokenizer_model": spec["scenario"]["tokenizer"]["model"],
            "tokenizer_revision": spec["scenario"]["tokenizer"]["load_kwargs"]["revision"],
            "output_tokens": spec.get("output_tokens", 128),
        }
        BenchmarkArgs.reload_schema()
        BenchmarkScenario.reload_schema()
        scenario = BenchmarkScenario.model_validate({"spec": spec["scenario"]})
        if spec.get("preview_only"):
            from guidellm.data import create_data_loader

            args = scenario.spec
            args.data_loader.samples = min(args.data_loader.samples or 3, 3)
            loader = await create_data_loader(
                loader_config=args.data_loader,
                data_config=args.data,
                tokenizer_config=args.tokenizer,
                column_mapper_config=args.data_column_mapper,
                preprocessors_config=args.data_preprocessors,
                finalizer_config=args.data_finalizer,
                random_seed=args.seed.value,
            )
            state["preview"] = [
                {
                    "input_tokens": req.input_metrics.text_tokens,
                    "output_tokens": req.output_metrics.text_tokens,
                }
                for turns in loader
                for req, _ in turns
            ]
            state["status"] = "completed"
            return
        constraints = {}
        if spec.get("duration_seconds"):
            constraints["submission_duration"] = SubmissionDuration(
                args=MaxDurationConstraintArgs(seconds=spec["duration_seconds"])
            )
        await run_scenario(scenario, Progress(), constraints)
        state["status"] = (
            "failed"
            if any(s["metrics"]["request_totals"]["errored"] for s in state["stages"])
            else "completed"
        )
    except asyncio.CancelledError:
        state["status"] = "cancelled"
    except Exception as exc:
        state["status"] = "failed"
        state["error_type"] = type(exc).__name__  # Never persist exception text.
        if type(exc).__name__ == "ValidationError":
            state["error_fields"] = [".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)]
    finally:
        save()


async def main():
    spec = json.loads(sys.stdin.readline())
    destination = Path(sys.argv[1])
    task = asyncio.create_task(execute(spec, destination))
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    await task


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(main())
