import asyncio
import secrets
import time

from fastapi import APIRouter, Depends, Request, Response

from .proxy import RouterError
from .schemas import LoginInput
from .security import digest, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])
COOKIE = "llm_router_session"


def same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = request.app.state.settings.public_origin or str(request.base_url).rstrip("/")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin.rstrip("/") != expected):
        raise RouterError(403, "origin_denied", "同一オリジンから操作してください")


async def require_admin(request: Request):
    if request.app.state.settings.open_access:
        # No login: still refuse writes from other sites so a web page cannot change settings.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            same_origin(request)
        return {"username": "", "csrf": "", "admin_id": None, "token_hash": "", "open": True}
    token = request.cookies.get(COOKIE)
    db = request.app.state.db
    session = await db.one(
        "SELECT sessions.*, admins.username FROM sessions JOIN admins ON admins.id=sessions.admin_id "
        "WHERE token_hash=? AND expires_at>?",
        (digest(token or ""), time.time()),
    )
    if not session:
        raise RouterError(401, "admin_auth_required", "管理者ログインが必要です")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        same_origin(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise RouterError(403, "csrf_denied", "セッションを再読み込みしてください")
    return session


async def require_user(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise RouterError(401, "invalid_api_key", "有効なRouter API Keyが必要です")
    token = authorization[7:]
    trial = request.app.state.benchmarks.authenticate(token)
    if trial:
        if (request.method, request.url.path) not in (
            ("GET", "/v1/models"),
            ("POST", "/v1/chat/completions"),
        ):
            raise RouterError(403, "benchmark_scope", "試験認証ではモデル照会と推論だけを利用できます")
        return trial
    row = await request.app.state.db.one(
        "SELECT users.id, users.name, api_keys.id AS key_id FROM api_keys "
        "JOIN users ON users.id=api_keys.user_id "
        "WHERE key_hash=? AND revoked_at IS NULL AND users.enabled=1 AND users.system=0",
        (digest(token),),
    )
    if not row:
        raise RouterError(401, "invalid_api_key", "有効なRouter API Keyが必要です")
    await request.app.state.db.execute(
        "UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), row["key_id"])
    )
    return row


@router.get("/status")
async def status(request: Request):
    row = await request.app.state.db.one("SELECT COUNT(*) AS count FROM admins")
    return {"initialized": row["count"] > 0}


@router.post("/login")
async def login(data: LoginInput, request: Request, response: Response):
    same_origin(request)
    address = request.client.host if request.client else "unknown"
    if not request.app.state.login_limiter.allowed(address):
        raise RouterError(429, "too_many_attempts", "しばらく待ってからログインしてください")
    db = request.app.state.db
    admin = await db.one("SELECT * FROM admins WHERE username=?", (data.username,))
    hashed = admin["password_hash"] if admin else request.app.state.dummy_password_hash
    valid = await asyncio.to_thread(verify_password, hashed, data.password)
    if not admin or not valid:
        raise RouterError(401, "invalid_login", "ユーザー名またはパスワードが違います")
    old = request.cookies.get(COOKIE)
    if old:
        await db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(old),))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    max_age = request.app.state.settings.session_hours * 3600
    await db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?)", (digest(token), admin["id"], csrf, time.time() + max_age)
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="strict",
        max_age=max_age,
        path="/",
    )
    return {"username": admin["username"], "csrf": csrf}


@router.get("/session")
async def session(admin=Depends(require_admin)):
    return {"username": admin["username"], "csrf": admin["csrf"], "open": bool(admin.get("open"))}


@router.post("/logout")
async def logout(request: Request, response: Response, admin=Depends(require_admin)):
    await request.app.state.db.execute("DELETE FROM sessions WHERE token_hash=?", (admin["token_hash"],))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
