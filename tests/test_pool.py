"""WorkerPool on REAL threads — capacity, backpressure, gauges, and error
propagation, with nothing stubbed. The pool is the no-loop-blocking boundary;
these tests pin that a saturated pool queues (never over-commits), that the
gauges the deck's pool panel reads are live, and that worker exceptions reach
the awaiting caller instead of vanishing in a thread."""
from __future__ import annotations

import threading
import time

import anyio
import pytest

from utah.daemon.pool import WorkerPool


# -- construction ---------------------------------------------------------------

def test_rejects_a_nonpositive_limit():
    with pytest.raises(ValueError, match=">= 1"):
        WorkerPool(limit=0)
    with pytest.raises(ValueError, match="-3"):
        WorkerPool(limit=-3)


def test_limit_and_initial_gauges():
    pool = WorkerPool(limit=4)
    assert pool.limit == 4
    assert pool.borrowed == 0
    assert pool.available == 4


# -- run: results and errors ------------------------------------------------------

def test_run_returns_the_callable_result_with_args():
    pool = WorkerPool(limit=2)

    async def go():
        return await pool.run(lambda a, b: a + b, 2, 3)

    assert anyio.run(go) == 5


def test_run_really_leaves_the_event_loop_thread():
    pool = WorkerPool(limit=1)
    loop_thread = threading.current_thread()

    async def go():
        return await pool.run(threading.current_thread)

    worker_thread = anyio.run(go)
    assert worker_thread is not loop_thread  # off-loop is the pool's whole law


def test_worker_exception_propagates_to_the_awaiter():
    pool = WorkerPool(limit=1)

    def boom():
        raise RuntimeError("worker blew up")

    async def go():
        with pytest.raises(RuntimeError, match="worker blew up"):
            await pool.run(boom)
        # and the slot is returned — the pool is not poisoned by the failure
        return await pool.run(lambda: "recovered")

    assert anyio.run(go) == "recovered"


def test_run_rejects_a_non_callable_fast():
    """A non-callable must fail in the caller with a clear message, not as an
    opaque TypeError from inside an anonymous worker thread."""
    pool = WorkerPool(limit=1)

    async def go():
        with pytest.raises(TypeError, match="callable"):
            await pool.run("not-a-function")  # type: ignore[arg-type]

    anyio.run(go)


# -- capacity and backpressure ----------------------------------------------------

def test_saturated_pool_queues_instead_of_overcommitting():
    """With limit=1, two concurrent calls must serialize: the second waits for
    the first slot, so peak concurrency never exceeds the limit."""
    pool = WorkerPool(limit=1)
    running = []
    peak = []
    lock = threading.Lock()

    def work(tag: str) -> str:
        with lock:
            running.append(tag)
            peak.append(len(running))
        time.sleep(0.15)
        with lock:
            running.remove(tag)
        return tag

    async def go():
        async with anyio.create_task_group() as tg:
            tg.start_soon(pool.run, work, "a")
            tg.start_soon(pool.run, work, "b")

    anyio.run(go)
    assert max(peak) == 1  # never two workers at once on a limit-1 pool


def test_gauges_track_borrowed_and_available_live():
    pool = WorkerPool(limit=2)
    release = threading.Event()
    snapshot: dict = {}

    def hold():
        release.wait(timeout=5.0)

    async def go():
        async with anyio.create_task_group() as tg:
            tg.start_soon(pool.run, hold)
            await anyio.sleep(0.1)  # let the worker take its slot
            snapshot["borrowed"] = pool.borrowed
            snapshot["available"] = pool.available
            release.set()

    anyio.run(go)
    assert snapshot["borrowed"] == 1
    assert snapshot["available"] == 1
    assert pool.borrowed == 0  # slot returned after the work finished
