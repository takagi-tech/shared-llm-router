import asyncio
import json
import time
import uuid

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from router.app import create_app
from router.config import Settings
from router.security import hash_password


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None, upstream=None):
        self.chunks, self.gate, self.upstream = chunks, gate, upstream
        self.closed = False

    async def __aiter__(self):
        if self.gate:
            await self.gate.wait()
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk

    async def aclose(self):
        if not self.closed and self.upstream:
            self.upstream.running -= 1
        self.closed = True


class FakeUpstream:
    def __init__(self):
        self.requests = []
        self.gate = None
        self.running = 0
        self.peak = 0
        self.streams = []
        self.health_status = 200
        self.completion_status = 200
        self.truncated = False
        self.delay = 0

    async def handle(self, request):
        if request.url.path == "/metrics":
            return httpx.Response(
                200,
                text='vllm:num_requests_running{model_name="upstream-model"} 2\n'
                'vllm:num_requests_waiting{model_name="upstream-model"} 5\n',
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(
                self.health_status, json={"data": [{"id": "upstream-model", "max_model_len": 131072}]}
            )
        payload = json.loads(request.content)
        self.requests.append((request, payload))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.completion_status != 200:
            return httpx.Response(self.completion_status, json={"error": {"message": "upstream error"}})
        if payload.get("stream"):
            if payload.get("tools"):
                parts = [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "report", "arguments": ""},
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [{"index": 0, "function": {"arguments": '{"summary":'}}]
                                },
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [{"index": 0, "function": {"arguments": '"日本語"}'}}]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                ]
            else:
                parts = [
                    {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
                    {"choices": [{"delta": {"content": "こんにちは"}}]},
                    {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
                ]
            parts.append({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
            raw = b"".join(
                b"data: " + json.dumps(p, ensure_ascii=False).encode() + b"\r\n\r\n" for p in parts
            )
            if not self.truncated:
                raw += b"data: [DONE]\r\n\r\n"
            chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]
            self.running += 1
            self.peak = max(self.peak, self.running)
            stream = Stream(chunks, self.gate, self)
            self.streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        if self.gate:
            await self.gate.wait()
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )


@pytest_asyncio.fixture
async def env(tmp_path):
    upstream = FakeUpstream()
    app = create_app(
        Settings(data_dir=tmp_path / "data", testing=True, open_access=False),
        httpx.MockTransport(upstream.handle),
    )
    async with LifespanManager(app):
        await app.state.db.execute(
            "INSERT INTO admins VALUES(?,?,?,?)",
            (str(uuid.uuid4()), "admin", hash_password("test-password-123"), time.time()),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            login = await client.post(
                "/api/auth/login", json={"username": "admin", "password": "test-password-123"}
            )
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            server = await client.post("/api/admin/servers", json={"name": "dev", "hostname": "localhost"})
            sid = server.json()["id"]
            endpoint = await client.post(
                "/api/admin/endpoints",
                json={
                    "server_id": sid,
                    "name": "test-endpoint",
                    "base_url": "http://upstream:8000/v1",
                    "api_key": "upstream-secret",
                    "enabled": True,
                    "capacity": 1,
                },
            )
            assert endpoint.status_code == 201, endpoint.text
            eid = endpoint.json()["id"]
            alias = await client.post(
                "/api/admin/aliases",
                json={"alias": "model", "endpoint_id": eid, "upstream_model": "upstream-model"},
            )
            assert alias.status_code == 201, alias.text
            await client.post(f"/api/admin/endpoints/{eid}/probe")
            user = await client.post("/api/admin/users", json={"name": "Researcher"})
            uid = user.json()["id"]
            key = await client.post(f"/api/admin/users/{uid}/keys", json={"name": "test"})
            token = key.json()["api_key"]
            client.headers["Authorization"] = "Bearer " + token
            yield {
                "app": app,
                "client": client,
                "upstream": upstream,
                "eid": eid,
                "uid": uid,
                "sid": sid,
                "aid": alias.json()["id"],
                "key": token,
                "kid": key.json()["id"],
            }


async def eventually(predicate, seconds=3):
    async with asyncio.timeout(seconds):
        while not await predicate():
            await asyncio.sleep(0.005)
