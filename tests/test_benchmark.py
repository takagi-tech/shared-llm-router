import asyncio
import json
from pathlib import Path

import pytest
from conftest import eventually

from router.engines import TrialInput, catalog
from router.guidellm_adapter import normalize_stage, scenario
from router.proxy import RouterError

RESULT = json.loads((Path(__file__).parent / "fixtures/guidellm-0.7.3.json").read_text())
CONFIG = {
    "alias": "model",
    "options": {
        "tokenizer": "gpt2",
        "values": [1, 2],
        "requests": 3,
        "samples": 5,
        "prompt_tokens": 32,
        "output_tokens": 3,
    },
}


class FakeRunner:
    def __init__(self):
        self.started = {}
        self.gate = None
        self.disconnected = False
        self.stopped = set()

    async def rpc(self, method, path, **kwargs):
        if self.disconnected:
            raise RouterError(503, "runner_unavailable", "runner unavailable")
        if path == "/healthz":
            return {"status": "ok", "version": "0.7.3"}
        bid = path.split("/")[2]
        if method == "POST" and path.endswith("/stop"):
            self.stopped.add(bid)
            return {"status": "cancelled", "stages": [], "completed_requests": 0}
        if method == "POST":
            self.started[bid] = kwargs["json"]
            return {"id": bid}
        if self.gate and not self.gate.is_set():
            return {"status": "running", "stages": [], "current_step": 1, "completed_requests": 0}
        return RESULT


@pytest.fixture
async def runner(env):
    fake = FakeRunner()
    env["app"].state.benchmarks.rpc = fake.rpc
    return fake


async def launch(env, config=None):
    response = await env["client"].post("/api/admin/benchmarks", json=config or CONFIG)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def result(env, bid):
    async def done():
        return bid not in env["app"].state.benchmarks.tasks

    await eventually(done, 5)
    return (await env["client"].get("/api/admin/benchmarks/" + bid)).json()


async def test_guidellm_results_privacy_and_capacity(env, runner):
    bid = await launch(env)
    record = await result(env, bid)
    assert record["status"] == "completed"
    assert record["config"]["engine_version"] == "0.7.3"
    assert len(record["steps"]) == 2
    assert record["steps"][0]["results"]["output_tokens"] == 9
    assert record["steps"][0]["results"]["input_tokens"] == 111
    assert record["steps"][0]["results"]["ttft_ms"]["p95"] > 0
    assert record["steps"][0]["results"]["e2e_ms"]["p95"] > 100
    assert "upstream-secret" not in json.dumps(record)
    spec = runner.started[bid]["scenario"]
    assert spec["backend"]["api_key"] == "upstream-secret"
    assert spec["backend"]["target"] == "http://upstream:8000"
    assert spec["backend"]["api_routes"]["/v1/chat/completions"] == "v1/chat/completions"
    assert spec["backend"]["timeout"] is None and spec["metrics"]["sample_size"] == 0
    assert spec["outputs"] == []
    ep = (await env["client"].get(f"/api/admin/endpoints/{env['eid']}")).json()
    assert ep["capacity"] == 1 and not ep["recovery_required"]
    assert not env["app"].state.service.maintenance
    assert (await env["client"].get("/api/admin/metrics")).json()["summary"]["total"] == 0


async def test_legacy_and_unsafe_configuration_rejected(env, runner):
    client = env["client"]
    for data in (
        {"alias": "model", "input_words": 10},
        {**CONFIG, "engine": "shell"},
        {**CONFIG, "options": {"tokenizer": "../../etc/passwd"}},
        {**CONFIG, "options": {"tokenizer": "gpt2", "extras": {"messages": []}}},
    ):
        assert (await client.post("/api/admin/benchmarks", json=data)).status_code == 422
    assert not runner.started
    engines = (await client.get("/api/admin/benchmark-engines")).json()
    assert engines[0]["form"] == "guidellm-v1" and "schema" in engines[0]


async def test_cancel_before_launch_does_not_contact_runner_or_hold(env, runner):
    gate = asyncio.Event()
    env["upstream"].gate = gate
    service = env["app"].state.service
    job = await service.submit(
        env["uid"], {"model": "model", "messages": [{"role": "user", "content": "hello"}]}
    )
    bid = await launch(env)
    await env["client"].post(f"/api/admin/benchmarks/{bid}/stop")
    record = await result(env, bid)
    assert record["status"] == "cancelled" and bid not in runner.started
    assert not (
        await env["app"].state.db.one("SELECT recovery_required FROM endpoints WHERE id=?", (env["eid"],))
    )["recovery_required"]
    service.scheduler.cancel(job, "test_end")
    await service.finalize(job)


async def test_router_scope_capacity_and_revocation(env, runner):
    runner.gate = asyncio.Event()
    env["upstream"].gate = asyncio.Event()
    bid = await launch(env, {**CONFIG, "mode": "router"})

    async def started():
        return bid in runner.started

    await eventually(started)
    token = runner.started[bid]["scenario"]["backend"]["api_key"]
    client = env["client"]
    normal = {"model": "model", "messages": [{"role": "user", "content": "x"}], "stream": True}
    assert (await client.post("/v1/chat/completions", json=normal)).status_code == 503
    assert (
        await client.post(
            "/v1/chat/completions",
            json={**normal, "model": "other"},
            headers={"Authorization": "Bearer " + token},
        )
    ).status_code == 403
    requests = [
        asyncio.create_task(
            client.post("/v1/chat/completions", json=normal, headers={"Authorization": "Bearer " + token})
        )
        for _ in range(2)
    ]

    async def queued():
        return len(env["app"].state.service.jobs) == 2

    await eventually(queued)
    assert env["upstream"].peak == 1
    assert (
        await client.put(f"/api/admin/endpoints/{env['eid']}/capacity", json={"capacity": 3})
    ).status_code == 409
    env["upstream"].gate.set()
    responses = await asyncio.gather(*requests)
    assert all(r.status_code == 200 for r in responses)
    assert env["upstream"].peak == 1
    await client.post(f"/api/admin/benchmarks/{bid}/stop")
    record = await result(env, bid)
    assert record["status"] == "cancelled" and bid in runner.stopped
    assert (await client.get("/v1/models", headers={"Authorization": "Bearer " + token})).status_code == 401
    rows = await env["app"].state.db.all("SELECT source,benchmark_id FROM requests")
    assert all(r["source"] == "benchmark" and r["benchmark_id"] == bid for r in rows)
    assert (
        await env["app"].state.db.one("SELECT recovery_required FROM endpoints WHERE id=?", (env["eid"],))
    )["recovery_required"]


async def test_runner_failure_holds_endpoint(env, runner):
    runner.gate = asyncio.Event()
    bid = await launch(env)

    async def started():
        return bid in runner.started

    await eventually(started)
    runner.disconnected = True
    record = await result(env, bid)
    assert record["status"] == "failed" and record["error_type"] == "runner_unavailable"
    assert (
        await env["app"].state.db.one("SELECT recovery_required FROM endpoints WHERE id=?", (env["eid"],))
    )["recovery_required"]


async def test_dataset_upload_delete_and_exports(env, runner):
    client = env["client"]
    private = "private dataset phrase"
    upload = await client.post(
        "/api/admin/benchmark-datasets?name=data.jsonl",
        content=json.dumps({"prompt": private}),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201, upload.text
    did = upload.json()["id"]
    rows = (await client.get("/api/admin/benchmark-datasets")).json()
    assert private not in json.dumps(rows) and rows[0]["rows"] == 1
    runner.gate = asyncio.Event()
    bid = await launch(
        env,
        {
            **CONFIG,
            "notes": "<script>alert(1)</script>",
            "options": {**CONFIG["options"], "data_kind": "file", "dataset_id": did, "samples": 1},
        },
    )
    assert (await client.delete(f"/api/admin/benchmark-datasets/{did}")).status_code == 409
    runner.gate.set()
    record = await result(env, bid)
    assert private not in json.dumps(record)
    for fmt in ("json", "csv", "html"):
        response = await client.get(f"/api/admin/benchmarks/{bid}/export/{fmt}")
        assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        assert private not in response.text and "upstream-secret" not in response.text
        if fmt == "html":
            assert (
                "<script>" not in response.text and "sandbox" in response.headers["content-security-policy"]
            )
    assert (await client.delete(f"/api/admin/benchmark-datasets/{did}")).status_code == 200
    assert (await client.get(f"/api/admin/benchmarks/{bid}")).status_code == 200


def test_versioned_adapter_and_no_default_duration_deadline():
    c = TrialInput(
        **{
            **CONFIG,
            "options": {
                **CONFIG["options"],
                "stop_kind": "duration",
                "duration_seconds": 2,
                "ignore_eos": True,
            },
        }
    )
    prepared = scenario(c.model_dump(), "http://host/base/v1", "model")
    assert prepared["duration_seconds"] == 2
    assert all(k["kind"] != "max_duration" for k in prepared["scenario"]["constraints"])
    assert prepared["scenario"]["backend"]["extras"]["body"]["ignore_eos"] is True
    assert prepared["scenario"]["backend"]["api_routes"]["/v1/chat/completions"] == "base/v1/chat/completions"
    assert catalog()[0]["schema_version"] == 1


def test_partial_missing_metrics_are_not_presented_as_zero():
    native = json.loads(json.dumps(RESULT["stages"][0]))
    native["coverage"] = {"time_to_first_token_ms": 0}
    normalized = normalize_stage(native)
    assert normalized["ttft_ms"]["p95"] is None
    assert normalized["native"]["metrics"] == native["metrics"]


async def test_tokenizer_zip_is_local_and_scoped(env):
    import io
    import zipfile

    client = env["client"]
    for filename, expected in (("../tokenizer.json", 422), ("tokenizer.json", 201)):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(filename, "{}")
        response = await client.post(
            "/api/admin/benchmark-tokenizers?name=fixture.zip",
            content=archive.getvalue(),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == expected
    tokenizers = (await client.get("/api/admin/benchmark-tokenizers")).json()
    assert len(tokenizers) == 1
    tid = tokenizers[0]["id"]
    config = TrialInput(
        **{**CONFIG, "options": {**CONFIG["options"], "tokenizer_source": "local", "tokenizer": tid}}
    )
    assert (
        scenario(config.model_dump(), "http://host/v1", "model")["scenario"]["tokenizer"]["model"]
        == "/tokenizers/" + tid
    )
