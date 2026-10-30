from argparse import Namespace

import httpx
from openai import AsyncOpenAI

from router.cli import command
from router.config import Settings
from router.db import Database
from router.security import SecretBox


async def test_backup_restores_credentials_and_registry(env, tmp_path):
    app, ids = env["app"], env
    destination = tmp_path / "backup"
    settings = Settings(data_dir=app.state.db.path.parent, testing=True)
    for asset in ("datasets", "tokenizers"):
        folder = settings.data_dir / asset
        folder.mkdir(exist_ok=True)
        (folder / "fixture.json").write_text("{}")
    await command(Namespace(action="backup", output=str(destination)), settings)
    for asset in ("datasets", "tokenizers"):
        assert (destination / asset / "fixture.json").read_text() == "{}"
    restored = Database(destination / "router.sqlite3")
    await restored.open()
    try:
        assert (await restored.one("PRAGMA integrity_check"))["integrity_check"] == "ok"
        endpoint = await restored.one("SELECT * FROM endpoints WHERE id=?", (ids["eid"],))
        assert SecretBox(destination).decrypt(endpoint["credential"]) == "upstream-secret"
        assert (await restored.one("SELECT alias FROM aliases WHERE endpoint_id=?", (ids["eid"],)))[
            "alias"
        ] == "model"
        assert (destination / "router.sqlite3").stat().st_mode & 0o777 == 0o600
        assert (destination / "encryption.key").stat().st_mode & 0o777 == 0o600
    finally:
        await restored.close()


async def test_openai_sdk_models_chat_and_stream(env):
    app, ids = env["app"], env
    async with AsyncOpenAI(
        api_key=ids["key"],
        base_url="http://testserver/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
    ) as sdk:
        assert [model.id for model in (await sdk.models.list()).data] == ["model"]
        completion = await sdk.chat.completions.create(
            model="model", messages=[{"role": "user", "content": "hello"}]
        )
        assert completion.choices[0].message.content == "hello"
        stream = await sdk.chat.completions.create(
            model="model", messages=[{"role": "user", "content": "hello"}], stream=True
        )
        text = "".join([chunk.choices[0].delta.content or "" async for chunk in stream if chunk.choices])
        assert text == "こんにちは world"
