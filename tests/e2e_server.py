"""Isolated UI integration server. Never contacts a real GPU service."""

import asyncio
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from conftest import FakeUpstream
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from router.app import create_app
from router.config import Settings
from router.security import hash_password

SHARED_DATASETS = Path(__file__).resolve().parent.parent / ".local/data/datasets"

temp = tempfile.TemporaryDirectory(prefix="router-ui-test-")
fake = FakeUpstream()
app = create_app(
    Settings(
        data_dir=Path(temp.name),
        testing=True,
        benchmark_router_url="http://127.0.0.1:18081/v1",
        runner_key_file=Path(__file__).resolve().parent.parent / ".local/runner.key",
    ),
    httpx.MockTransport(fake.handle),
)

# External GuideLLM uses this local mock HTTP endpoint; normal proxy calls still
# use the in-process fake transport. This fixture never contacts a real GPU.
spa_route = app.router.routes.pop()


@app.get("/fixture/v1/models")
async def mock_models():
    return {"data": [{"id": "upstream-model", "max_model_len": 131072}]}


@app.post("/fixture/v1/chat/completions")
async def mock_completion(request: Request):
    response = await fake.handle(
        httpx.Request("POST", "http://mock/v1/chat/completions", content=await request.body())
    )
    if response.headers.get("content-type") == "text/event-stream":

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()

        return StreamingResponse(chunks(), media_type="text/event-stream", status_code=response.status_code)
    return JSONResponse(response.json(), status_code=response.status_code)


@app.post("/fixture/control")
async def control(request: Request):
    config = await request.json()
    fake.delay = config.get("delay", 0)
    fake.completion_status = config.get("status", 200)
    return {"requests": len(fake.requests), "running": fake.running, "peak": fake.peak}


app.router.routes.append(spa_route)

original = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    async with original(application):
        await application.state.db.execute(
            "INSERT INTO admins VALUES(?,?,?,?)",
            (str(uuid.uuid4()), "admin", hash_password("test-password-123"), time.time()),
        )
        # The Docker runner sees /datasets, while this app uses a temporary DB.
        # Mirror only this fixture's uploaded UUID files into its read-only mount.
        manager = application.state.benchmarks
        rpc = manager.rpc
        mirrored = set()
        shared = SHARED_DATASETS

        async def fixture_rpc(method, path, **kwargs):
            if method == "POST" and path.startswith("/jobs/"):
                for data in kwargs.get("json", {}).get("scenario", {}).get("data", []):
                    name = Path(data.get("path", "")).name
                    source = Path(temp.name) / "datasets" / name
                    if name and await asyncio.to_thread(source.is_file):
                        destination = shared / name
                        if destination not in mirrored:
                            if await asyncio.to_thread(destination.exists):
                                raise RuntimeError("fixture file collision")
                            await asyncio.to_thread(shutil.copyfile, source, destination)
                            mirrored.add(destination)
            return await rpc(method, path, **kwargs)

        manager.rpc = fixture_rpc
        try:
            yield
        finally:
            for destination in mirrored:
                await asyncio.to_thread(destination.unlink, missing_ok=True)


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18081, access_log=False)
