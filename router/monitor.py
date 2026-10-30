import asyncio
import contextlib
import json
import logging
import math
import re
import time

import httpx

log = logging.getLogger(__name__)

# Only named numeric gauges are collected. No request labels, prompts or tokens.
GAUGES = {
    "llamacpp:requests_processing",
    "llamacpp:requests_deferred",
    "llamacpp:prompt_tokens_seconds",
    "llamacpp:predicted_tokens_seconds",
    "llamacpp:n_busy_slots_per_decode",
    "node_load1",
    "llm_router_cpu_count",
    "llm_router_gpu_scrape_success",
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:gpu_cache_usage_perc",
    "vllm:kv_cache_usage_perc",
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_POWER_USAGE",
    "node_memory_MemTotal_bytes",
    "node_memory_MemAvailable_bytes",
}


def model_info(model):
    result = {k: model[k] for k in ("id", "owned_by", "max_model_len") if k in model}
    # llama.cpp exposes the configured context (not the trained maximum) in meta.n_ctx.
    meta = model.get("meta")
    if "max_model_len" not in result and isinstance(meta, dict):
        context = meta.get("n_ctx")
        if isinstance(context, int) and context > 0:
            result["max_model_len"] = context
    return result


def parse_gauges(text: str):
    result = {}
    for line in text.splitlines():
        match = re.match(r"^([\w:]+)(\{.*?\})?\s+([^\s]+)", line)
        if not match or match[1] not in GAUGES:
            continue
        try:
            value = float(match[3])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        labels = dict(re.findall(r'(\w+)="([^"\\]*)"', match[2] or ""))
        # Retain only identifiers needed to distinguish devices or model gauges.
        labels = {k: v for k, v in labels.items() if k in ("gpu", "UUID", "device", "model_name")}
        result.setdefault(match[1], []).append({"labels": labels, "value": value})
    return result


LOAD_GAUGES = {
    "running": ("vllm:num_requests_running", "llamacpp:requests_processing"),
    "queued": ("vllm:num_requests_waiting", "llamacpp:requests_deferred"),
}


def metrics_url_for(ep):
    # vLLM serves /metrics next to /v1 by default; llama.cpp only with --metrics.
    return ep["metrics_url"] or ep["base_url"].removesuffix("/v1") + "/metrics"


def upstream_load(ep):
    """Requests the inference server itself reports, including ones not sent via the Router."""
    native = (
        json.loads(ep["native_metrics"]) if isinstance(ep["native_metrics"], str) else ep["native_metrics"]
    )
    gauges = native.get("gauges") or {}
    # Disabled endpoints are no longer probed, so their last sample would be stale.
    if not ep["enabled"] or ep["health"] != "healthy" or native.get("error"):
        return {"running": None, "queued": None}
    result = {}
    for key, names in LOAD_GAUGES.items():
        values = [g["value"] for name in names for g in gauges.get(name, [])]
        result[key] = int(sum(values)) if values else None
    return result


class Monitor:
    def __init__(self, db, service, secrets, transport=None):
        self.db, self.service, self.secrets = db, service, secrets
        # Monitoring has its own bounded network calls, independent of inference.
        self.client = httpx.AsyncClient(
            timeout=5, trust_env=False, follow_redirects=False, transport=transport
        )
        self.task = None
        self.locks: dict[str, asyncio.Lock] = {}

    async def probe(self, endpoint_id: str, observed=None):
        """Check one endpoint and store the result.

        observed=(models, error) records a result the caller already fetched (the port scan),
        so the same /v1/models request is not sent twice.
        """
        async with self.locks.setdefault(endpoint_id, asyncio.Lock()):
            ep = await self.db.one("SELECT * FROM endpoints WHERE id=? AND archived=0", (endpoint_id,))
            if not ep:
                return None
            started = time.monotonic()
            headers = {}
            key = self.secrets.decrypt(ep["credential"])
            if key:
                headers["Authorization"] = "Bearer " + key
            models, error = observed or ([], None)
            if observed is None:
                try:
                    response = await self.client.get(ep["base_url"] + "/models", headers=headers)
                    if response.status_code != 200:
                        error = f"http_{response.status_code}"
                    else:
                        data = response.json()
                        if not isinstance(data.get("data"), list):
                            error = "invalid_models_response"
                        else:
                            models = [
                                model_info(m)
                                for m in data["data"]
                                if isinstance(m, dict) and isinstance(m.get("id"), str)
                            ]
                except (httpx.HTTPError, ValueError, AttributeError):
                    error = "connection_error"
            if not error and await self.alias_missing(endpoint_id, models):
                error = "registered_model_missing"
            settings = await self.db.settings()
            failed = ep["consecutive_failures"] + 1 if error else 0
            succeeded = 0 if error else ep["consecutive_successes"] + 1
            health = ep["health"]
            if error and failed >= settings["health_failure_threshold"]:
                health = "unhealthy"
            elif not error and (health == "unknown" or succeeded >= settings["health_recovery_threshold"]):
                health = "healthy"
            now = time.time()
            # Serialize with administrative registry changes and request admission.
            async with self.service.registry_lock:
                current = await self.db.one(
                    "SELECT base_url, credential FROM endpoints WHERE id=?", (endpoint_id,)
                )
                if current["base_url"] != ep["base_url"] or current["credential"] != ep["credential"]:
                    return {"id": endpoint_id, "discarded": True}
                await self.db.execute(
                    "UPDATE endpoints SET health=?, consecutive_failures=?, consecutive_successes=?, "
                    "last_checked_at=?, last_error=?, observed_models=? WHERE id=?",
                    (health, failed, succeeded, now, error, json.dumps(models), endpoint_id),
                )
                if health == "unhealthy":
                    self.service.scheduler.queue(endpoint_id, ep["capacity"]).fail_waiting(
                        "endpoint_unavailable"
                    )
            await self.db.execute(
                "INSERT INTO health_samples(endpoint_id,observed_at,healthy,latency_ms,error_type) VALUES (?,?,?,?,?)",
                (endpoint_id, now, not error, (time.monotonic() - started) * 1000, error),
            )
            if not error:
                # Derived URLs refresh the live load only; explicit ones also keep history.
                await self.collect(
                    "endpoint", endpoint_id, metrics_url_for(ep), headers, history=bool(ep["metrics_url"])
                )
            return {
                "id": endpoint_id,
                "health": health,
                "probe_ok": not error,
                "error_type": error,
                "observed_models": models,
                "checked_at": now,
            }

    async def alias_missing(self, endpoint_id, models):
        aliases = await self.db.all(
            "SELECT upstream_model FROM aliases WHERE endpoint_id=? AND enabled=1", (endpoint_id,)
        )
        names = {m["id"] for m in models}
        return any(a["upstream_model"] not in names for a in aliases)

    async def collect_servers(self):
        servers = await self.db.all("SELECT id, metrics_url FROM servers WHERE metrics_url IS NOT NULL")
        await asyncio.gather(*(self.collect("server", s["id"], s["metrics_url"]) for s in servers))

    async def collect(self, kind, target_id, url, headers=None, history=True):
        try:
            response = await self.client.get(url, headers=headers or {})
            response.raise_for_status()
            data = {"gauges": parse_gauges(response.text), "observed_at": time.time(), "error": None}
        except (httpx.HTTPError, ValueError):
            data = {"gauges": {}, "observed_at": time.time(), "error": "metrics_unavailable"}
        if kind == "endpoint":
            await self.db.execute(
                "UPDATE endpoints SET native_metrics=? WHERE id=?", (json.dumps(data), target_id)
            )
        else:
            await self.db.execute(
                "UPDATE servers SET hardware_metrics=?, metrics_checked_at=? WHERE id=?",
                (json.dumps(data), data["observed_at"], target_id),
            )
        if history:
            await self.db.execute(
                "INSERT INTO resource_samples(kind,target_id,observed_at,data) VALUES (?,?,?,?)",
                (kind, target_id, data["observed_at"], json.dumps(data)),
            )

    async def cycle(self):
        endpoints = await self.db.all("SELECT id FROM endpoints WHERE archived=0 AND enabled=1")
        await asyncio.gather(*(self.probe(ep["id"]) for ep in endpoints))
        await self.collect_servers()

    async def loop(self):
        last_retention = 0
        while True:
            try:
                settings = await self.db.settings()
                if settings["health_interval_seconds"]:
                    await self.cycle()
                if time.monotonic() - last_retention > 3600:
                    await self.service.metrics.retain()
                    await self.db.execute(
                        "DELETE FROM resource_samples WHERE observed_at<?",
                        (time.time() - settings["metric_retention_days"] * 86400,),
                    )
                    last_retention = time.monotonic()
                await asyncio.sleep(settings["health_interval_seconds"] or 10)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error("Monitoring cycle failed")
                await asyncio.sleep(10)

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.client.aclose()
