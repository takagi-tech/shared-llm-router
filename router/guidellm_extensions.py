"""Narrow GuideLLM 0.7.3 adaptations, exercised by runner integration tests."""

import asyncio
import time
from typing import Literal

import httpx
from guidellm.backends.backend import Backend, BackendArgs
from guidellm.backends.openai.http import OpenAIHTTPBackend, OpenAIHTTPBackendArgs
from guidellm.benchmark.schemas import GenerativeBenchmarkAccumulator
from guidellm.data.finalizers import FinalizerRegistry
from guidellm.data.finalizers.generative import GenerativeRequestFinalizer, GenerativeRequestFinalizerArgs
from guidellm.data.schemas import DataFinalizerArgs
from guidellm.scheduler.constraints.request import MaxDurationConstraint
from pydantic import Field
from transformers import AutoTokenizer


@BackendArgs.register("llm_router_openai_http")
class RouterHTTPArgs(OpenAIHTTPBackendArgs):
    kind: Literal["llm_router_openai_http"] = "llm_router_openai_http"
    submission_duration: float | None = None


@Backend.register("llm_router_openai_http")
class RouterHTTPBackend(OpenAIHTTPBackend):
    async def process_startup(self):
        if self._in_process:
            raise RuntimeError("Backend already started")
        self._async_client = httpx.AsyncClient(
            http2=self._args.http2,
            timeout=httpx.Timeout(None, connect=5),
            verify=self._args.verify,
            follow_redirects=self._args.follow_redirects,
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=None),
            trust_env=False,
        )
        self._in_process = True

    async def resolve(self, request, request_info, history=None):
        # GuideLLM prefetches requests. Expired queued requests must never reach the
        # server, while HTTP streams already sent remain free of read deadlines.
        limit = self._args.submission_duration
        if limit is not None and time.time() >= request_info.scheduler_start_time + limit:
            raise asyncio.CancelledError("submission_window_closed")
        async for result in super().resolve(request, request_info, history):
            yield result


class SubmissionDuration(MaxDurationConstraint):
    """Stop scheduling new work, drain already accepted requests without a deadline."""

    def __call__(self, state, request_info):
        # GuideLLM may temporarily expose +inf before the first response.
        # Use the scheduler's shared start time, also used by the HTTP dispatch gate.
        timing_state = state.model_copy(update={"start_requests_time": state.start_time})
        action = super().__call__(timing_state, request_info)
        drained = action.request_queuing == "stop" and state.processed_requests >= state.created_requests
        action.request_processing = "stop_local" if drained else "continue"
        action.progress.stop_time = None
        action.metadata.pop("stop_time", None)
        return action


@DataFinalizerArgs.register("llm_router_token_counted")
class TokenCountedArgs(GenerativeRequestFinalizerArgs):
    kind: Literal["llm_router_token_counted"] = "llm_router_token_counted"
    tokenizer_model: str
    tokenizer_revision: str = "main"
    output_tokens: int = 128


@FinalizerRegistry.register("llm_router_token_counted")
class TokenCountedFinalizer(GenerativeRequestFinalizer):
    def __init__(self, config):
        super().__init__(config)
        self.tokenizer = AutoTokenizer.from_pretrained(
            config.tokenizer_model, revision=config.tokenizer_revision, trust_remote_code=False
        )

    def finalize_turn(self, columns):
        if any(
            columns.get(k)
            for k in ("image_column", "audio_column", "video_column", "tools_column", "tool_response_column")
        ):
            raise ValueError("Only text benchmark datasets are supported")
        request, settings = super().finalize_turn(columns)
        pieces = [
            p for key in ("prefix_column", "text_column") for p in columns.get(key, []) if isinstance(p, str)
        ]
        if pieces:
            request.input_metrics.text_tokens = sum(
                len(self.tokenizer.encode(p, add_special_tokens=False)) for p in pieces
            )
        if request.output_metrics.text_tokens is None:
            request.output_metrics.text_tokens = self.config.output_tokens
        return request, settings


class ObservedAccumulator(GenerativeBenchmarkAccumulator):
    """Record numeric provenance before GuideLLM merges request/response metrics."""

    token_sources: dict = Field(
        default_factory=lambda: {
            "completed": 0,
            "not_sent": 0,
            "server_input_known": 0,
            "server_output_known": 0,
            "server_input_total": 0,
            "server_output_total": 0,
            "client_input_total": 0,
            "requested_output_total": 0,
            "client_input_known": 0,
            "requested_output_known": 0,
        }
    )

    def update_estimate(self, response, request, info, scheduler_state):
        if info.status == "cancelled" and info.timings.request_start is None:
            self.token_sources["not_sent"] += 1
        if info.status == "completed":
            self.token_sources["completed"] += 1
            values = {
                "server_input": response.input_metrics.text_tokens if response else None,
                "server_output": response.output_metrics.text_tokens if response else None,
                "client_input": request.input_metrics.text_tokens,
                "requested_output": request.output_metrics.text_tokens,
            }
            for name, value in values.items():
                if value is not None:
                    self.token_sources[name + "_known"] += 1
                    self.token_sources[name + "_total"] += value
        return super().update_estimate(response, request, info, scheduler_state)


async def run_scenario(scenario, progress, extra_constraints):
    """Use GuideLLM's factories and Benchmarker, with the provenance accumulator."""
    from guidellm.benchmark import Benchmarker
    from guidellm.benchmark.entrypoints import (
        resolve_backend,
        resolve_constraints,
        resolve_profile,
        resolve_tokenizer,
    )
    from guidellm.benchmark.schemas import GenerativeBenchmark
    from guidellm.data import create_data_loader
    from guidellm.scheduler import NonDistributedEnvironment

    args = scenario.spec
    backend, model = await resolve_backend(args.backend)
    await resolve_tokenizer(args, model)
    loader = await create_data_loader(
        loader_config=args.data_loader,
        data_config=args.data,
        tokenizer_config=args.tokenizer,
        column_mapper_config=args.data_column_mapper,
        preprocessors_config=args.data_preprocessors,
        finalizer_config=args.data_finalizer,
        random_seed=args.seed.value,
    )
    constraints = resolve_constraints(args, **extra_constraints)
    profile = await resolve_profile(args.profile, constraints, random_seed=args.seed.value)
    async for _ in Benchmarker().run(
        accumulator_class=ObservedAccumulator,
        benchmark_class=GenerativeBenchmark,
        requests=loader,
        backend=backend,
        profile=profile,
        environment=NonDistributedEnvironment(),
        progress=progress,
        sample_size=0,
        warmup=args.profile.warmup,
        cooldown=args.profile.cooldown,
        prefer_response_metrics=True,
    ):
        pass
