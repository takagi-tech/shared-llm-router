import pytest

from router.monitor import model_info, parse_gauges


def test_llamacpp_context_and_native_metrics():
    assert model_info({"id": "gemma", "meta": {"n_ctx": 16384, "n_ctx_train": 262144}}) == {
        "id": "gemma",
        "max_model_len": 16384,
    }
    assert model_info({"id": "gemma", "meta": None}) == {"id": "gemma"}
    assert (
        parse_gauges("llamacpp:requests_processing 1\nllamacpp:predicted_tokens_seconds 22.5")[
            "llamacpp:requests_processing"
        ][0]["value"]
        == 1
    )


@pytest.mark.parametrize("stream", [False, True])
async def test_llamacpp_registration_and_forwarding(env, stream):
    c = env["client"]
    r = await c.put(
        f"/api/admin/endpoints/{env['eid']}",
        json={
            "server_id": env["sid"],
            "name": "test-endpoint",
            "base_url": "http://upstream:8000/v1",
            "backend": "llamacpp",
            "enabled": True,
            "capacity": 1,
        },
    )
    assert r.status_code == 200
    await c.post(f"/api/admin/endpoints/{env['eid']}/probe")
    payload = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
        "tools": [{"type": "function", "function": {"name": "report", "parameters": {}}}],
    }
    r = await c.post("/v1/chat/completions", json=payload)
    assert r.status_code == 200
    assert env["upstream"].requests[-1][1] == {**payload, "model": "upstream-model"}
    if stream:
        assert "tool_calls" in r.text and "[DONE]" in r.text
