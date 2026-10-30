import asyncio
import json

from conftest import eventually


def payload(**extra):
    return {"model": "model", "messages": [{"role": "user", "content": "hello"}], **extra}


async def test_model_alias_and_auth_separation(env):
    client = env["client"]
    assert (await client.get("/v1/models")).json()["data"][0]["id"] == "model"
    response = await client.post("/v1/chat/completions", json=payload())
    assert response.status_code == 200, response.text
    req, upstream = env["upstream"].requests[-1]
    assert upstream["model"] == "upstream-model"
    assert req.headers["authorization"] == "Bearer upstream-secret"
    assert "cookie" not in req.headers
    records = (await client.get("/api/admin/requests")).json()["items"]
    assert records[0]["input_tokens"] == 10 and records[0]["ttft_ms"] is None
    assert "hello" not in json.dumps(records)
    endpoint = (await client.get(f"/api/admin/endpoints/{env['eid']}")).json()
    assert "credential" not in endpoint and endpoint["has_credential"]
    encrypted = await env["app"].state.db.one("SELECT credential FROM endpoints WHERE id=?", (env["eid"],))
    assert "upstream-secret" not in encrypted["credential"]


async def test_invalid_key_and_unknown_model_not_forwarded(env):
    client = env["client"]
    result = await client.post(
        "/v1/chat/completions", json=payload(), headers={"Authorization": "Bearer wrong"}
    )
    assert result.status_code == 401
    assert (await client.post("/v1/chat/completions", json=payload(model="missing"))).status_code == 404
    assert not env["upstream"].requests


async def test_long_input_and_unset_lengths_preserved(env):
    value = payload(messages=[{"role": "user", "content": "長文" * 50000}], temperature=0.2)
    response = await env["client"].post("/v1/chat/completions", json=value)
    assert response.status_code == 200
    forwarded = env["upstream"].requests[-1][1]
    assert forwarded == {**value, "model": "upstream-model"}
    value["max_completion_tokens"] = 20000
    await env["client"].post("/v1/chat/completions", json=value)
    assert env["upstream"].requests[-1][1]["max_completion_tokens"] == 20000


async def test_streaming_tool_call_bytes_and_metrics(env):
    value = payload(
        stream=True,
        tools=[{"type": "function", "function": {"name": "report", "parameters": {}}}],
        tool_choice="auto",
        parallel_tool_calls=True,
    )
    response = await env["client"].post("/v1/chat/completions", json=value)
    assert response.status_code == 200, response.text
    assert '"arguments": "' in response.text and "日本語" in response.text and "[DONE]" in response.text
    assert response.content == b"".join(env["upstream"].streams[-1].chunks)
    req, forwarded = env["upstream"].requests[-1]
    assert forwarded == {**value, "model": "upstream-model"}
    assert req.extensions["timeout"] == {"connect": 5, "read": None, "write": None, "pool": None}
    row = (await env["client"].get("/v1/requests/" + response.headers["x-request-id"])).json()
    assert row["status"] == "completed" and row["ttft_ms"] is not None
    assert row["output_tokens"] == 5 and env["upstream"].running == 0


async def test_streaming_truncation_is_failure_not_success(env):
    env["upstream"].truncated = True
    response = await env["client"].post("/v1/chat/completions", json=payload(stream=True))
    assert "upstream_stream_interrupted" in response.text
    row = (await env["client"].get("/v1/requests/" + response.headers["x-request-id"])).json()
    assert row["status"] == "failed"
    assert env["app"].state.service.scheduler.snapshot(env["eid"])["running"] == 0


async def test_running_and_waiting_cancel_by_client_id(env):
    client, upstream = env["client"], env["upstream"]
    upstream.gate = asyncio.Event()
    first = asyncio.create_task(
        client.post("/v1/chat/completions", json=payload(stream=True), headers={"X-Request-ID": "first"})
    )
    second = asyncio.create_task(
        client.post("/v1/chat/completions", json=payload(stream=True), headers={"X-Request-ID": "second"})
    )

    async def both():
        return len(env["app"].state.service.jobs) == 2

    await eventually(both)
    assert len(upstream.requests) <= 1
    assert (await client.post("/v1/requests/by-client-id/second/cancel")).status_code == 200
    response = await second
    assert response.status_code == 499
    assert (await client.post("/v1/requests/by-client-id/first/cancel")).status_code == 200
    await first
    assert len(upstream.requests) <= 1 and upstream.running == 0
    assert not env["app"].state.service.jobs


async def test_duplicate_client_id_and_cross_user_cancel(env):
    client = env["client"]
    first = await client.post("/v1/chat/completions", json=payload(), headers={"X-Request-ID": "unique"})
    assert (
        await client.post("/v1/chat/completions", json=payload(), headers={"X-Request-ID": "unique"})
    ).status_code == 409
    user = (await client.post("/api/admin/users", json={"name": "Other"})).json()
    key = (await client.post(f"/api/admin/users/{user['id']}/keys", json={"name": "key"})).json()["api_key"]
    assert (
        await client.post(
            "/v1/requests/" + first.headers["x-request-id"] + "/cancel",
            headers={"Authorization": "Bearer " + key},
        )
    ).status_code == 404
    # Same client ID is valid for a different user.
    assert (
        await client.post(
            "/v1/chat/completions",
            json=payload(),
            headers={"X-Request-ID": "unique", "Authorization": "Bearer " + key},
        )
    ).status_code == 200


async def test_admin_csrf_and_api_key_cannot_manage(env):
    client = env["client"]
    assert (
        await client.post("/api/admin/users", json={"name": "x"}, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    assert (
        await client.post("/api/admin/users", json={"name": "x"}, headers={"Origin": "http://evil"})
    ).status_code == 403
    client.cookies.clear()
    assert (await client.get("/api/admin/users")).status_code == 401


async def test_revocation_and_duplicate_alias(env):
    client = env["client"]
    assert (
        await client.post(
            "/api/admin/aliases", json={"alias": "model", "endpoint_id": env["eid"], "upstream_model": "x"}
        )
    ).status_code == 409
    await client.delete(f"/api/admin/keys/{env['kid']}")
    assert (await client.get("/v1/models")).status_code == 401


async def test_health_failure_and_recovery(env):
    upstream, client = env["upstream"], env["client"]
    config = (await client.get("/api/admin/settings")).json()
    config.update(health_failure_threshold=3, health_recovery_threshold=2)
    assert (await client.put("/api/admin/settings", json=config)).status_code == 200
    upstream.health_status = 503
    for _ in range(3):
        result = await client.post(f"/api/admin/endpoints/{env['eid']}/probe")
    assert result.json()["health"] == "unhealthy"
    assert (await client.post("/v1/chat/completions", json=payload())).status_code == 503
    upstream.health_status = 200
    assert (await client.post(f"/api/admin/endpoints/{env['eid']}/probe")).json()["health"] == "unhealthy"
    assert (await client.post(f"/api/admin/endpoints/{env['eid']}/probe")).json()["health"] == "healthy"


async def test_metrics_missing_tokens_not_zero(env):
    await env["client"].post("/v1/chat/completions", json=payload())
    metrics = (await env["client"].get("/api/admin/metrics")).json()
    assert metrics["summary"]["completed"] == 1
    assert metrics["summary"]["ttft_ms"]["count"] == 0
    assert metrics["summary"]["ttft_ms"]["p50"] is None
    assert metrics["summary"]["input_known"] == 1


async def test_branding_public_fields_and_persistent_admin_settings(env):
    import httpx

    client = env["client"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://testserver"
    ) as public:
        original = (await public.get("/api/branding")).json()
        assert original == {
            "logo_text": "llm",
            "organization_name": "Lab",
            "workspace_description": "研究室の推論基盤",
        }
        config = (await client.get("/api/admin/settings")).json()
        config.update(logo_text="DEMO", organization_name="DEMO", workspace_description="共用の推論基盤")
        assert (await public.put("/api/admin/settings", json=config)).status_code in (401, 403)
        assert (await client.put("/api/admin/settings", json=config)).status_code == 200
        assert (await public.get("/api/branding")).json() == {k: config[k] for k in original}
        assert (await env["app"].state.db.settings())["logo_text"] == "DEMO"
        legacy = {k: v for k, v in config.items() if k not in original}
        saved = await client.put("/api/admin/settings", json=legacy)
        assert saved.status_code == 200 and saved.json()["logo_text"] == "DEMO"
        for invalid in ("", " " * 3, "a" * 13, "a\nb"):
            assert (
                await client.put("/api/admin/settings", json={**config, "logo_text": invalid})
            ).status_code == 422
