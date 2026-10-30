"""Authenticated process supervisor. No Docker socket and no inference payload logs."""

import asyncio
import contextlib
import json
import os
import secrets
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request

ROOT = Path(os.environ.get("LLM_ROUTER_RUNNER_DATA", "/runner-data"))
KEY = Path(os.environ.get("LLM_ROUTER_RUNNER_KEY_FILE", "/run/runner.key"))
LEASE_SECONDS = float(os.environ.get("LLM_ROUTER_RUNNER_LEASE_SECONDS", "30"))
WORKERS = {"guidellm": "router.guidellm_worker"}
processes = {}
leases = {}
watchers = set()


def job_path(bid):
    try:
        bid = str(uuid.UUID(bid))
    except ValueError:
        raise HTTPException(400, "invalid job id") from None
    return ROOT / (bid + ".json")


async def authorize(request: Request):
    token = await asyncio.to_thread(KEY.read_text)
    if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token.strip()):
        raise HTTPException(401, "runner authentication required")


async def stop_job(bid):
    process = processes.get(bid)
    if process and process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        # A child may outlive a gracefully exited parent.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    return await read_job(bid)


async def read_job(bid):
    path = job_path(bid)
    if not await asyncio.to_thread(path.exists):
        raise HTTPException(404, "job not found")
    value = json.loads(await asyncio.to_thread(path.read_text))
    process = processes.get(bid)
    if value["status"] in ("preparing", "running") and (not process or process.returncode is not None):
        value["status"] = "interrupted"
        value["error_type"] = "worker_exited"
    return value


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(ROOT.mkdir, parents=True, exist_ok=True)

    async def reap():
        while True:
            await asyncio.sleep(2)
            for bid in list(processes):
                if time.monotonic() - leases.get(bid, 0) > LEASE_SECONDS:
                    await stop_job(bid)
                    processes.pop(bid, None)
                    leases.pop(bid, None)

    reaper = asyncio.create_task(reap())
    try:
        yield
    finally:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
        await asyncio.gather(*(stop_job(bid) for bid in list(processes)))
        await asyncio.gather(*list(watchers), return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def health():
    return {"status": "ok", "engine": "guidellm", "version": "0.7.3"}


@app.post("/jobs/{bid}", dependencies=[Depends(authorize)])
async def start(bid: str, request: Request):
    path = job_path(bid)
    if bid in processes or path.exists():
        raise HTTPException(409, "job already exists")
    body = await request.json()
    worker = WORKERS.get(body.get("engine", "guidellm"))
    if not worker:
        raise HTTPException(422, "unsupported engine")
    from .guidellm_worker import atomic_json

    await asyncio.to_thread(
        atomic_json, path, {"status": "preparing", "stages": [], "current_step": 0, "completed_requests": 0}
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        worker,
        str(path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

    async def cleanup_group():
        await process.wait()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    watcher = asyncio.create_task(cleanup_group())
    watchers.add(watcher)
    watcher.add_done_callback(watchers.discard)
    processes[bid] = process
    leases[bid] = time.monotonic()
    process.stdin.write(json.dumps(body).encode() + b"\n")
    await process.stdin.drain()
    process.stdin.close()
    return {"id": bid}


@app.get("/jobs/{bid}", dependencies=[Depends(authorize)])
async def get_job(bid: str):
    leases[bid] = time.monotonic()
    return await read_job(bid)


@app.post("/jobs/{bid}/stop", dependencies=[Depends(authorize)])
async def stop(bid: str):
    return await stop_job(bid)


@app.delete("/jobs/{bid}", dependencies=[Depends(authorize)])
async def delete(bid: str):
    await stop_job(bid)
    processes.pop(bid, None)
    leases.pop(bid, None)
    await asyncio.to_thread(job_path(bid).unlink, missing_ok=True)
    return {"ok": True}
