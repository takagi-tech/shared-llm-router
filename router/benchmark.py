import asyncio
import contextlib
import json
import secrets
import time
import uuid

import httpx

from .engines import ENGINES, TrialInput
from .proxy import RouterError

BENCHMARK_USER = "system-benchmark"
ACTIVE = ("pending", "draining", "preparing", "running", "stopping")


class BenchmarkManager:
    def __init__(self, db, service, settings):
        self.db, self.service, self.settings = db, service, settings
        self.tasks = {}
        self.stop_events = {}
        self.tokens = {}
        self.previews = {}
        self.client = httpx.AsyncClient(base_url=settings.runner_url, timeout=12, trust_env=False)

    async def rpc(self, method, path, **kwargs):
        try:
            key = (await asyncio.to_thread(self.settings.runner_key_file.read_text)).strip()
            response = await self.client.request(
                method, path, headers={"Authorization": "Bearer " + key}, **kwargs
            )
            if response.status_code == 404 and method == "DELETE":
                return {"ok": True}
            response.raise_for_status()
            return response.json()
        except (OSError, httpx.HTTPError):
            raise RouterError(
                503, "runner_unavailable", "GuideLLM runnerに接続できません。Dockerの状態を確認してください"
            ) from None

    async def initialize(self):
        await self.db.execute(
            "INSERT OR IGNORE INTO users(id,name,enabled,system,created_at) VALUES(?,?,0,1,?)",
            (BENCHMARK_USER, "負荷試験", time.time()),
        )
        # DB recovery has already marked interrupted jobs. Reconcile only unfinished external runs.
        for row in await self.db.all("SELECT id,config FROM benchmarks WHERE status='interrupted'"):
            if json.loads(row["config"]).get("engine") == "guidellm":
                with contextlib.suppress(RouterError):
                    await self.rpc("POST", f"/jobs/{row['id']}/stop")

    async def validate_assets(self, config):
        if config.options["tokenizer_source"] == "local":
            path = self.settings.data_dir / "tokenizers" / config.options["tokenizer"] / "manifest.json"
            try:
                return json.loads(await asyncio.to_thread(path.read_text))
            except OSError:
                raise RouterError(404, "tokenizer_missing", "登録済みTokenizerがありません") from None
        return None

    async def preview(self, config):
        await self.validate_assets(config)
        dataset = None
        if config.options["data_kind"] in ("file", "trace"):
            dataset = await self.db.one(
                "SELECT * FROM benchmark_datasets WHERE id=?", (config.options["dataset_id"],)
            )
            if not dataset:
                raise RouterError(404, "dataset_missing", "試験用データがありません")
        pid = str(uuid.uuid4())
        spec = ENGINES[config.engine]["prepare"](
            config.model_dump(), "http://127.0.0.1/v1", config.alias, dataset=dataset
        )
        spec["preview_only"] = True
        await self.rpc("POST", f"/jobs/{pid}", json=spec)
        self.previews[pid] = None
        return {"id": pid}

    async def preview_status(self, pid):
        if pid not in self.previews:
            raise RouterError(404, "preview_missing", "事前確認がありません")
        value = self.previews[pid]
        if value is None:
            value = await self.rpc("GET", f"/jobs/{pid}")
            if value["status"] not in ("preparing", "running"):
                self.previews[pid] = value
                with contextlib.suppress(RouterError):
                    await self.rpc("DELETE", f"/jobs/{pid}")
        return value

    async def start(self, config: TrialInput, admin_id):
        health = await self.rpc("GET", "/healthz")
        if health.get("version") != ENGINES[config.engine]["version"]:
            raise RouterError(
                409, "runner_version_mismatch", "RouterとrunnerのGuideLLMバージョンが一致しません"
            )
        values = config.model_dump()
        values["tokenizer_asset"] = await self.validate_assets(config)
        dataset = None
        if config.options["data_kind"] in ("file", "trace"):
            dataset = await self.db.one(
                "SELECT * FROM benchmark_datasets WHERE id=?", (config.options["dataset_id"],)
            )
            if not dataset:
                raise RouterError(404, "dataset_missing", "試験用データがありません")
            if config.options["samples"] > dataset["rows"]:
                raise RouterError(422, "sample_count_exceeded", "サンプル数が登録データの行数を超えています")
            values["dataset"] = dataset
        async with self.service.registry_lock:
            alias = await self.db.one("SELECT * FROM aliases WHERE alias=? AND enabled=1", (config.alias,))
            if not alias:
                raise RouterError(404, "model_not_found", "モデルエイリアスがありません")
            endpoint = await self.db.one("SELECT * FROM endpoints WHERE id=?", (alias["endpoint_id"],))
            if (
                endpoint["archived"]
                or not endpoint["enabled"]
                or endpoint["health"] != "healthy"
                or endpoint["recovery_required"]
            ):
                raise RouterError(409, "endpoint_unavailable", "有効かつ正常な接続先を選んでください")
            if endpoint["id"] in self.service.maintenance:
                raise RouterError(409, "benchmark_busy", "この接続先では既に試験が動いています")
            if config.options["ignore_eos"] and endpoint["backend"] != "vllm":
                raise RouterError(422, "unsupported_option", "EOS無視は現在vLLM接続先に対応します")
            models = json.loads(endpoint["observed_models"])
            metadata = next((m for m in models if m.get("id") == alias["upstream_model"]), {})
            context = metadata.get("max_model_len")
            context = context if isinstance(context, int) and context > 0 else None
            values["context_length"] = context
            if (
                context
                and config.options["data_kind"] == "synthetic"
                and config.options["prompt_tokens"] + config.options["output_tokens"] > context
            ):
                raise RouterError(
                    422, "context_exceeded", "目標入力長と出力長の合計が接続先のコンテキスト長を超えています"
                )
            bid = str(uuid.uuid4())
            values.update(
                engine_version=ENGINES[config.engine]["version"],
                capacity_at_start=endpoint["capacity"],
                upstream_model=alias["upstream_model"],
                backend=endpoint["backend"],
            )
            await self.db.execute(
                "INSERT INTO benchmarks(id,endpoint_id,alias,admin_id,status,config,created_at) VALUES(?,?,?,?,?,?,?)",
                (bid, endpoint["id"], config.alias, admin_id, "draining", json.dumps(values), time.time()),
            )
            self.service.maintenance[endpoint["id"]] = bid
            self.service.scheduler.queue(endpoint["id"]).fail_waiting("benchmark_maintenance")
            self.stop_events[bid] = asyncio.Event()
            self.tasks[bid] = asyncio.create_task(self.run(bid, endpoint, values, dataset))
        return {"id": bid, "status": "draining"}

    def authenticate(self, token):
        info = self.tokens.get(token)
        if not info or self.stop_events[info["benchmark_id"]].is_set():
            return None
        return {"id": BENCHMARK_USER, **info}

    async def stop(self, bid):
        event = self.stop_events.get(bid)
        if event:
            event.set()
            await self.db.execute("UPDATE benchmarks SET status='stopping' WHERE id=?", (bid,))
        return {"id": bid, "stopping": bool(event)}

    async def ingest(self, bid, value):
        await self.db.execute(
            "INSERT OR REPLACE INTO benchmark_progress VALUES(?,?)",
            (bid, json.dumps(value.get("progress", {}))),
        )
        await self.db.execute(
            "UPDATE benchmarks SET current_step=?, completed_requests=? WHERE id=?",
            (value.get("current_step", 0), value.get("completed_requests", 0), bid),
        )
        if value.get("error_fields"):
            row = await self.db.one("SELECT config FROM benchmarks WHERE id=?", (bid,))
            config = json.loads(row["config"])
            config["error_fields"] = value["error_fields"]
            await self.db.execute("UPDATE benchmarks SET config=? WHERE id=?", (json.dumps(config), bid))
        for index, native in enumerate(value.get("stages", []), 1):
            row = await self.db.one("SELECT config FROM benchmarks WHERE id=?", (bid,))
            engine = ENGINES[json.loads(row["config"])["engine"]]
            results = engine["normalize"](native)
            sid = bid + ":" + str(index)
            strategy = native.get("strategy", {})
            # Persist the native strategy too; different profiles may not have a single scalar load.
            await self.db.execute(
                "INSERT OR REPLACE INTO benchmark_steps(id,benchmark_id,step,concurrency,arrival_rate,started_at,completed_at,status,results) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    bid,
                    index,
                    strategy.get("streams"),
                    strategy.get("rate"),
                    native["start_time"],
                    native["end_time"],
                    "failed" if results["failed"] else "completed",
                    json.dumps(results),
                ),
            )

    async def run(self, bid, endpoint, config, dataset):
        stop = self.stop_events[bid]
        status, error, launched, hold = "completed", None, False, False
        token = secrets.token_urlsafe(40)
        try:
            while any(j.endpoint["id"] == endpoint["id"] for j in self.service.jobs.values()):
                if stop.is_set():
                    status = "cancelled"
                    return
                await asyncio.sleep(0.1)
            if stop.is_set():
                status = "cancelled"
                return
            if config["mode"] == "router":
                self.tokens[token] = {"benchmark_id": bid, "alias": config["alias"]}
                target, model, key = self.settings.benchmark_router_url, config["alias"], token
            else:
                target, model = endpoint["base_url"], config["upstream_model"]
                key = self.service.secrets.decrypt(endpoint["credential"]) if endpoint["credential"] else None
            body = ENGINES[config["engine"]]["prepare"](config, target, model, key, dataset)
            body["engine"] = config["engine"]
            await self.db.execute(
                "UPDATE benchmarks SET status='preparing', started_at=? WHERE id=?", (time.time(), bid)
            )
            launched = True  # Even an ambiguous failed POST may have launched the child.
            await self.rpc("POST", f"/jobs/{bid}", json=body)
            while True:
                value = (
                    await self.rpc("POST", f"/jobs/{bid}/stop")
                    if stop.is_set()
                    else await self.rpc("GET", f"/jobs/{bid}")
                )
                await self.ingest(bid, value)
                current = value["status"]
                if current not in ("preparing", "running"):
                    status = "cancelled" if stop.is_set() else current
                    error = value.get("error_type")
                    hold = (
                        status in ("interrupted", "cancelled")
                        or (
                            status == "failed"
                            and bool(value.get("error_type"))
                            and value.get("current_step", 0) > 0
                        )
                        or any(
                            s["metrics"]["request_totals"]["incomplete"] > s.get("not_sent", 0)
                            for s in value.get("stages", [])
                        )
                    )
                    break
                await self.db.execute(
                    "UPDATE benchmarks SET status=? WHERE id=?",
                    ("stopping" if stop.is_set() else current, bid),
                )
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            status, error, hold = "interrupted", "router_shutdown", launched
        except Exception as exc:
            status, error, hold = (
                "failed",
                "runner_unavailable" if isinstance(exc, RouterError) else "benchmark_internal_error",
                launched,
            )
        finally:
            self.tokens.pop(token, None)
            if launched and status not in ("completed",):
                with contextlib.suppress(RouterError):
                    value = await self.rpc("POST", f"/jobs/{bid}/stop")
                    await self.ingest(bid, value)
            for job in list(self.service.jobs.values()):
                if job.benchmark_id == bid:
                    self.service.scheduler.cancel(job, "benchmark_stopped")
            if hold:
                await self.db.execute(
                    "UPDATE endpoints SET recovery_required=1 WHERE id=?", (endpoint["id"],)
                )
            await self.db.execute(
                "UPDATE benchmarks SET status=?,completed_at=?,error_type=? WHERE id=?",
                (status, time.time(), error, bid),
            )
            self.service.maintenance.pop(endpoint["id"], None)
            self.stop_events.pop(bid, None)
            self.tasks.pop(bid, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
