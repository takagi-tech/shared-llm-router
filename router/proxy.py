import asyncio
import contextlib
import json
import logging
import re
import sqlite3
import time
import uuid

import httpx
from starlette.responses import Response

from .db import Database
from .metrics import Metrics
from .scheduler import Job, Scheduler
from .security import SecretBox

log = logging.getLogger(__name__)


class RouterError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)

    def body(self):
        return {"error": {"message": self.message, "type": "router_error", "code": self.code}}


class Observation:
    """Reads a copy of SSE bytes for metrics; never rebuilds or buffers the relay."""

    def __init__(self):
        self.buffer = b""
        self.lines: list[bytes] = []
        self.first_mono: float | None = None
        self.first_at: float | None = None
        self.last_mono: float | None = None
        self.input_tokens = None
        self.output_tokens = None
        self.done = False
        self.error = False

    def usage(self, value):
        if isinstance(value, dict):
            for src, dest in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                n = value.get(src)
                if type(n) is int and n >= 0:
                    setattr(self, dest, n)

    def consume(self, data: bytes):
        if data.strip() == b"[DONE]":
            self.done = True
            return
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(value, dict):
            return
        self.usage(value.get("usage"))
        if value.get("error"):
            self.error = True
        for choice in value.get("choices") or []:
            delta = choice.get("delta") or {}
            output = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning")
            output = output or any(
                (call.get("function") or {}).get("arguments") or (call.get("function") or {}).get("name")
                for call in delta.get("tool_calls") or []
            )
            if output:
                now = time.monotonic()
                if self.first_mono is None:
                    self.first_mono, self.first_at = now, time.time()
                self.last_mono = now

    def feed(self, data: bytes):
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line:
                if self.lines:
                    self.consume(b"\n".join(self.lines))
                    self.lines.clear()
            elif line.startswith(b"data:"):
                self.lines.append(line[5:].lstrip(b" "))


class ProxyService:
    def __init__(
        self, db: Database, secrets: SecretBox, scheduler: Scheduler, metrics: Metrics, transport=None
    ):
        self.db, self.secrets, self.scheduler, self.metrics = db, secrets, scheduler, metrics
        # An explicit read=None prevents HTTPX's default inactivity deadline.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=None, write=None, pool=None),
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.jobs: dict[str, Job] = {}
        self.maintenance: dict[str, str] = {}
        self.registry_lock = asyncio.Lock()
        self.stopping = False

    async def close(self):
        self.stopping = True
        for job in list(self.jobs.values()):
            self.scheduler.cancel(job, "router_shutdown", "interrupted")
        # Lifespan normally runs after connections drain. Explicit cancellation also
        # handles jobs owned by the benchmark runner.
        await self.client.aclose()

    async def submit(
        self,
        user_id: str,
        payload: dict,
        client_id: str | None = None,
        benchmark_id: str | None = None,
        direct=False,
    ) -> Job:
        if self.stopping:
            raise RouterError(503, "router_stopping", "Routerは停止処理中です")
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
            raise RouterError(400, "invalid_request", "modelを指定してください")
        if not isinstance(payload.get("messages"), list) or not payload["messages"]:
            raise RouterError(400, "invalid_messages", "messagesを指定してください")
        if type(payload.get("stream", False)) is not bool:
            raise RouterError(400, "invalid_stream", "streamは真偽値で指定してください")
        for message in payload["messages"]:
            if not isinstance(message, dict):
                raise RouterError(400, "invalid_messages", "messagesの形式が不正です")
            content = message.get("content")
            if isinstance(content, list) and any(
                not isinstance(part, dict) or part.get("type") != "text" for part in content
            ):
                raise RouterError(
                    400, "unsupported_content", "初回版はテキストとtool callingに対応しています"
                )
        if client_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", client_id):
            raise RouterError(
                400, "invalid_request_id", "X-Request-IDは英数字等128文字以内で指定してください"
            )
        async with self.registry_lock:
            alias = await self.db.one(
                "SELECT * FROM aliases WHERE alias=? AND enabled=1", (payload["model"],)
            )
            if not alias:
                raise RouterError(404, "model_not_found", "指定されたモデルエイリアスはありません")
            endpoint = await self.db.one("SELECT * FROM endpoints WHERE id=?", (alias["endpoint_id"],))
            if not endpoint["enabled"] or endpoint["archived"]:
                raise RouterError(503, "endpoint_disabled", "この接続先は無効です")
            if endpoint["recovery_required"]:
                raise RouterError(
                    503, "recovery_required", "再起動前の上流処理を確認し、管理者が受付を再開してください"
                )
            if self.maintenance.get(endpoint["id"]) not in (None, benchmark_id):
                raise RouterError(503, "endpoint_maintenance", "この接続先は負荷試験中です")
            if not benchmark_id and endpoint["health"] != "healthy":
                raise RouterError(503, "endpoint_unavailable", "この接続先は未確認または不健全です")
            forwarded = dict(payload)
            forwarded["model"] = alias["upstream_model"]
            job = Job(
                str(uuid.uuid4()),
                user_id,
                endpoint,
                alias["alias"],
                forwarded,
                source="benchmark" if benchmark_id else "normal",
                benchmark_id=benchmark_id,
                direct=direct,
            )
            job.observer = Observation()
            try:
                await self.db.execute(
                    "INSERT INTO requests (id, client_request_id, user_id, endpoint_id, alias, upstream_model, "
                    "status, streaming, source, benchmark_id, requested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job.id,
                        client_id,
                        user_id,
                        endpoint["id"],
                        job.alias,
                        alias["upstream_model"],
                        "queued",
                        bool(payload.get("stream")),
                        job.source,
                        benchmark_id,
                        job.requested_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RouterError(
                    409, "duplicate_request_id", "この利用者のX-Request-IDは既に使用されています"
                ) from exc
            self.jobs[job.id] = job
            if direct:
                job.started_at, job.started_mono = time.time(), time.monotonic()
                job.status = "running"
                job.admitted.set()
            else:
                self.scheduler.enqueue(job)
        return job

    async def cancel(self, request_id: str, user_id: str | None = None):
        row = await self.db.one("SELECT * FROM requests WHERE id=?", (request_id,))
        if not row or (user_id and row["user_id"] != user_id):
            raise RouterError(404, "request_not_found", "リクエストがありません")
        job = self.jobs.get(request_id)
        if job:
            self.scheduler.cancel(job)
        return {"id": request_id, "status": row["status"], "cancellation_requested": bool(job)}

    async def mark_started(self, job: Job):
        await self.db.execute(
            "UPDATE requests SET status='running', started_at=?, queue_ms=? WHERE id=? AND completed_at IS NULL",
            (job.started_at, (job.started_mono - job.requested_mono) * 1000, job.id),
        )

    async def execute(self, job: Job, send):
        queue_timeout = job.endpoint.get("queue_timeout_seconds")
        if queue_timeout is None:
            queue_timeout = (await self.db.settings())["default_queue_timeout_seconds"]
        try:
            if queue_timeout is None:
                await job.admitted.wait()
            else:
                await asyncio.wait_for(job.admitted.wait(), queue_timeout)
        except TimeoutError:
            job.cancel("queue_timeout", "failed")
            return
        if job.cancelled.is_set():
            return
        await self.mark_started(job)
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
        key = self.secrets.decrypt(job.endpoint["credential"])
        if key:
            headers["Authorization"] = "Bearer " + key
        req = self.client.build_request(
            "POST", job.endpoint["base_url"] + "/chat/completions", json=job.payload, headers=headers
        )
        response = None
        try:
            response = await self.client.send(req, stream=True)
            job.http_status = response.status_code
            streaming = bool(job.payload.get("stream")) and response.status_code < 400
            if streaming and "text/event-stream" not in response.headers.get("content-type", ""):
                raise RouterError(502, "invalid_upstream_stream", "上流からSSE応答を受信できませんでした")
            if 300 <= response.status_code < 400:
                raise RouterError(502, "upstream_redirect", "上流のリダイレクトには対応していません")
            content_type = response.headers.get("content-type", "application/json")
            out_headers = [
                (b"content-type", content_type.encode()),
                (b"x-request-id", job.id.encode()),
                (b"cache-control", b"no-store"),
                (b"x-accel-buffering", b"no"),
            ]
            if streaming:
                await send(
                    {"type": "http.response.start", "status": response.status_code, "headers": out_headers}
                )
                job.headers_sent = True
                async for chunk in response.aiter_bytes():
                    job.observer.feed(chunk)
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    if job.observer.done:
                        break
                if job.observer.error:
                    job.status, job.reason = "failed", "upstream_stream_error"
                elif not job.observer.done:
                    raise RouterError(
                        502, "upstream_stream_interrupted", "上流のStreaming応答が途中で終了しました"
                    )
                else:
                    job.status = "completed"
            else:
                body = await response.aread()
                if response.status_code < 400:
                    try:
                        data = json.loads(body)
                    except ValueError as exc:
                        raise RouterError(
                            502, "invalid_upstream_response", "上流のJSON応答が不正です"
                        ) from exc
                    if isinstance(data, dict):
                        job.observer.usage(data.get("usage"))
                    job.status = "completed"
                else:
                    job.status, job.reason = "failed", f"upstream_http_{response.status_code}"
                await send(
                    {"type": "http.response.start", "status": response.status_code, "headers": out_headers}
                )
                job.headers_sent = True
                await send({"type": "http.response.body", "body": body, "more_body": True})
        finally:
            if response:
                await response.aclose()

    async def finalize(self, job: Job):
        if job.finished:
            return
        job.finished = True
        now, mono = time.time(), time.monotonic()
        obs = job.observer
        speed = None
        if obs.output_tokens is not None and obs.output_tokens > 1 and obs.last_mono and obs.first_mono:
            elapsed = obs.last_mono - obs.first_mono
            if elapsed > 0:
                speed = (obs.output_tokens - 1) / elapsed
        values = {
            "status": job.status,
            "completed_at": now,
            "started_at": job.started_at,
            "first_token_at": obs.first_at,
            "queue_ms": ((job.started_mono or mono) - job.requested_mono) * 1000,
            "upstream_ms": (mono - job.started_mono) * 1000 if job.started_mono else None,
            "ttft_ms": (obs.first_mono - job.started_mono) * 1000
            if obs.first_mono and job.started_mono
            else None,
            "e2e_ms": (mono - job.requested_mono) * 1000,
            "input_tokens": obs.input_tokens,
            "output_tokens": obs.output_tokens,
            "output_tokens_per_second": speed,
            "http_status": job.http_status,
            "error_type": job.reason,
        }
        try:
            await self.metrics.finish(job.id, values)
        finally:
            self.scheduler.release(job)
            self.jobs.pop(job.id, None)
            # Do not keep request content reachable after the request finishes.
            job.payload.clear()
            obs.buffer = b""
            obs.lines.clear()

    async def run(self, job: Job, send, receive=None):
        async def disconnected():
            if receive is None:
                await asyncio.Future()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        work = asyncio.create_task(self.execute(job, send))
        cancel = asyncio.create_task(job.cancelled.wait())
        disconnect = asyncio.create_task(disconnected())
        client_gone = False
        try:
            done, _ = await asyncio.wait((work, cancel, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if disconnect in done:
                client_gone = True
                job.cancel("client_disconnected")
            if job.cancelled.is_set():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await work
                job.status = job.cancel_status
                code = 504 if job.reason == "queue_timeout" else 503 if job.status != "cancelled" else 499
                if not client_gone:
                    await self.send_error(
                        job, send, RouterError(code, job.reason, "リクエストは終了しました")
                    )
            else:
                await work
        except RouterError as exc:
            job.status, job.reason = "failed", exc.code
            await self.send_error(job, send, exc)
        except httpx.HTTPError:
            job.status, job.reason = "failed", "upstream_connection_error"
            await self.send_error(job, send, RouterError(502, job.reason, "上流との通信に失敗しました"))
        except (OSError, asyncio.CancelledError):
            job.status, job.reason = "cancelled", "client_disconnected"
            client_gone = True
        except Exception:
            job.status, job.reason = "failed", "internal_error"
            # Do not log request content or a URL that could contain credentials.
            log.error("Request %s failed internally", job.id)
            await self.send_error(job, send, RouterError(500, job.reason, "Router内部でエラーが発生しました"))
        finally:
            for task in (work, cancel, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, cancel, disconnect, return_exceptions=True)
            if not client_gone:
                with contextlib.suppress(OSError):
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            await self.finalize(job)

    async def send_error(self, job: Job, send, error: RouterError):
        body = json.dumps(error.body(), ensure_ascii=False).encode()
        if not job.headers_sent:
            job.http_status = error.status
            await send(
                {
                    "type": "http.response.start",
                    "status": error.status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"x-request-id", job.id.encode()),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            job.headers_sent = True
        elif job.payload.get("stream"):
            body = b"data: " + body + b"\n\ndata: [DONE]\n\n"
        else:
            return
        await send({"type": "http.response.body", "body": body, "more_body": True})


class ProxyResponse(Response):
    media_type = "application/json"

    def __init__(self, service: ProxyService, job: Job):
        super().__init__(content=b"")
        self.service, self.job = service, job

    async def __call__(self, scope, receive, send):
        await self.service.run(self.job, send, receive)
