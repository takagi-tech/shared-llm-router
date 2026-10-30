"""Optional, loopback-only host exporter; no GPU management or remote commands."""

import asyncio
import csv
import math
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

GPU_FIELDS = [
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_POWER_USAGE",
]


def gpu_metrics(output):
    lines = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if len(row) != 6 or not row[0].isdigit():
            continue
        for metric, raw in zip(GPU_FIELDS, row[1:], strict=True):
            try:
                value = float(raw)
            except ValueError:
                continue
            if math.isfinite(value):
                lines.append(f'{metric}{{gpu="{row[0]}"}} {value}')
    return lines


def host_metrics():
    lines = [f"node_load1 {os.getloadavg()[0]}", f"llm_router_cpu_count {os.cpu_count() or 1}"]
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in ("MemTotal", "MemAvailable"):
            lines.append(f"node_memory_{name}_bytes {int(value.split()[0]) * 1024}")
    return lines


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    lines = await asyncio.to_thread(host_metrics)
    try:
        process = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,utilization.gpu,memory.used,memory.free,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=3)
            lines += gpu_metrics(output.decode()) if process.returncode == 0 else []
            lines.append(f"llm_router_gpu_scrape_success {int(process.returncode == 0)}")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    except (OSError, TimeoutError):
        lines.append("llm_router_gpu_scrape_success 0")
    return "\n".join(lines) + "\n"
