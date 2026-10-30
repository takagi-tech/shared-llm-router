from router.scheduler import EndpointQueue, Job


def job(name, user):
    return Job(name, user, {"id": "ep"}, "alias", {})


def test_user_fifo_round_robin_and_no_bypass():
    queue = EndpointQueue(1)
    first = job("running", "A")
    queue.enqueue(first)
    waiting = [job("a1", "A"), job("a2", "A"), job("b1", "B"), job("b2", "B"), job("c1", "C")]
    for item in waiting:
        queue.enqueue(item)
    order = []
    while queue.running:
        running = next(iter(queue.running.values()))
        order.append(running.id)
        queue.release(running)
    assert order == ["running", "a1", "b1", "c1", "a2", "b2"]


def test_cancellation_and_capacity_reduction():
    queue = EndpointQueue(2)
    a, b, c, d = [job(x, x) for x in "abcd"]
    for item in (a, b, c, d):
        queue.enqueue(item)
    queue.resize(1)
    assert len(queue.running) == 2  # Running jobs are not preempted.
    c.cancel()
    queue.remove_waiting(c)
    queue.release(a)
    assert not d.admitted.is_set()
    queue.release(b)
    assert d.admitted.is_set()
    queue.release(d)
    queue.release(d)
    assert queue.snapshot() == {"running": 0, "queued": 0, "capacity": 1}


def test_fail_waiting_never_releases_running_slot():
    queue = EndpointQueue(1)
    a, b = job("a", "A"), job("b", "B")
    queue.enqueue(a)
    queue.enqueue(b)
    queue.fail_waiting("offline")
    assert b.cancelled.is_set() and not b.admitted.is_set()
    assert queue.snapshot()["running"] == 1
