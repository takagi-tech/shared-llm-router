"""Small, explicitly bounded integration checks through the running Router.

No credential values or inference payloads are printed or written into results.
The time budget here belongs to the test client, not to the Router.
"""

import asyncio
import json
from pathlib import Path

import httpx


async def main():
    admin = json.loads(await asyncio.to_thread(Path(".local/initial-admin.json").read_text))
    results = []
    async with httpx.AsyncClient(base_url=admin["url"], timeout=120, trust_env=False) as client:
        login = await client.post("/api/auth/login", json={k: admin[k] for k in ("username", "password")})
        login.raise_for_status()
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        endpoints = (await client.get("/api/admin/endpoints")).json()
        target = next(e for e in endpoints if e["name"] == "dev-qwen")
        probe = await client.post(f"/api/admin/endpoints/{target['id']}/probe")
        results.append(
            {"check": "health", "ok": probe.json().get("probe_ok"), "health": probe.json().get("health")}
        )
        user = (await client.post("/api/admin/users", json={"name": "開発検証"})).json()
        key_response = await client.post(
            f"/api/admin/users/{user['id']}/keys", json={"name": "temporary-smoke"}
        )
        key_response.raise_for_status()
        key = key_response.json()
        client.headers["Authorization"] = "Bearer " + key["api_key"]
        try:
            listed = (await client.get("/v1/models")).json()
            results.append({"check": "models", "aliases": [m["id"] for m in listed["data"]]})
            simple = {
                "model": "qwen-main",
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 64,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            response = await client.post("/v1/chat/completions", json=simple)
            results.append(
                {
                    "check": "nonstream",
                    "status": response.status_code,
                    "has_choices": bool(response.json().get("choices")),
                    "request_id": response.headers.get("x-request-id"),
                }
            )
            response = await client.post(
                "/v1/chat/completions",
                json={**simple, "stream": True, "stream_options": {"include_usage": True}},
            )
            results.append(
                {
                    "check": "stream",
                    "status": response.status_code,
                    "done": "[DONE]" in response.text,
                    "request_id": response.headers.get("x-request-id"),
                }
            )
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_temperature",
                        "description": "Look up a city's temperature.",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ]
            tool_payload = {
                "model": "qwen-main",
                "messages": [{"role": "user", "content": "Use lookup_temperature for Tokyo."}],
                "tools": tools,
                "tool_choice": "auto",
                "max_tokens": 256,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            response = await client.post("/v1/chat/completions", json=tool_payload)
            body = response.json()
            calls = body.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
            results.append(
                {
                    "check": "tool_calling_auto",
                    "status": response.status_code,
                    "tool_calls": len(calls),
                    "request_id": response.headers.get("x-request-id"),
                    "error_type": (body.get("error") or {}).get("type"),
                }
            )
            if calls:
                follow = {
                    **tool_payload,
                    "tool_choice": "none",
                    "messages": [
                        *tool_payload["messages"],
                        body["choices"][0]["message"],
                        *[
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": '{"temperature": 24, "unit": "C", "source": "synthetic test"}',
                            }
                            for call in calls
                        ],
                    ],
                }
                follow_response = await client.post("/v1/chat/completions", json=follow)
                results.append(
                    {
                        "check": "tool_result_roundtrip",
                        "status": follow_response.status_code,
                        "has_choices": bool(follow_response.json().get("choices")),
                    }
                )
            streamed = await client.post(
                "/v1/chat/completions",
                json={**tool_payload, "stream": True, "stream_options": {"include_usage": True}},
            )
            results.append(
                {
                    "check": "tool_calling_stream",
                    "status": streamed.status_code,
                    "has_tool_delta": '"tool_calls"' in streamed.text,
                    "done": "[DONE]" in streamed.text,
                }
            )
        finally:
            await client.delete(f"/api/admin/keys/{key['id']}")
    await asyncio.to_thread(
        Path(".local/smoke-result.json").write_text, json.dumps(results, ensure_ascii=False, indent=2)
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
