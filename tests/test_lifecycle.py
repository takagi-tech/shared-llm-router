import asyncio
import json
import time
import uuid

from router.metrics import BOUNDS, add_sample, blank, describe, merge


def request_body():
    return {"model": "model", "stream": True, "messages": [{"role": "user", "content": "x"}]}


async def test_disconnect_while_queued_removes_request_without_dispatch(env):
    app, upstream = env["app"], env["upstream"]
    upstream.gate = asyncio.Event()
    service = app.state.service
    running = await service.submit(env["uid"], request_body())
    waiting = await service.submit(env["uid"], request_body())
    events = []

    async def send(message):
        events.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    first = asyncio.create_task(service.run(running, send))
    await service.run(waiting, send, receive)
    assert waiting.status == "cancelled"
    assert not waiting.admitted.is_set()
    service.scheduler.cancel(running)
    await first
    assert len(upstream.requests) <= 1
    assert not service.jobs


async def test_disconnect_during_stream_closes_upstream(env):
    service, upstream = env["app"].state.service, env["upstream"]
    upstream.gate = asyncio.Event()
    job = await service.submit(env["uid"], request_body())
    disconnect = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.start":
            disconnect.set()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    await service.run(job, send, receive)
    assert job.status == "cancelled" and upstream.running == 0
    assert service.scheduler.snapshot(env["eid"])["running"] == 0


async def test_queue_timeout_does_not_cancel_active_request(env):
    db, service = env["app"].state.db, env["app"].state.service
    await db.execute("UPDATE endpoints SET queue_timeout_seconds=.02 WHERE id=?", (env["eid"],))
    env["upstream"].gate = asyncio.Event()
    first = await service.submit(env["uid"], request_body())
    second = await service.submit(env["uid"], request_body())

    async def discard(message):
        pass

    first_task = asyncio.create_task(service.run(first, discard))
    await service.run(second, discard)
    assert second.status == "failed" and second.reason == "queue_timeout"
    assert not first.finished
    service.scheduler.cancel(first)
    await first_task


async def test_endpoint_disable_cancels_waiting_but_not_running(env):
    client, service = env["client"], env["app"].state.service
    env["upstream"].gate = asyncio.Event()
    first = await service.submit(env["uid"], request_body())
    second = await service.submit(env["uid"], request_body())

    async def discard(message):
        pass

    tasks = [asyncio.create_task(service.run(j, discard)) for j in (first, second)]
    ep = (await client.get(f"/api/admin/endpoints/{env['eid']}")).json()
    body = {k: ep[k] for k in ("server_id", "name", "base_url", "capacity")}
    body["enabled"] = False
    response = await client.put(f"/api/admin/endpoints/{env['eid']}", json=body)
    assert response.status_code == 200, response.text
    await tasks[1]
    assert second.status == "failed" and not first.cancelled.is_set()
    service.scheduler.cancel(first)
    await tasks[0]


async def test_different_endpoints_do_not_share_slots(env):
    db, service = env["app"].state.db, env["app"].state.service
    ep2 = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO endpoints(id,server_id,name,base_url,enabled,health,created_at,updated_at) "
        "VALUES(?,?,?,'http://other/v1',1,'healthy',?,?)",
        (ep2, env["sid"], "other", time.time(), time.time()),
    )
    await db.execute(
        "INSERT INTO aliases VALUES(?,?,?,'upstream-model',1,?)",
        (str(uuid.uuid4()), "other", ep2, time.time()),
    )
    first = await service.submit(env["uid"], request_body())
    other = await service.submit(env["uid"], {**request_body(), "model": "other"})
    assert first.admitted.is_set() and other.admitted.is_set()
    for j in (first, other):
        j.status = "cancelled"
        await service.finalize(j)


async def test_recovery_records_interrupted_and_holds_endpoint(env):
    db, client = env["app"].state.db, env["client"]
    rid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO requests(id,user_id,endpoint_id,alias,upstream_model,status,streaming,requested_at) "
        "VALUES(?,?,?,'model','upstream-model','running',1,?)",
        (rid, env["uid"], env["eid"], time.time() - 30),
    )
    await db.recover()
    row = await db.one("SELECT * FROM requests WHERE id=?", (rid,))
    assert row["status"] == "interrupted"
    result = await client.post("/v1/chat/completions", json=request_body())
    assert result.status_code == 503 and result.json()["error"]["code"] == "recovery_required"
    await client.post(f"/api/admin/endpoints/{env['eid']}/resume")
    assert (await client.post("/v1/chat/completions", json=request_body())).status_code == 200


def test_histograms_merge_counts_not_percentiles():
    a, b = blank(), blank()
    for _ in range(100):
        add_sample(a, {"status": "completed", "e2e_ms": 10})
    add_sample(b, {"status": "completed", "e2e_ms": 100000})
    merge(a, b)
    result = describe(a)
    assert result["e2e_ms"]["p95"] == 10
    assert result["input_known"] == 0
    assert sum(a["e2e_ms"]["hist"]) == 101
    add_sample(b, {"status": "completed", "e2e_ms": BOUNDS[-1] * 2})
    assert describe(b)["e2e_ms"]["p99"] is None


async def test_retention_removes_raw_body_free_records_keeps_benchmark_results(env):
    client, db = env["client"], env["app"].state.db
    result = await client.post("/v1/chat/completions", json=request_body())
    rid = result.headers["x-request-id"]
    await db.execute("UPDATE requests SET completed_at=? WHERE id=?", (time.time() - 40 * 86400, rid))
    await env["app"].state.metrics.retain()
    assert not await db.one("SELECT id FROM requests WHERE id=?", (rid,))
    # Aggregate observations persist independently of detailed request rows.
    aggregate = await db.one("SELECT data FROM metric_minutes")
    assert json.loads(aggregate["data"])["total"] == 1
