import httpx


def lab(request):
    port = request.url.port
    if request.url.host == "192.0.2.20" and port in (8000, 8002):
        return httpx.Response(200, json={"data": [{"id": f"model-{port}", "owned_by": "vllm"}]})
    if request.url.host == "192.0.2.20" and port == 8080:
        # Open WebUI and other web apps answer with HTML, not a model list.
        return httpx.Response(200, text="<html>Open WebUI</html>")
    if request.url.host == "192.0.2.40" and port == 8001:
        return httpx.Response(200, json={"data": [{"id": "gguf", "owned_by": "llamacpp"}]})
    raise httpx.ConnectError("refused", request=request)


async def configure(env, **values):
    client = env["client"]
    config = (await client.get("/api/admin/settings")).json()
    return await client.put("/api/admin/settings", json={**config, **values})


async def test_scan_registers_found_servers_once(env):
    discovery = env["app"].state.discovery
    await discovery.client.aclose()
    discovery.client = httpx.AsyncClient(transport=httpx.MockTransport(lab))
    saved = await configure(
        env, discovery_hosts=["192.0.2.20", "192.0.2.40", "192.0.2.41"], discovery_port_end=8100
    )
    assert saved.status_code == 200, saved.text

    result = (await env["client"].post("/api/admin/discovery/scan")).json()
    ports = {r["host"]: [(f["port"], f["new"]) for f in r["found"]] for r in result["hosts"]}
    assert ports == {
        "192.0.2.20": [(8000, True), (8002, True)],
        "192.0.2.40": [(8001, True)],
        "192.0.2.41": [],
    }

    endpoints = {e["name"]: e for e in (await env["client"].get("/api/admin/endpoints")).json()}
    assert endpoints["192.0.2.20:8000"]["base_url"] == "http://192.0.2.20:8000/v1"
    assert endpoints["192.0.2.20:8000"]["enabled"] == 1
    assert endpoints["192.0.2.20:8000"]["health"] == "healthy"
    assert endpoints["192.0.2.40:8001"]["backend"] == "llamacpp"
    assert "192.0.2.20:8080" not in endpoints

    again = (await env["client"].post("/api/admin/discovery/scan")).json()
    assert all(not f["new"] for r in again["hosts"] for f in r["found"])
    assert len((await env["client"].get("/api/admin/endpoints")).json()) == len(endpoints)
    servers = [s["name"] for s in (await env["client"].get("/api/admin/servers")).json()]
    assert sorted(servers) == ["192.0.2.20", "192.0.2.40", "192.0.2.41", "dev"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://testserver"
    ) as public:
        status = (await public.get("/api/public/status")).json()
        names = {e["name"] for s in status["servers"] for e in s["endpoints"]}
        assert {"192.0.2.20:8000", "192.0.2.20:8002", "192.0.2.40:8001"} <= names


async def test_scan_reuses_manually_registered_endpoint(env):
    discovery = env["app"].state.discovery
    await discovery.client.aclose()
    discovery.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": [{"id": "upstream-model"}]})
        )
    )
    await configure(env, discovery_hosts=["upstream"], discovery_port_start=8000, discovery_port_end=8000)
    result = (await env["client"].post("/api/admin/discovery/scan")).json()
    (found,) = result["hosts"][0]["found"]
    assert found["endpoint_id"] == env["eid"] and not found["new"]


async def test_discovery_settings_are_validated(env):
    for values in (
        {"discovery_hosts": ["192.0.2.20; rm -rf"]},
        {"discovery_hosts": ["http://192.0.2.20"]},
        {"discovery_port_start": 8100, "discovery_port_end": 8000},
        {"discovery_port_start": 1, "discovery_port_end": 2000},
    ):
        assert (await configure(env, **values)).status_code == 422, values
    saved = await configure(env, discovery_hosts=[" 192.0.2.20 ", "192.0.2.20", "gpu-a.example"])
    assert saved.json()["discovery_hosts"] == ["192.0.2.20", "gpu-a.example"]


async def test_discovery_requires_admin(env):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://testserver"
    ) as public:
        assert (await public.post("/api/admin/discovery/scan")).status_code in (401, 403)
        assert (await public.get("/api/admin/discovery")).status_code in (401, 403)


async def test_open_access_without_login(env):
    env["app"].state.settings.open_access = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://testserver"
    ) as anyone:
        assert (await anyone.get("/api/admin/endpoints")).status_code == 200
        assert (await anyone.get("/api/auth/session")).json()["open"] is True
        assert (await anyone.post("/api/admin/discovery/scan")).status_code == 200
        # Writes from another site are still refused.
        cross = await anyone.post("/api/admin/discovery/scan", headers={"origin": "http://evil.example"})
        assert cross.status_code == 403


async def test_scan_marks_stopped_llm_without_extra_request(env):
    discovery = env["app"].state.discovery
    running = {8000, 8002}
    hits = []

    def handler(request):
        hits.append((request.url.port, request.url.path))
        if request.url.host == "192.0.2.20" and request.url.port in running:
            return httpx.Response(200, json={"data": [{"id": f"model-{request.url.port}"}]})
        raise httpx.ConnectError("refused", request=request)

    await discovery.client.aclose()
    discovery.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await configure(env, discovery_hosts=["192.0.2.20"], discovery_port_start=8000, discovery_port_end=8010)
    await env["client"].post("/api/admin/discovery/scan")
    # One /v1/models per port and nothing more from the scan itself.
    assert sorted(hits) == [(port, "/v1/models") for port in range(8000, 8011)]

    running.discard(8002)
    await env["client"].post("/api/admin/discovery/scan")
    endpoints = {e["name"]: e for e in (await env["client"].get("/api/admin/endpoints")).json()}
    assert endpoints["192.0.2.20:8000"]["health"] == "healthy"
    assert endpoints["192.0.2.20:8002"]["health"] == "unhealthy"
    assert endpoints["192.0.2.20:8002"]["observed_models"] == []
