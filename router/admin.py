import json
import time
import uuid

from fastapi import APIRouter, Depends, Query, Request

from .auth import require_admin
from .engines import TrialInput
from .monitor import upstream_load
from .proxy import RouterError
from .schemas import (
    AliasInput,
    CapacityInput,
    EndpointInput,
    KeyInput,
    ServerInput,
    SettingsInput,
    UserInput,
)
from .security import digest, new_key

router = APIRouter(prefix="/api/admin", tags=["administration"], dependencies=[Depends(require_admin)])


def ident():
    return str(uuid.uuid4())


async def exists(db, table, item_id):
    # table is an internal constant, not user input.
    row = await db.one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
    if not row:
        raise RouterError(404, "not_found", "対象がありません")
    return row


def endpoint_public(row, service):
    result = {k: v for k, v in row.items() if k != "credential"}
    result["has_credential"] = bool(row["credential"])
    result["observed_models"] = json.loads(row["observed_models"])
    result["native_metrics"] = json.loads(row["native_metrics"])
    result["upstream"] = upstream_load(row)
    result.update(service.scheduler.snapshot(row["id"], row["capacity"]))
    result["benchmark_id"] = service.maintenance.get(row["id"])
    result["direct_test_running"] = sum(
        j.endpoint["id"] == row["id"] and j.direct for j in service.jobs.values()
    )
    return result


@router.get("/servers")
async def servers(request: Request):
    rows = await request.app.state.db.all(
        "SELECT servers.*, (SELECT COUNT(*) FROM endpoints WHERE server_id=servers.id AND archived=0) "
        "AS endpoint_count FROM servers ORDER BY name"
    )
    for row in rows:
        row["hardware_metrics"] = json.loads(row["hardware_metrics"])
    return rows


@router.post("/servers", status_code=201)
async def create_server(data: ServerInput, request: Request):
    sid, now = ident(), time.time()
    await request.app.state.db.execute(
        "INSERT INTO servers(id,name,hostname,gpu_notes,metrics_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (sid, data.name, data.hostname, data.gpu_notes, data.metrics_url, now, now),
    )
    return {"id": sid}


@router.put("/servers/{sid}")
async def update_server(sid: str, data: ServerInput, request: Request):
    await exists(request.app.state.db, "servers", sid)
    await request.app.state.db.execute(
        "UPDATE servers SET name=?,hostname=?,gpu_notes=?,metrics_url=?,updated_at=? WHERE id=?",
        (data.name, data.hostname, data.gpu_notes, data.metrics_url, time.time(), sid),
    )
    return {"id": sid}


@router.delete("/servers/{sid}")
async def delete_server(sid: str, request: Request):
    await exists(request.app.state.db, "servers", sid)
    if await request.app.state.db.one("SELECT id FROM endpoints WHERE server_id=?", (sid,)):
        raise RouterError(409, "server_in_use", "履歴を含むEndpointが所属しているため削除できません")
    await request.app.state.db.execute("DELETE FROM servers WHERE id=?", (sid,))
    return {"ok": True}


@router.get("/endpoints")
async def endpoints(request: Request):
    rows = await request.app.state.db.all(
        "SELECT endpoints.*, servers.name AS server_name FROM endpoints "
        "JOIN servers ON servers.id=endpoints.server_id WHERE archived=0 ORDER BY endpoints.name"
    )
    return [endpoint_public(row, request.app.state.service) for row in rows]


@router.get("/endpoints/{eid}")
async def endpoint(eid: str, request: Request):
    row = await exists(request.app.state.db, "endpoints", eid)
    return endpoint_public(row, request.app.state.service)


@router.post("/endpoints", status_code=201)
async def create_endpoint(data: EndpointInput, request: Request):
    db, service = request.app.state.db, request.app.state.service
    await exists(db, "servers", data.server_id)
    eid, now = ident(), time.time()
    async with service.registry_lock:
        await db.execute(
            "INSERT INTO endpoints(id,server_id,name,base_url,backend,credential,enabled,capacity,queue_timeout_seconds,"
            "metrics_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                data.server_id,
                data.name,
                data.base_url,
                data.backend,
                request.app.state.secrets.encrypt(data.api_key),
                data.enabled,
                data.capacity,
                data.queue_timeout_seconds,
                data.metrics_url,
                now,
                now,
            ),
        )
        service.scheduler.queue(eid, data.capacity)
    return {"id": eid}


@router.put("/endpoints/{eid}")
async def update_endpoint(eid: str, data: EndpointInput, request: Request):
    db, service = request.app.state.db, request.app.state.service
    await exists(db, "servers", data.server_id)
    async with service.registry_lock:
        old = await exists(db, "endpoints", eid)
        if eid in service.maintenance:
            raise RouterError(409, "benchmark_busy", "試験を停止してから接続設定を変更してください")
        changed_target = (
            old["base_url"] != data.base_url
            or old["server_id"] != data.server_id
            or old["backend"] != data.backend
            or data.api_key is not None
        )
        if changed_target and any(j.endpoint["id"] == eid for j in service.jobs.values()):
            raise RouterError(
                409, "endpoint_busy", "待機・実行中の要求が終了してから接続先を変更してください"
            )
        secret = (
            old["credential"] if data.api_key is None else request.app.state.secrets.encrypt(data.api_key)
        )
        await db.execute(
            "UPDATE endpoints SET server_id=?,name=?,base_url=?,backend=?,credential=?,enabled=?,capacity=?,"
            "queue_timeout_seconds=?,metrics_url=?,updated_at=?,health=? WHERE id=?",
            (
                data.server_id,
                data.name,
                data.base_url,
                data.backend,
                secret,
                data.enabled,
                data.capacity,
                data.queue_timeout_seconds,
                data.metrics_url,
                time.time(),
                "unknown" if changed_target else old["health"],
                eid,
            ),
        )
        queue = service.scheduler.queue(eid, data.capacity)
        if not data.enabled:
            queue.fail_waiting("endpoint_disabled")
        queue.resize(data.capacity)
    return {"id": eid}


@router.put("/endpoints/{eid}/capacity")
async def set_capacity(eid: str, data: CapacityInput, request: Request):
    service, db = request.app.state.service, request.app.state.db
    async with service.registry_lock:
        await exists(db, "endpoints", eid)
        if eid in service.maintenance:
            raise RouterError(409, "benchmark_busy", "試験完了後にCapacityを変更してください")
        await db.execute(
            "UPDATE endpoints SET capacity=?,updated_at=? WHERE id=?", (data.capacity, time.time(), eid)
        )
        service.scheduler.queue(eid, data.capacity).resize(data.capacity)
    return {"id": eid, "capacity": data.capacity}


@router.delete("/endpoints/{eid}")
async def archive_endpoint(eid: str, request: Request):
    service, db = request.app.state.service, request.app.state.db
    async with service.registry_lock:
        await exists(db, "endpoints", eid)
        if eid in service.maintenance:
            raise RouterError(409, "benchmark_busy", "試験を停止してから削除してください")
        if any(j.endpoint["id"] == eid for j in service.jobs.values()):
            raise RouterError(409, "endpoint_busy", "要求が終了してから削除してください")
        await db.execute("UPDATE endpoints SET archived=1,enabled=0,credential=NULL WHERE id=?", (eid,))
        await db.execute("UPDATE aliases SET enabled=0 WHERE endpoint_id=?", (eid,))
    return {"ok": True}


@router.post("/endpoints/{eid}/probe")
async def probe_endpoint(eid: str, request: Request):
    await exists(request.app.state.db, "endpoints", eid)
    return await request.app.state.monitor.probe(eid)


@router.post("/endpoints/{eid}/resume")
async def resume_endpoint(eid: str, request: Request):
    async with request.app.state.service.registry_lock:
        await exists(request.app.state.db, "endpoints", eid)
        await request.app.state.db.execute("UPDATE endpoints SET recovery_required=0 WHERE id=?", (eid,))
    return {"id": eid, "recovery_required": False}


@router.get("/aliases")
async def aliases(request: Request):
    return await request.app.state.db.all(
        "SELECT aliases.*, endpoints.name AS endpoint_name, endpoints.archived "
        "FROM aliases JOIN endpoints ON endpoints.id=aliases.endpoint_id "
        "WHERE endpoints.archived=0 ORDER BY alias"
    )


@router.post("/aliases", status_code=201)
async def create_alias(data: AliasInput, request: Request):
    db, service = request.app.state.db, request.app.state.service
    async with service.registry_lock:
        ep = await exists(db, "endpoints", data.endpoint_id)
        if ep["archived"]:
            raise RouterError(409, "endpoint_archived", "削除済みの接続先です")
        aid = ident()
        await db.execute(
            "INSERT INTO aliases VALUES (?,?,?,?,?,?)",
            (aid, data.alias, data.endpoint_id, data.upstream_model, data.enabled, time.time()),
        )
    return {"id": aid}


@router.put("/aliases/{aid}")
async def update_alias(aid: str, data: AliasInput, request: Request):
    db, service = request.app.state.db, request.app.state.service
    async with service.registry_lock:
        old = await exists(db, "aliases", aid)
        target = await exists(db, "endpoints", data.endpoint_id)
        if target["archived"]:
            raise RouterError(409, "endpoint_archived", "削除済みの接続先です")
        if (
            any(j.alias == old["alias"] for j in service.jobs.values())
            or old["endpoint_id"] in service.maintenance
        ):
            raise RouterError(409, "alias_busy", "実行・待機・試験が終了してから変更してください")
        await db.execute(
            "UPDATE aliases SET alias=?,endpoint_id=?,upstream_model=?,enabled=? WHERE id=?",
            (data.alias, data.endpoint_id, data.upstream_model, data.enabled, aid),
        )
    return {"id": aid}


@router.delete("/aliases/{aid}")
async def delete_alias(aid: str, request: Request):
    db, service = request.app.state.db, request.app.state.service
    async with service.registry_lock:
        old = await exists(db, "aliases", aid)
        if (
            any(j.alias == old["alias"] for j in service.jobs.values())
            or old["endpoint_id"] in service.maintenance
        ):
            raise RouterError(409, "alias_busy", "実行・待機・試験が終了してから削除してください")
        await db.execute("DELETE FROM aliases WHERE id=?", (aid,))
    return {"ok": True}


@router.get("/users")
async def users(request: Request):
    return await request.app.state.db.all(
        "SELECT users.*, (SELECT COUNT(*) FROM api_keys WHERE user_id=users.id "
        "AND revoked_at IS NULL) AS active_keys FROM users WHERE system=0 ORDER BY name"
    )


@router.post("/users", status_code=201)
async def create_user(data: UserInput, request: Request):
    uid = ident()
    await request.app.state.db.execute(
        "INSERT INTO users(id,name,enabled,created_at) VALUES(?,?,?,?)",
        (uid, data.name, data.enabled, time.time()),
    )
    return {"id": uid}


@router.put("/users/{uid}")
async def update_user(uid: str, data: UserInput, request: Request):
    db = request.app.state.db
    user = await exists(db, "users", uid)
    if user["system"]:
        raise RouterError(403, "system_user", "システム利用者は編集できません")
    await db.execute("UPDATE users SET name=?,enabled=? WHERE id=?", (data.name, data.enabled, uid))
    if not data.enabled:
        for job in list(request.app.state.service.jobs.values()):
            if job.user_id == uid and job.status == "queued":
                request.app.state.service.scheduler.cancel(job, "user_disabled", "failed")
    return {"id": uid}


@router.get("/users/{uid}/keys")
async def keys(uid: str, request: Request):
    await exists(request.app.state.db, "users", uid)
    return await request.app.state.db.all(
        "SELECT id,user_id,name,prefix,created_at,last_used_at,revoked_at "
        "FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
        (uid,),
    )


@router.post("/users/{uid}/keys", status_code=201)
async def create_key(uid: str, data: KeyInput, request: Request):
    user = await exists(request.app.state.db, "users", uid)
    if not user["enabled"] or user["system"]:
        raise RouterError(409, "user_disabled", "有効な利用者を選んでください")
    kid, secret = ident(), new_key()
    await request.app.state.db.execute(
        "INSERT INTO api_keys(id,user_id,name,key_hash,prefix,created_at) VALUES(?,?,?,?,?,?)",
        (kid, uid, data.name, digest(secret), secret[:12], time.time()),
    )
    return {"id": kid, "api_key": secret, "name": data.name}


@router.delete("/keys/{kid}")
async def revoke_key(kid: str, request: Request):
    await exists(request.app.state.db, "api_keys", kid)
    await request.app.state.db.execute(
        "UPDATE api_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=?", (time.time(), kid)
    )
    return {"ok": True}


@router.get("/requests")
async def requests_list(
    request: Request,
    status: str | None = None,
    endpoint_id: str | None = None,
    user_id: str | None = None,
    source: str = "normal",
    search: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    clauses, params = ["requests.source=?"], [source]
    for key, value in (("status", status), ("endpoint_id", endpoint_id), ("user_id", user_id)):
        if value:
            clauses.append("requests." + key + "=?")
            params.append(value)
    if search:
        clauses.append("(requests.id LIKE ? OR requests.client_request_id LIKE ? OR requests.alias LIKE ?)")
        params.extend(["%" + search + "%"] * 3)
    where = " AND ".join(clauses)
    total = await request.app.state.db.one("SELECT COUNT(*) AS count FROM requests WHERE " + where, params)
    rows = await request.app.state.db.all(
        "SELECT requests.*, users.name AS user_name, endpoints.name AS endpoint_name FROM requests "
        "JOIN users ON users.id=requests.user_id JOIN endpoints ON endpoints.id=requests.endpoint_id "
        "WHERE " + where + " ORDER BY requested_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return {"items": rows, "total": total["count"], "limit": limit, "offset": offset}


@router.get("/requests/{rid}")
async def request_detail(rid: str, request: Request):
    return await exists(request.app.state.db, "requests", rid)


@router.post("/requests/{rid}/cancel")
async def cancel_request(rid: str, request: Request):
    return await request.app.state.service.cancel(rid)


@router.get("/metrics")
async def metrics(
    request: Request,
    hours: float = Query(1, gt=0, le=2160),
    endpoint_id: str | None = None,
    user_id: str | None = None,
    source: str = "normal",
):
    now = time.time()
    data = await request.app.state.metrics.query(now - hours * 3600, now, endpoint_id, user_id, source)
    jobs = [
        j
        for j in request.app.state.service.jobs.values()
        if j.source == source
        and (not endpoint_id or j.endpoint["id"] == endpoint_id)
        and (not user_id or j.user_id == user_id)
    ]
    data["live"] = {
        "running": sum(j.status == "running" for j in jobs),
        "queued": sum(j.status == "queued" for j in jobs),
    }
    return data


@router.get("/resources/{kind}/{target_id}")
async def resources(kind: str, target_id: str, request: Request, hours: float = Query(1, gt=0, le=2160)):
    rows = await request.app.state.db.all(
        "SELECT observed_at,data FROM resource_samples WHERE kind=? AND target_id=? "
        "AND observed_at>=? ORDER BY observed_at DESC LIMIT 500",
        (kind, target_id, time.time() - hours * 3600),
    )
    return [{"time": row["observed_at"], **json.loads(row["data"])} for row in reversed(rows)]


@router.get("/settings")
async def settings(request: Request):
    return await request.app.state.db.settings()


@router.put("/settings")
async def set_settings(data: SettingsInput, request: Request):
    values = data.model_dump(exclude_unset=True)
    merged = {**await request.app.state.db.settings(), **values}
    span = merged["discovery_port_end"] - merged["discovery_port_start"]
    if not 0 <= span < 1000:
        raise RouterError(422, "invalid_port_range", "ポート範囲は開始≦終了、1000個以内にしてください")
    async with request.app.state.db.transaction() as conn:
        for key, value in values.items():
            await conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
    return await request.app.state.db.settings()


@router.get("/benchmarks")
async def benchmarks(request: Request):
    rows = await request.app.state.db.all(
        "SELECT benchmarks.*, endpoints.name AS endpoint_name FROM benchmarks "
        "JOIN endpoints ON endpoints.id=benchmarks.endpoint_id ORDER BY created_at DESC LIMIT 200"
    )
    for row in rows:
        row["config"] = json.loads(row["config"])
    return rows


@router.post("/benchmarks", status_code=201)
async def start_benchmark(data: TrialInput, request: Request, admin=Depends(require_admin)):
    return await request.app.state.benchmarks.start(data, admin["admin_id"])


@router.get("/benchmarks/{bid}")
async def benchmark_detail(bid: str, request: Request):
    row = await exists(request.app.state.db, "benchmarks", bid)
    row["config"] = json.loads(row["config"])
    steps = await request.app.state.db.all(
        "SELECT * FROM benchmark_steps WHERE benchmark_id=? ORDER BY step", (bid,)
    )
    for step in steps:
        step["results"] = json.loads(step["results"])
    row["steps"] = steps
    progress = await request.app.state.db.one(
        "SELECT data FROM benchmark_progress WHERE benchmark_id=?", (bid,)
    )
    row["progress"] = json.loads(progress["data"]) if progress else {}
    return row


@router.post("/benchmarks/{bid}/stop")
async def stop_benchmark(bid: str, request: Request):
    await exists(request.app.state.db, "benchmarks", bid)
    return await request.app.state.benchmarks.stop(bid)


@router.delete("/benchmarks/{bid}")
async def delete_benchmark(bid: str, request: Request):
    record = await exists(request.app.state.db, "benchmarks", bid)
    if bid in request.app.state.benchmarks.tasks:
        raise RouterError(409, "benchmark_busy", "実行中の試験は削除できません")
    if json.loads(record["config"]).get("engine"):
        await request.app.state.benchmarks.rpc("DELETE", f"/jobs/{bid}")
    await request.app.state.db.execute("DELETE FROM benchmarks WHERE id=?", (bid,))
    return {"ok": True}


@router.get("/discovery")
async def discovery(request: Request):
    return request.app.state.discovery.last


@router.post("/discovery/scan")
async def discovery_scan(request: Request):
    return await request.app.state.discovery.scan()
