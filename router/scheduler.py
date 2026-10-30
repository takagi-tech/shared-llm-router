import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    user_id: str
    endpoint: dict
    alias: str
    payload: dict
    source: str = "normal"
    benchmark_id: str | None = None
    direct: bool = False
    requested_at: float = field(default_factory=time.time)
    requested_mono: float = field(default_factory=time.monotonic)
    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float | None = None
    started_mono: float | None = None
    reason: str | None = None
    cancel_status: str = "cancelled"
    status: str = "queued"
    http_status: int | None = None
    headers_sent: bool = False
    finished: bool = False
    observer: object | None = None

    def cancel(self, reason="cancelled", status="cancelled"):
        if not self.finished and not self.cancelled.is_set():
            self.reason = reason
            self.cancel_status = status
            self.cancelled.set()


class EndpointQueue:
    """All methods run synchronously on the one scheduler event loop.

    No await occurs while mutating queues, so dispatch/cancel/release are atomic.
    Running slots are released only after upstream cleanup has finished.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.users: dict[str, deque[Job]] = {}
        self.turns: deque[str] = deque()
        self.running: dict[str, Job] = {}

    def enqueue(self, job: Job):
        if job.user_id not in self.users:
            self.users[job.user_id] = deque()
            self.turns.append(job.user_id)
        self.users[job.user_id].append(job)
        self.dispatch()

    def dispatch(self):
        while self.turns and len(self.running) < self.capacity:
            user = self.turns.popleft()
            queue = self.users[user]
            job = queue.popleft()
            if queue:
                self.turns.append(user)
            else:
                del self.users[user]
            if job.cancelled.is_set() or job.finished:
                continue
            self.running[job.id] = job
            job.started_at = time.time()
            job.started_mono = time.monotonic()
            job.status = "running"
            job.admitted.set()

    def remove_waiting(self, job: Job):
        queue = self.users.get(job.user_id)
        if queue is not None:
            try:
                queue.remove(job)
            except ValueError:
                return
            if not queue:
                del self.users[job.user_id]
                self.turns.remove(job.user_id)

    def release(self, job: Job):
        self.remove_waiting(job)
        self.running.pop(job.id, None)
        self.dispatch()

    def resize(self, capacity: int):
        self.capacity = capacity
        self.dispatch()

    def fail_waiting(self, reason: str):
        waiting = [job for queue in self.users.values() for job in queue]
        self.users.clear()
        self.turns.clear()
        for job in waiting:
            job.cancel(reason, "failed")

    def snapshot(self):
        return {
            "running": len(self.running),
            "queued": sum(map(len, self.users.values())),
            "capacity": self.capacity,
        }


class Scheduler:
    def __init__(self):
        self.queues: dict[str, EndpointQueue] = {}

    def queue(self, endpoint_id: str, capacity: int | None = None):
        if endpoint_id not in self.queues:
            self.queues[endpoint_id] = EndpointQueue(capacity or 1)
        return self.queues[endpoint_id]

    def enqueue(self, job: Job):
        self.queue(job.endpoint["id"], job.endpoint["capacity"]).enqueue(job)

    def release(self, job: Job):
        if not job.direct:
            self.queue(job.endpoint["id"]).release(job)

    def cancel(self, job: Job, reason="cancelled", status="cancelled"):
        job.cancel(reason, status)
        self.queue(job.endpoint["id"]).remove_waiting(job)

    def snapshot(self, endpoint_id: str, capacity=1):
        return self.queue(endpoint_id, capacity).snapshot()
