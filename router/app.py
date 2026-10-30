import asyncio
import fcntl
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import admin, auth, benchmark_api, public
from .auth import require_user
from .benchmark import BenchmarkManager
from .config import Settings
from .db import Database
from .discovery import Discovery
from .metrics import Metrics
from .monitor import Monitor
from .proxy import ProxyResponse, ProxyService, RouterError
from .scheduler import Scheduler
from .security import LoginLimiter, SecretBox, hash_password


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"same-origin"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                if scope["path"].startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                if not scope["path"].startswith(("/v1/", "/api/")):
                    headers.append(
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                            b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped)


def create_app(settings: Settings | None = None, upstream_transport=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = await asyncio.to_thread(open, settings.data_dir / "scheduler.lock", "a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError(
                "このデータディレクトリのRouterは既に起動中です。workerは1つにしてください"
            ) from None
        db = Database(settings.database_path)
        try:
            secrets = SecretBox(settings.data_dir, settings.encryption_key)
            await db.open()
            await db.recover()
            scheduler = Scheduler()
            for endpoint in await db.all("SELECT id,capacity FROM endpoints WHERE archived=0"):
                scheduler.queue(endpoint["id"], endpoint["capacity"])
            metrics = Metrics(db)
            service = ProxyService(db, secrets, scheduler, metrics, transport=upstream_transport)
            monitor = Monitor(db, service, secrets, transport=upstream_transport)
            discovery = Discovery(db, service, monitor, transport=upstream_transport)
            benchmarks = BenchmarkManager(db, service, settings)
            await benchmarks.initialize()
            app.state.settings, app.state.db, app.state.secrets = settings, db, secrets
            app.state.service, app.state.monitor, app.state.metrics = service, monitor, metrics
            app.state.benchmarks, app.state.discovery = benchmarks, discovery
            app.state.login_limiter = LoginLimiter()
            app.state.dummy_password_hash = await asyncio.to_thread(hash_password, os.urandom(32).hex())
            if not settings.testing:
                monitor.start()
            try:
                yield
            finally:
                await benchmarks.close()
                await discovery.close()
                await monitor.close()
                await service.close()
        finally:
            await db.close()
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(
        title="LLM Router",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SecurityHeaders)

    @app.exception_handler(RouterError)
    async def router_error(request: Request, exc: RouterError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo credentials or request content in validation error details.
        messages = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "入力内容を確認してください",
                    "details": messages,
                }
            },
            status_code=422,
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, exc: sqlite3.IntegrityError):
        return JSONResponse(
            {"error": {"code": "conflict", "message": "名前の重複または参照先を確認してください"}},
            status_code=409,
        )

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(benchmark_api.router)
    app.include_router(public.router)

    @app.get("/api/admin/openapi.json", dependencies=[Depends(auth.require_admin)])
    async def openapi():
        return app.openapi()

    @app.get("/api/branding")
    async def branding(request: Request):
        config = await request.app.state.db.settings()
        return {key: config[key] for key in ("logo_text", "organization_name", "workspace_description")}

    @app.get("/v1/models")
    async def models(request: Request, user=Depends(require_user)):
        rows = await request.app.state.db.all(
            "SELECT aliases.alias,aliases.created_at FROM aliases JOIN endpoints ON endpoints.id=aliases.endpoint_id "
            "WHERE aliases.enabled=1 AND endpoints.enabled=1 AND endpoints.archived=0 ORDER BY alias"
        )
        if user.get("benchmark_id"):
            rows = [r for r in rows if r["alias"] == user["alias"]]
        return {
            "object": "list",
            "data": [
                {"id": r["alias"], "object": "model", "created": int(r["created_at"]), "owned_by": "llm-router"}
                for r in rows
            ],
        }

    @app.post("/v1/chat/completions")
    async def completions(request: Request, user=Depends(require_user)):
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise RouterError(400, "invalid_json", "JSON形式で送信してください") from None
        if user.get("benchmark_id") and payload.get("model") != user["alias"]:
            raise RouterError(403, "benchmark_target_mismatch", "試験対象のモデルだけを利用できます")
        job = await request.app.state.service.submit(
            user["id"], payload, request.headers.get("x-request-id"), benchmark_id=user.get("benchmark_id")
        )
        return ProxyResponse(request.app.state.service, job)

    @app.get("/v1/requests/by-client-id/{client_id}")
    async def request_by_client(client_id: str, request: Request, user=Depends(require_user)):
        row = await request.app.state.db.one(
            "SELECT * FROM requests WHERE user_id=? AND client_request_id=?", (user["id"], client_id)
        )
        if not row:
            raise RouterError(404, "request_not_found", "リクエストがありません")
        return row

    @app.post("/v1/requests/by-client-id/{client_id}/cancel")
    async def cancel_by_client(client_id: str, request: Request, user=Depends(require_user)):
        row = await request.app.state.db.one(
            "SELECT id FROM requests WHERE user_id=? AND client_request_id=?", (user["id"], client_id)
        )
        if not row:
            raise RouterError(404, "request_not_found", "リクエストがありません")
        return await request.app.state.service.cancel(row["id"], user["id"])

    @app.get("/v1/requests/{rid}")
    async def get_request(rid: str, request: Request, user=Depends(require_user)):
        row = await request.app.state.db.one(
            "SELECT * FROM requests WHERE id=? AND user_id=?", (rid, user["id"])
        )
        if not row:
            raise RouterError(404, "request_not_found", "リクエストがありません")
        return row

    @app.post("/v1/requests/{rid}/cancel")
    async def cancel_request(rid: str, request: Request, user=Depends(require_user)):
        return await request.app.state.service.cancel(rid, user["id"])

    if settings.web_dist.exists():
        app.mount("/assets", StaticFiles(directory=settings.web_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "v1/")):
            raise RouterError(404, "not_found", "このAPIはありません")
        index = settings.web_dist / "index.html"
        if not index.exists():
            return JSONResponse(
                {"message": "webでnpm ci && npm run buildを実行してください"}, status_code=503
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
