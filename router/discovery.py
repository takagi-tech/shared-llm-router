import asyncio
import time
import uuid
from urllib.parse import urlsplit

import httpx

from .monitor import model_info


def detect_backend(models):
    owners = {m.get("owned_by") for m in models}
    return "llamacpp" if "llamacpp" in owners else "vllm"


def parse_models(response):
    # Only an OpenAI-style model list counts; other web apps on the same port range are ignored.
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return None
    models = [model_info(m) for m in data["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    return models or None


class Discovery:
    """Scan configured hosts for OpenAI-compatible servers and register what is found.

    Scans run only when an administrator asks (the 再検出 button), to keep the
    load on lab machines predictable; there is no periodic background scan.

    Found ports become ordinary servers/endpoints, so health checks, the public page
    and routing work unchanged. Ports that stop answering are left to the health monitor.
    """

    def __init__(self, db, service, monitor, transport=None):
        self.db, self.service, self.monitor = db, service, monitor
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(3, connect=1), trust_env=False, follow_redirects=False, transport=transport
        )
        self.lock = asyncio.Lock()
        self.last = {"running": False, "started_at": None, "completed_at": None, "hosts": []}

    async def check(self, host, port, gate):
        async with gate:
            try:
                response = await self.client.get(f"http://{host}:{port}/v1/models")
            except httpx.HTTPError:
                return None
        models = parse_models(response)
        return {"port": port, "models": models} if models else None

    async def server_for(self, host):
        row = await self.db.one(
            "SELECT id FROM servers WHERE hostname=? OR name=? ORDER BY created_at", (host, host)
        )
        if row:
            return row["id"]
        sid, now = str(uuid.uuid4()), time.time()
        await self.db.execute(
            "INSERT INTO servers(id,name,hostname,created_at,updated_at) VALUES (?,?,?,?,?)",
            (sid, host, host, now, now),
        )
        return sid

    async def register(self, host, found):
        base_url = f"http://{host}:{found['port']}/v1"
        existing = await self.db.one("SELECT id FROM endpoints WHERE base_url=? AND archived=0", (base_url,))
        if existing:
            return existing["id"], False
        sid, eid, now = await self.server_for(host), str(uuid.uuid4()), time.time()
        name = f"{host}:{found['port']}"
        if await self.db.one("SELECT id FROM endpoints WHERE name=?", (name,)):
            name = f"{name}-{eid[:8]}"
        async with self.service.registry_lock:
            await self.db.execute(
                "INSERT INTO endpoints(id,server_id,name,base_url,backend,enabled,capacity,created_at,updated_at) "
                "VALUES (?,?,?,?,?,1,1,?,?)",
                (eid, sid, name, base_url, detect_backend(found["models"]), now, now),
            )
            self.service.scheduler.queue(eid, 1)
        return eid, True

    async def scan_host(self, host, ports, concurrency, known):
        # Every monitored machine is a server, even before any LLM runs on it.
        await self.server_for(host)
        gate = asyncio.Semaphore(concurrency)
        checks = await asyncio.gather(*(self.check(host, port, gate) for port in ports))
        found, answered = [], set()
        for item in filter(None, checks):
            eid, new = await self.register(host, item)
            answered.add(eid)
            # The scan already fetched /v1/models; record it instead of asking again.
            await self.monitor.probe(eid, observed=(item["models"], None))
            found.append({**item, "endpoint_id": eid, "new": new})
        for ep in known:
            url = urlsplit(ep["base_url"])
            if url.hostname != host or ep["id"] in answered:
                continue
            if url.port in ports and not ep["credential"]:
                # Scanned without an answer: the LLM on this port is down.
                await self.monitor.probe(ep["id"], observed=([], "connection_error"))
            else:
                # Outside the range, or needs an API key the scan does not send.
                await self.monitor.probe(ep["id"])
        return {"host": host, "found": found}

    async def scan(self):
        if self.lock.locked():
            return self.last
        async with self.lock:
            settings = await self.db.settings()
            hosts = settings["discovery_hosts"]
            ports = range(settings["discovery_port_start"], settings["discovery_port_end"] + 1)
            self.last = {**self.last, "running": True, "started_at": time.time()}
            results = []
            try:
                known = await self.db.all(
                    "SELECT id,base_url,credential FROM endpoints WHERE archived=0 AND enabled=1"
                )
                # Machines are scanned at the same time, so one that is switched off (each port
                # waits for the connect timeout) does not delay the others.
                results = await asyncio.gather(
                    *(self.scan_host(h, ports, settings["discovery_concurrency"], known) for h in hosts)
                )
                # Endpoints registered by hand on machines that are not in the list.
                others = [ep["id"] for ep in known if urlsplit(ep["base_url"]).hostname not in hosts]
                await asyncio.gather(*(self.monitor.probe(eid) for eid in others))
                await self.monitor.collect_servers()
            finally:
                self.last = {
                    "running": False,
                    "started_at": self.last["started_at"],
                    "completed_at": time.time(),
                    "hosts": list(results),
                }
            return self.last

    async def close(self):
        await self.client.aclose()
