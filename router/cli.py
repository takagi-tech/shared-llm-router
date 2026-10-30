import argparse
import asyncio
import getpass
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import uvicorn

from .config import Settings
from .db import Database
from .security import SecretBox, digest, hash_password, new_key


async def command(args, settings):
    box = SecretBox(settings.data_dir, settings.encryption_key)
    db = Database(settings.database_path)
    await db.open()
    try:
        if args.action == "admin":
            password = os.environ.get("LLM_ROUTER_INITIAL_ADMIN_PASSWORD") or getpass.getpass(
                "管理者パスワード (12文字以上): "
            )
            hashed = hash_password(password)
            existing = await db.one("SELECT id FROM admins WHERE username=?", (args.username,))
            if existing:
                await db.execute("UPDATE admins SET password_hash=? WHERE id=?", (hashed, existing["id"]))
                await db.execute("DELETE FROM sessions WHERE admin_id=?", (existing["id"],))
            else:
                await db.execute(
                    "INSERT INTO admins VALUES(?,?,?,?)",
                    (str(uuid.uuid4()), args.username, hashed, time.time()),
                )
            print(f"管理者 {args.username} を設定しました")
        elif args.action == "register":
            from .schemas import clean_url

            now = time.time()
            server = await db.one("SELECT id FROM servers WHERE name=?", (args.server,))
            sid = server["id"] if server else str(uuid.uuid4())
            if not server:
                await db.execute(
                    "INSERT INTO servers(id,name,hostname,gpu_notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (sid, args.server, args.hostname, args.gpu, now, now),
                )
            if await db.one("SELECT id FROM endpoints WHERE name=?", (args.name,)):
                raise ValueError("接続先は登録済みです。更新は管理画面から行ってください")
            key = os.environ.get("LLM_ROUTER_UPSTREAM_API_KEY")
            if key is None:
                key = getpass.getpass("上流API Key (認証不要なら空欄): ")
            eid = str(uuid.uuid4())
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO endpoints(id,server_id,name,base_url,credential,enabled,capacity,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,1,?,?,?)",
                    (
                        eid,
                        sid,
                        args.name,
                        clean_url(args.url, api=True),
                        box.encrypt(key),
                        args.capacity,
                        now,
                        now,
                    ),
                )
                await conn.execute(
                    "INSERT INTO aliases VALUES(?,?,?,?,1,?)",
                    (str(uuid.uuid4()), args.alias, eid, args.model, now),
                )
            print(
                json.dumps(
                    {"endpoint_id": eid, "alias": args.alias, "capacity": args.capacity}, ensure_ascii=False
                )
            )
        elif args.action == "key":
            user = await db.one("SELECT id FROM users WHERE name=? AND system=0", (args.user,))
            uid = user["id"] if user else str(uuid.uuid4())
            if not user:
                await db.execute(
                    "INSERT INTO users(id,name,created_at) VALUES(?,?,?)", (uid, args.user, time.time())
                )
            secret = new_key()
            await db.execute(
                "INSERT INTO api_keys(id,user_id,name,key_hash,prefix,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), uid, args.name, digest(secret), secret[:12], time.time()),
            )
            # One-time intentional delivery to the user, never logged by the application.
            print(secret)
        elif args.action == "backup":
            target = Path(args.output)
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True, mode=0o700)
            if (target / "router.sqlite3").exists():
                raise ValueError("出力先にバックアップが存在します")
            import sqlite3

            backup = sqlite3.connect(target / "router.sqlite3")
            await db.connection.backup(backup)
            backup.close()
            os.chmod(target / "router.sqlite3", 0o600)
            if not settings.encryption_key:
                fd = os.open(target / "encryption.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write((settings.data_dir / "encryption.key").read_bytes())
            for asset in ("datasets", "tokenizers"):
                source = settings.data_dir / asset
                if await asyncio.to_thread(source.exists):
                    await asyncio.to_thread(shutil.copytree, source, target / asset)
            print("DB・暗号鍵・登録データのバックアップ完了。環境変数の暗号鍵は別途保管してください")
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description="LLM Router")
    sub = parser.add_subparsers(dest="action", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    hardware = sub.add_parser("hardware-exporter", help="このホストのGPU・RAM指標をlocalhostで公開")
    hardware.add_argument("--port", type=int, default=18082)
    admin = sub.add_parser("admin", help="管理者を作成／パスワードを更新")
    admin.add_argument("--username", default="admin")
    register = sub.add_parser("register", help="初期Endpointを登録（起動前に実行）")
    register.add_argument("--server", required=True)
    register.add_argument("--hostname", required=True)
    register.add_argument("--gpu", default="")
    register.add_argument("--name", required=True)
    register.add_argument("--url", required=True)
    register.add_argument("--model", required=True)
    register.add_argument("--alias", required=True)
    register.add_argument("--capacity", type=int, default=1)
    key = sub.add_parser("key", help="利用者キーを発行（平文は一度だけ表示）")
    key.add_argument("--user", required=True)
    key.add_argument("--name", default="default")
    backup = sub.add_parser("backup")
    backup.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = Settings()
    if args.action == "hardware-exporter":
        uvicorn.run("router.hardware:app", host="127.0.0.1", port=args.port, access_log=False)
    elif args.action == "serve":
        uvicorn.run(
            "router.app:app",
            host=args.host or settings.host,
            port=args.port or settings.port,
            workers=1,
            access_log=False,
            timeout_graceful_shutdown=10,
        )
    else:
        try:
            asyncio.run(command(args, settings))
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
