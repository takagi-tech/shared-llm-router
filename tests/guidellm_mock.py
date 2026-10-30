"""HTTP fixture for the real GuideLLM runner. No model/GPU dependency."""

import asyncio
import hashlib
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
state = {"delay": 0.08, "status": 200, "requests": [], "active": 0, "peak": 0}


@app.get("/v1/models")
async def models():
    return {"data": [{"id": "mock-model"}]}


@app.post("/control")
async def control(request: Request):
    config = await request.json()
    state.update(
        delay=config.get("delay", 0.08),
        status=config.get("status", 200),
        usage=config.get("usage", True),
        requests=[],
        peak=0,
    )
    return {"ok": True}


@app.get("/state")
async def current():
    return state


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    state["requests"].append(
        {
            "time": time.time(),
            "max_tokens": body.get("max_tokens", body.get("max_completion_tokens")),
            "ignore_eos": body.get("ignore_eos"),
            "prompt_digest": hashlib.sha256(json.dumps(body.get("messages")).encode()).hexdigest(),
        }
    )
    if state["status"] != 200:
        return JSONResponse({"error": {"message": "PRIVATE_RESPONSE_BODY"}}, status_code=state["status"])

    async def events():
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(state["delay"])
            for word in ("one", " two", " three"):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "mock-chat",
                            "object": "chat.completion.chunk",
                            "model": "mock-model",
                            "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
                        }
                    )
                    + "\n\n"
                )
                await asyncio.sleep(0.01)
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "mock-chat",
                        "object": "chat.completion.chunk",
                        "model": "mock-model",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        **(
                            {"usage": {"prompt_tokens": 37, "completion_tokens": 3, "total_tokens": 40}}
                            if state.get("usage", True)
                            else {}
                        ),
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"
        finally:
            state["active"] -= 1

    return StreamingResponse(events(), media_type="text/event-stream")
