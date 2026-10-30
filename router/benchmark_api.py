import asyncio
import csv
import hashlib
import html
import io
import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from .auth import require_admin
from .engines import TrialInput, catalog
from .proxy import RouterError

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/benchmark-engines")
async def engines():
    return catalog()


@router.get("/benchmark-datasets")
async def datasets(request: Request):
    return await request.app.state.db.all("SELECT * FROM benchmark_datasets ORDER BY created_at DESC")


@router.post("/benchmark-datasets", status_code=201)
async def upload(request: Request, name: str = Query(max_length=100)):
    ext = Path(name).suffix.lower()
    if ext not in (".json", ".jsonl", ".csv"):
        raise RouterError(422, "unsupported_format", "JSON、JSONL、CSVに対応します")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 20 * 1024 * 1024:
            raise RouterError(413, "dataset_too_large", "データは20MB以下にしてください")
    try:
        text = content.decode("utf-8-sig")
        rows = (
            list(csv.DictReader(io.StringIO(text)))
            if ext == ".csv"
            else [json.loads(line) for line in text.splitlines() if line.strip()]
            if ext == ".jsonl"
            else json.loads(text)
        )
        if (
            not isinstance(rows, list)
            or not rows
            or len(rows) > 100000
            or any(not isinstance(r, dict) for r in rows)
        ):
            raise ValueError()
    except (ValueError, UnicodeError, csv.Error):
        raise RouterError(
            422, "invalid_dataset", "1〜100000件のオブジェクト行を含むUTF-8データが必要です"
        ) from None
    did = str(uuid.uuid4())
    directory = request.app.state.settings.data_dir / "datasets"
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((directory / (did + ext)).write_bytes, content)
    await request.app.state.db.execute(
        "INSERT INTO benchmark_datasets VALUES(?,?,?,?,?,?,?)",
        (did, name, ext, hashlib.sha256(content).hexdigest(), len(content), len(rows), time.time()),
    )
    return {
        "id": did,
        "name": name,
        "rows": len(rows),
        "columns": sorted(set().union(*(r.keys() for r in rows[:100]))),
    }


@router.delete("/benchmark-datasets/{did}")
async def delete_dataset(did: str, request: Request):
    row = await request.app.state.db.one("SELECT * FROM benchmark_datasets WHERE id=?", (did,))
    if not row:
        raise RouterError(404, "dataset_missing", "データがありません")
    for task_id in request.app.state.benchmarks.tasks:
        trial = await request.app.state.db.one("SELECT config FROM benchmarks WHERE id=?", (task_id,))
        if json.loads(trial["config"]).get("options", {}).get("dataset_id") == did:
            raise RouterError(409, "dataset_in_use", "実行中の試験で使用しています")
    await asyncio.to_thread(
        (request.app.state.settings.data_dir / "datasets" / (did + row["extension"])).unlink, missing_ok=True
    )
    await request.app.state.db.execute("DELETE FROM benchmark_datasets WHERE id=?", (did,))
    return {"ok": True}


@router.get("/benchmarks/{bid}/export/{format}")
async def export(bid: str, format: str, request: Request):
    from .admin import benchmark_detail

    record = await benchmark_detail(bid, request)
    if format == "json":
        content, mime = json.dumps(record, ensure_ascii=False, indent=2), "application/json"
    elif format in ("csv", "html"):
        fields = [
            "step",
            "status",
            "total",
            "completed",
            "failed",
            "cancelled",
            "duration_seconds",
            "request_throughput",
            "input_tokens",
            "output_tokens",
            "output_token_throughput",
            "ttft_p50_ms",
            "ttft_p95_ms",
            "e2e_p95_ms",
            "tpot_p95_ms",
            "itl_p95_ms",
        ]
        rows = []
        for s in record["steps"]:
            r = s["results"]
            values = {**r, "step": s["step"], "status": s["status"]}
            for metric, percentile in (
                ("ttft", "p50"),
                ("ttft", "p95"),
                ("e2e", "p95"),
                ("tpot", "p95"),
                ("itl", "p95"),
            ):
                values[f"{metric}_{percentile}_ms"] = (r.get(metric + "_ms") or {}).get(percentile)
            rows.append([values.get(f) for f in fields])
        if format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(fields)
            writer.writerows(rows)
            content, mime = output.getvalue(), "text/csv"
        else:

            def cells(values, tag):
                return (
                    "<tr>"
                    + "".join(
                        f"<{tag}>{html.escape(str(v) if v is not None else '—')}</{tag}>" for v in values
                    )
                    + "</tr>"
                )

            content = (
                '<!doctype html><meta charset="utf-8"><title>LLM Router benchmark</title><h1>LLM Router benchmark</h1><pre>'
                + html.escape(json.dumps(record["config"], ensure_ascii=False, indent=2))
                + "</pre><table border=1>"
                + cells(fields, "th")
                + "".join(cells(r, "td") for r in rows)
                + "</table>"
            )
            mime = "text/html"
    else:
        raise RouterError(404, "format_not_found", "JSON、CSV、HTMLに対応します")
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="benchmark-{uuid.UUID(bid)}.{format}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/benchmarks/{bid}/resources")
async def resources(bid: str, request: Request):
    record = await request.app.state.db.one(
        "SELECT b.*,e.server_id FROM benchmarks b JOIN endpoints e ON e.id=b.endpoint_id WHERE b.id=?", (bid,)
    )
    if not record:
        raise RouterError(404, "benchmark_missing", "試験がありません")
    rows = await request.app.state.db.all(
        "SELECT kind,observed_at,data FROM resource_samples WHERE observed_at BETWEEN ? AND ? AND ((kind='server' AND target_id=?) OR (kind='endpoint' AND target_id=?)) ORDER BY observed_at",
        (
            record["started_at"] or record["created_at"],
            record["completed_at"] or time.time(),
            record["server_id"],
            record["endpoint_id"],
        ),
    )
    return [{"kind": r["kind"], "time": r["observed_at"], **json.loads(r["data"])} for r in rows]


@router.post("/benchmark-previews", status_code=201)
async def preview(data: TrialInput, request: Request):
    return await request.app.state.benchmarks.preview(data)


@router.get("/benchmark-previews/{pid}")
async def preview_status(pid: str, request: Request):
    return await request.app.state.benchmarks.preview_status(pid)


@router.post("/benchmark-config/validate")
async def validate_config(data: TrialInput):
    return data.model_dump()


@router.get("/benchmark-tokenizers")
async def tokenizers(request: Request):
    directory = request.app.state.settings.data_dir / "tokenizers"

    def read():
        return [json.loads(p.read_text()) for p in directory.glob("*/manifest.json")]

    return await asyncio.to_thread(read)


@router.post("/benchmark-tokenizers", status_code=201)
async def upload_tokenizer(request: Request, name: str = Query(min_length=1, max_length=100)):
    import zipfile

    allowed = {
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "merges.txt",
        "chat_template.jinja",
    }
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 40 * 1024 * 1024:
            raise RouterError(413, "tokenizer_too_large", "Tokenizerは40MB以下にしてください")
    tid = str(uuid.uuid4())
    directory = request.app.state.settings.data_dir / "tokenizers" / tid

    def unpack():
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if (
                    not members
                    or any(m.filename not in allowed for m in members)
                    or len({m.filename for m in members}) != len(members)
                    or sum(m.file_size for m in members) > 40 * 1024 * 1024
                ):
                    raise ValueError()
                if "tokenizer.json" not in archive.namelist():
                    raise ValueError()
                files = {m.filename: archive.read(m) for m in members}
                json.loads(files["tokenizer.json"])
                directory.mkdir(parents=True)
                for filename, data in files.items():
                    (directory / filename).write_bytes(data)
                manifest = {
                    "id": tid,
                    "name": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "created_at": time.time(),
                    "files": {filename: hashlib.sha256(data).hexdigest() for filename, data in files.items()},
                }
                (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
                return manifest
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError):
            raise RouterError(
                422,
                "invalid_tokenizer",
                "Tokenizerファイルを直下に含むZIPを指定してください。tokenizer.jsonが必要です",
            ) from None

    return await asyncio.to_thread(unpack)
