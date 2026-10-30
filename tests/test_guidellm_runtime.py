"""Opt-in tests against real GuideLLM and process supervision (CPU, fake inference)."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from router.engines import TrialInput
from router.guidellm_adapter import scenario

PYTHON = os.environ.get("LLM_ROUTER_GUIDELLM_PYTHON")
ROOT = Path(__file__).resolve().parent.parent
PYTHON_BIN = str(Path(PYTHON or "/unused").absolute())
pytestmark = pytest.mark.skipif(not PYTHON, reason="set LLM_ROUTER_GUIDELLM_PYTHON to the runner Python")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_runner(tmp_path_factory):
    root = ROOT
    directory = tmp_path_factory.mktemp("guidellm-runtime")
    key = directory / "runner.key"
    key.write_text("private-control-key")
    runner_port, mock_port = port(), port()
    env = {
        **os.environ,
        "LLM_ROUTER_RUNNER_KEY_FILE": str(key),
        "LLM_ROUTER_RUNNER_DATA": str(directory / "runs"),
        "LLM_ROUTER_RUNNER_LEASE_SECONDS": "4",
        "HF_HOME": str(root / ".local/guidellm/cache/huggingface"),
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "2",
    }
    processes = [
        await asyncio.to_thread(
            subprocess.Popen,
            [
                PYTHON_BIN,
                "-m",
                "uvicorn",
                "router.runner:app",
                "--port",
                str(runner_port),
                "--no-access-log",
            ],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ),
        await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "guidellm_mock:app",
                "--app-dir",
                str(root / "tests"),
                "--port",
                str(mock_port),
                "--no-access-log",
            ],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ),
    ]
    async with (
        httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{runner_port}",
            headers={"Authorization": "Bearer private-control-key"},
            trust_env=False,
            timeout=15,
        ) as runner,
        httpx.AsyncClient(base_url=f"http://127.0.0.1:{mock_port}", trust_env=False, timeout=15) as mock,
    ):
        try:
            for client, path in ((runner, "/healthz"), (mock, "/v1/models")):
                async with asyncio.timeout(15):
                    while True:
                        try:
                            if (await client.get(path)).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
            yield runner, mock, directory
        finally:
            for p in processes:
                p.terminate()
            for p in processes:
                try:
                    await asyncio.to_thread(p.wait, 15)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    await asyncio.to_thread(p.wait)


async def start(live_runner, options, preview=False):
    runner, mock, _ = live_runner
    c = TrialInput(
        alias="mock-model",
        options={
            "tokenizer": "gpt2",
            "prompt_tokens": 32,
            "output_tokens": 3,
            "samples": 4,
            "requests": 4,
            "values": [1, 2],
            "max_concurrency": 2,
            **options,
        },
    )
    spec = scenario(
        c.model_dump(), str(mock.base_url).rstrip("/") + "/v1", "mock-model", "PRIVATE_UPSTREAM_KEY"
    )
    spec["preview_only"] = preview
    bid = str(uuid.uuid4())
    response = await runner.post("/jobs/" + bid, json=spec)
    assert response.status_code == 200, response.text
    return bid


async def wait(live_runner, bid, predicate=lambda r: r["status"] not in ("preparing", "running")):
    runner, _, _ = live_runner
    async with asyncio.timeout(90):
        while True:
            r = (await runner.get("/jobs/" + bid)).json()
            if predicate(r):
                return r
            await asyncio.sleep(0.15)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "profile", ["synchronous", "concurrent", "constant", "poisson", "throughput", "sweep"]
)
async def test_real_profiles(live_runner, profile):
    runner, mock, directory = live_runner
    await mock.post("/control", json={})
    bid = await start(
        live_runner, {"profile": profile, "values": [2] if profile == "concurrent" else [20], "sweep_size": 3}
    )
    result = await wait(live_runner, bid)
    assert result["status"] == "completed", result.get("error_type")
    assert result["stages"]
    for stage in result["stages"]:
        assert stage["metrics"]["request_totals"]["successful"] == 4
        assert stage["metrics"]["prompt_token_count"]["successful"]["mean"] == 37
        assert stage["metrics"]["output_token_count"]["successful"]["mean"] == 3
    text = (directory / "runs" / (bid + ".json")).read_text()
    assert (
        "PRIVATE_UPSTREAM_KEY" not in text
        and "PRIVATE_RESPONSE_BODY" not in text
        and '"messages"' not in text
    )
    assert (await mock.get("/state")).json()["peak"] <= 2


@pytest.mark.asyncio(loop_scope="module")
async def test_preview_counts_without_inference(live_runner):
    _, mock, _ = live_runner
    await mock.post("/control", json={})
    bid = await start(live_runner, {}, preview=True)
    result = await wait(live_runner, bid)
    assert result["status"] == "completed", result.get("error_type")
    assert result["preview"] == [{"input_tokens": 32, "output_tokens": 3}] * 3
    assert not (await mock.get("/state")).json()["requests"]


@pytest.mark.asyncio(loop_scope="module")
async def test_duration_drains_response_beyond_five_seconds(live_runner):
    _, mock, _ = live_runner
    await mock.post("/control", json={"delay": 6})
    bid = await start(
        live_runner,
        {"profile": "concurrent", "values": [2], "stop_kind": "duration", "duration_seconds": 0.2},
    )
    result = await wait(live_runner, bid)
    assert result["status"] == "completed", result
    assert result["stages"][0]["metrics"]["request_totals"]["successful"] == 2
    assert result["stages"][0]["duration"] >= 6
    state = (await mock.get("/state")).json()
    assert len(state["requests"]) == 2 and state["active"] == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_errors_stop_next_stage_and_hide_body(live_runner):
    _, mock, directory = live_runner
    await mock.post("/control", json={"status": 500})
    bid = await start(live_runner, {"profile": "concurrent", "values": [1, 2]})
    result = await wait(live_runner, bid)
    assert result["status"] == "failed" and len(result["stages"]) == 1
    assert result["stages"][0]["error_categories"].get("http_500", 0) >= 1
    assert "PRIVATE_RESPONSE_BODY" not in (directory / "runs" / (bid + ".json")).read_text()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("lease", [False, True])
async def test_stop_and_router_lease_loss_close_stream(live_runner, lease):
    runner, mock, _ = live_runner
    await mock.post("/control", json={"delay": 20})
    bid = await start(live_runner, {"profile": "synchronous"})
    async with asyncio.timeout(45):
        while not (await mock.get("/state")).json()["active"]:
            await runner.get("/jobs/" + bid)
            await asyncio.sleep(0.2)
    if lease:
        await asyncio.sleep(7)
        result = (await runner.get("/jobs/" + bid)).json()
    else:
        result = (await runner.post(f"/jobs/{bid}/stop")).json()
    assert result["status"] in ("cancelled", "interrupted"), result
    async with asyncio.timeout(5):
        while (await mock.get("/state")).json()["active"]:
            await asyncio.sleep(0.1)
    assert (await runner.get(f"/jobs/{bid}", headers={"Authorization": "Bearer wrong"})).status_code == 401


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("trace", [False, True])
async def test_dataset_columns_and_trace_replay(live_runner, trace):
    runner, mock, directory = live_runner
    await mock.post("/control", json={})
    dataset = directory / ("trace.jsonl" if trace else "data.jsonl")
    rows = (
        [
            {"timestamp": 0, "input_length": 16, "output_length": 4},
            {"timestamp": 0.2, "input_length": 32, "output_length": 8},
        ]
        if trace
        else [
            {"user_text": "First private fixture sentence.", "desired": 7},
            {"user_text": "Second private fixture sentence.", "desired": 9},
        ]
    )
    await asyncio.to_thread(dataset.write_text, "\n".join(json.dumps(r) for r in rows))
    config = TrialInput(
        alias="mock-model",
        options={
            "tokenizer": "gpt2",
            "data_kind": "trace" if trace else "file",
            "dataset_id": "fixture",
            "samples": 2,
            "requests": 2,
            "profile": "replay" if trace else "synchronous",
            "prompt_column": "user_text",
            "output_column": "desired",
        },
    )
    spec = scenario(
        config.model_dump(),
        str(mock.base_url).rstrip("/") + "/v1",
        "mock-model",
        dataset={"id": "fixture", "extension": ".jsonl"},
    )
    spec["scenario"]["data"][0]["path"] = str(dataset)
    bid = str(uuid.uuid4())
    assert (await runner.post("/jobs/" + bid, json=spec)).status_code == 200
    result = await wait(live_runner, bid)
    assert result["status"] == "completed", result.get("error_type")
    requests = (await mock.get("/state")).json()["requests"]
    assert sorted(r["max_tokens"] for r in requests) == ([4, 8] if trace else [7, 9])
    if trace:
        assert requests[1]["time"] - requests[0]["time"] >= 0.15
    assert "private fixture sentence" not in (directory / "runs" / (bid + ".json")).read_text()


@pytest.mark.asyncio(loop_scope="module")
async def test_absent_usage_is_missing_not_requested_length(live_runner):
    from router.guidellm_adapter import normalize_stage

    _, mock, _ = live_runner
    await mock.post("/control", json={"usage": False})
    bid = await start(live_runner, {"profile": "synchronous", "requests": 2})
    result = await wait(live_runner, bid)
    assert result["status"] == "completed"
    stage = normalize_stage(result["stages"][0])
    assert stage["completed"] == 2
    assert stage["output_tokens"] is None
    assert stage["input_tokens"] is None
    assert stage["output_token_throughput"] is None
    assert stage["requested_output_mean"] > 0


@pytest.mark.asyncio(loop_scope="module")
async def test_worker_crash_cleans_process_group(live_runner):
    _, mock, directory = live_runner
    await mock.post("/control", json={"delay": 10})
    bid = await start(live_runner, {"profile": "synchronous", "requests": 2})
    await wait(live_runner, bid, lambda r: r["status"] == "running")
    expected = str(directory / "runs" / (bid + ".json")).encode()

    def find_worker():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / "cmdline").read_bytes().split(b"\0")
                if b"router.guidellm_worker" in args and expected in args:
                    return int(entry.name)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
        raise AssertionError("worker process not found")

    pid = await asyncio.to_thread(find_worker)
    os.kill(pid, signal.SIGKILL)
    result = await wait(live_runner, bid)
    assert result["status"] == "interrupted"
    assert result["error_type"] == "worker_exited"
    async with asyncio.timeout(10):
        while True:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
