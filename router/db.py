import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from .config import DEFAULT_SETTINGS


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.connection: aiosqlite.Connection | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = await aiosqlite.connect(self.path, isolation_level=None)
        self.connection.row_factory = aiosqlite.Row
        os.chmod(self.path, 0o600)
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        version = await self.one("PRAGMA user_version")
        if version["user_version"] > 3:
            raise RuntimeError("このDBは新しいアプリのバージョンで作成されています")
        if version["user_version"] == 0:
            await self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
        if version["user_version"] < 2:
            await self.connection.executescript(
                "CREATE TABLE IF NOT EXISTS benchmark_datasets("
                "id TEXT PRIMARY KEY,name TEXT NOT NULL,extension TEXT NOT NULL,"
                "sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,rows INTEGER NOT NULL,created_at REAL NOT NULL);"
                "PRAGMA user_version=2;"
            )
        if version["user_version"] < 3:
            await self.connection.executescript(
                "CREATE TABLE IF NOT EXISTS benchmark_progress("
                "benchmark_id TEXT PRIMARY KEY REFERENCES benchmarks(id) ON DELETE CASCADE,"
                "data TEXT NOT NULL DEFAULT '{}'); PRAGMA user_version=3;"
            )
        for key, value in DEFAULT_SETTINGS.items():
            await self.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    async def close(self):
        if self.connection:
            await self.connection.close()
            self.connection = None

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

    async def execute(self, sql: str, params=()):
        async with self.lock:
            cur = await self.connection.execute(sql, params)
            return cur.rowcount

    async def one(self, sql: str, params=()) -> dict | None:
        async with self.lock:
            cur = await self.connection.execute(sql, params)
            row = await cur.fetchone()
            return dict(row) if row else None

    async def all(self, sql: str, params=()) -> list[dict]:
        async with self.lock:
            cur = await self.connection.execute(sql, params)
            return [dict(row) for row in await cur.fetchall()]

    async def settings(self) -> dict:
        return {row["key"]: json.loads(row["value"]) for row in await self.all("SELECT * FROM settings")}

    async def recover(self):
        # No replay: clients no longer own their old HTTP connections after restart.
        now = time.time()
        await self.execute(
            "UPDATE endpoints SET recovery_required=1 WHERE id IN "
            "(SELECT endpoint_id FROM benchmarks WHERE status IN ('preparing','running','stopping'))"
        )
        unfinished = await self.all("SELECT * FROM requests WHERE status IN ('queued','running')")
        from .metrics import Metrics

        metrics = Metrics(self)
        for row in unfinished:
            if row["status"] == "running":
                await self.execute(
                    "UPDATE endpoints SET recovery_required=1 WHERE id=?", (row["endpoint_id"],)
                )
            await metrics.finish(
                row["id"],
                {
                    "status": "interrupted",
                    "completed_at": now,
                    "error_type": "router_restarted",
                    "e2e_ms": (now - row["requested_at"]) * 1000,
                },
            )
        await self.execute(
            "UPDATE benchmarks SET status='interrupted', completed_at=?, error_type='router_restarted' "
            "WHERE status IN ('pending','draining','preparing','running','stopping')",
            (now,),
        )
        await self.execute(
            "UPDATE benchmark_steps SET status='interrupted', completed_at=? WHERE status='running'",
            (now,),
        )
        await self.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
