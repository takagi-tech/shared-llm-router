import json
import math
import time

from .db import Database

# Histograms merge without averaging percentiles. Bounds are milliseconds.
BOUNDS = [
    1,
    2,
    5,
    10,
    20,
    50,
    100,
    200,
    500,
    1000,
    2000,
    5000,
    10000,
    20000,
    50000,
    100000,
    200000,
    500000,
    1000000,
    2000000,
    5000000,
    10000000,
    50000000,
    100000000,
]
FIELDS = ("queue_ms", "ttft_ms", "e2e_ms")


def blank():
    return {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "interrupted": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "input_known": 0,
        "output_known": 0,
        **{field: {"count": 0, "sum": 0, "hist": [0] * (len(BOUNDS) + 1)} for field in FIELDS},
    }


def add_sample(data: dict, sample: dict):
    data["total"] += 1
    status = sample["status"]
    if status in ("completed", "failed", "cancelled", "interrupted"):
        data[status] += 1
    for side in ("input", "output"):
        value = sample.get(side + "_tokens")
        if value is not None:
            data[side + "_tokens"] += value
            data[side + "_known"] += 1
    # Latency distribution describes successful requests. Failures remain in counts.
    if status != "completed":
        return
    for field in FIELDS:
        value = sample.get(field)
        if value is None:
            continue
        item = data[field]
        item["count"] += 1
        item["sum"] += value
        bucket = next((i for i, bound in enumerate(BOUNDS) if value <= bound), len(BOUNDS))
        item["hist"][bucket] += 1


def merge(target: dict, source: dict):
    for key, value in source.items():
        if key in FIELDS:
            target[key]["count"] += value["count"]
            target[key]["sum"] += value["sum"]
            target[key]["hist"] = [a + b for a, b in zip(target[key]["hist"], value["hist"], strict=True)]
        else:
            target[key] += value


def percentile(hist: list[int], q: float):
    count = sum(hist)
    if not count:
        return None
    wanted = math.ceil(count * q)
    seen = 0
    for i, n in enumerate(hist):
        seen += n
        if seen >= wanted:
            # An overflow value is unknown, not a fictitious finite latency.
            return BOUNDS[i] if i < len(BOUNDS) else None
    return None


def describe(data: dict) -> dict:
    result = {key: value for key, value in data.items() if key not in FIELDS}
    for field in FIELDS:
        item = data[field]
        result[field] = {
            "count": item["count"],
            "mean": item["sum"] / item["count"] if item["count"] else None,
            "p50": percentile(item["hist"], 0.50),
            "p95": percentile(item["hist"], 0.95),
            "p99": percentile(item["hist"], 0.99),
            "overflow": item["hist"][-1],
        }
    result["percentiles_approximate"] = True
    return result


class Metrics:
    def __init__(self, db: Database):
        self.db = db

    async def finish(self, request_id: str, values: dict):
        async with self.db.transaction() as conn:
            cur = await conn.execute("SELECT * FROM requests WHERE id=?", (request_id,))
            row = dict(await cur.fetchone())
            if row["completed_at"] is not None:
                return
            keys = list(values)
            await conn.execute(
                "UPDATE requests SET " + ",".join(f"{k}=?" for k in keys) + " WHERE id=?",
                (*[values[k] for k in keys], request_id),
            )
            row.update(values)
            # Bucket by completion time: long-running requests are not backfilled into expired buckets.
            bucket = int(row["completed_at"] // 60) * 60
            dims = (bucket, row["endpoint_id"], row["user_id"], row["alias"], row["source"])
            cur = await conn.execute(
                "SELECT data FROM metric_minutes WHERE bucket=? AND endpoint_id=? AND user_id=? "
                "AND alias=? AND source=?",
                dims,
            )
            previous = await cur.fetchone()
            data = json.loads(previous["data"]) if previous else blank()
            add_sample(data, row)
            await conn.execute(
                "INSERT OR REPLACE INTO metric_minutes VALUES (?,?,?,?,?,?)", (*dims, json.dumps(data))
            )

    async def query(self, since: float, until: float, endpoint_id=None, user_id=None, source="normal"):
        clauses, params = ["bucket>=?", "bucket<=?", "source=?"], [int(since // 60) * 60, until, source]
        for field, value in (("endpoint_id", endpoint_id), ("user_id", user_id)):
            if value:
                clauses.append(field + "=?")
                params.append(value)
        rows = await self.db.all("SELECT * FROM metric_minutes WHERE " + " AND ".join(clauses), params)
        total = blank()
        buckets, endpoints, users = {}, {}, {}
        for row in rows:
            data = json.loads(row["data"])
            merge(total, data)
            for group, key in (
                (buckets, row["bucket"]),
                (endpoints, row["endpoint_id"]),
                (users, row["user_id"]),
            ):
                merge(group.setdefault(key, blank()), data)
        # Limit chart resolution without throwing away totals or merging percentiles incorrectly.
        step = max(60, math.ceil((until - since) / 120 / 60) * 60)
        chart = {}
        for bucket, value in buckets.items():
            merge(chart.setdefault(int(bucket // step) * step, blank()), value)
        return {
            "since": since,
            "until": until,
            "bucket_seconds": step,
            "summary": describe(total),
            "series": [{"time": k, **describe(v)} for k, v in sorted(chart.items())],
            "endpoints": [{"id": k, **describe(v)} for k, v in endpoints.items()],
            "users": [{"id": k, **describe(v)} for k, v in users.items()],
        }

    async def retain(self):
        settings = await self.db.settings()
        now = time.time()
        await self.db.execute(
            "DELETE FROM requests WHERE completed_at IS NOT NULL AND completed_at<? AND benchmark_id IS NULL",
            (now - settings["request_retention_days"] * 86400,),
        )
        # Benchmark summaries are durable, individual observations follow ordinary retention.
        await self.db.execute(
            "DELETE FROM requests WHERE completed_at IS NOT NULL AND completed_at<?",
            (now - settings["request_retention_days"] * 86400,),
        )
        await self.db.execute(
            "DELETE FROM metric_minutes WHERE bucket<?", (now - settings["metric_retention_days"] * 86400,)
        )
        await self.db.execute(
            "DELETE FROM health_samples WHERE observed_at<?",
            (now - settings["metric_retention_days"] * 86400,),
        )
        await self.db.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
