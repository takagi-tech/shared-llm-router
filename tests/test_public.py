import httpx


async def public_client(env):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env["app"]), base_url="http://testserver")


async def test_public_status_without_login(env):
    async with await public_client(env) as public:
        response = await public.get("/api/public/status")
        assert response.status_code == 200
        (server,) = response.json()["servers"]
        assert server["name"] == "dev"
        (endpoint,) = server["endpoints"]
        assert endpoint["name"] == "test-endpoint"
        assert endpoint["status"] == "healthy"
        assert endpoint["models"] == ["upstream-model"]
        assert endpoint["aliases"] == ["model"]
        # Load comes from the inference server, so direct use (not via the Router) is counted too.
        assert (endpoint["running"], endpoint["queued"]) == (2, 5)


async def test_public_status_hides_secrets_and_usage(env):
    async with await public_client(env) as public:
        body = (await public.get("/api/public/status")).text
        for hidden in (
            "upstream-secret",
            "http://upstream:8000",
            "credential",
            "base_url",
            "last_error",
            "localhost",
            "Researcher",
            env["key"],
            env["uid"],
        ):
            assert hidden not in body


async def test_public_status_is_read_only(env):
    async with await public_client(env) as public:
        assert (await public.get("/api/admin/endpoints")).status_code in (401, 403)
        assert (await public.post("/api/public/status")).status_code == 405
        disabled = await env["client"].put(
            f"/api/admin/endpoints/{env['eid']}",
            json={
                "server_id": env["sid"],
                "name": "test-endpoint",
                "base_url": "http://upstream:8000/v1",
                "enabled": False,
            },
        )
        assert disabled.status_code == 200, disabled.text
        (server,) = (await public.get("/api/public/status")).json()["servers"]
        assert server["endpoints"][0]["status"] == "disabled"
        assert (server["endpoints"][0]["running"], server["endpoints"][0]["queued"]) == (None, None)
