import ipaddress
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from .monitor import upstream_load

router = APIRouter(prefix="/api/public", tags=["public"])


def host_key(server):
    # Numeric IP order (192.0.2.9 before 192.0.2.20); names without an IP come last.
    try:
        return (0, int(ipaddress.ip_address(server["hostname"])), "")
    except ValueError:
        return (1, 0, server["name"])


def port_key(row):
    return urlsplit(row["base_url"]).port or 0


def endpoint_status(row, service):
    if not row["enabled"]:
        return "disabled"
    if row["recovery_required"]:
        return "recovery"
    if service.maintenance.get(row["id"]):
        return "maintenance"
    return row["health"]


@router.get("/status")
async def status(request: Request):
    """Read-only view for lab members without login.

    Only names, state, models, load and hardware gauges are returned. Upstream URLs,
    credentials, error details, users, keys and request history stay behind admin login.
    """
    db, service = request.app.state.db, request.app.state.service
    servers = await db.all("SELECT id,name,hostname,hardware_metrics FROM servers")
    endpoints = await db.all(
        "SELECT id,server_id,name,base_url,backend,enabled,recovery_required,health,capacity,"
        "observed_models,native_metrics,last_checked_at FROM endpoints WHERE archived=0 ORDER BY name"
    )
    aliases = await db.all("SELECT alias,endpoint_id FROM aliases WHERE enabled=1 ORDER BY alias")
    result = []
    endpoints.sort(key=port_key)
    for server in sorted(servers, key=host_key):
        hardware = json.loads(server["hardware_metrics"])
        items = []
        for row in endpoints:
            if row["server_id"] != server["id"]:
                continue
            items.append(
                {
                    "name": row["name"],
                    "backend": row["backend"],
                    "status": endpoint_status(row, service),
                    "models": [m["id"] for m in json.loads(row["observed_models"])],
                    "aliases": [a["alias"] for a in aliases if a["endpoint_id"] == row["id"]],
                    **upstream_load(row),
                    "last_checked_at": row["last_checked_at"],
                }
            )
        result.append(
            {
                "name": server["name"],
                "hardware": {
                    "observed_at": hardware.get("observed_at"),
                    "gauges": hardware.get("gauges", {}),
                },
                "endpoints": items,
            }
        )
    return {"servers": result}
