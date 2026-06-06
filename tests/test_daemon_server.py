"""Live control-server mechanics on a REAL unix socket + REAL worker pool.

Nothing is stubbed: a real ``ControlServer`` binds a real socket in a temp dir,
a real ``WorkerPool`` runs real off-loop work, and a real ``ControlClient``
drives it over the wire. The headline assertion is the Phase-0 gate: while an
``agent`` occupies the pool, a concurrent ``ping`` still returns in <50 ms.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio
import pytest

from utah.daemon import client as ctl


@pytest.fixture
def sock():
    """A short AF_UNIX socket path (macOS caps the path at ~104 chars, so
    pytest's deep tmp_path can't be used for a unix socket)."""
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            os.rmdir(d)
        except OSError:
            pass
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.handlers import REGISTRY
from utah.daemon.pool import WorkerPool
from utah.daemon.rpc import METHOD_NOT_FOUND, RpcError
from utah.daemon.server import ControlServer


def _build_server(sock_path):
    from utah.daemon.bus import Bus
    pool = WorkerPool(limit=4)
    gov = Governor(max_load_per_core=1e9, max_inflight=1000)  # never shed in test
    bus = Bus()
    shutdown = anyio.Event()
    ctx = Context(pool=pool, governor=gov, bus=bus, shutdown=shutdown,
                  started_monotonic=time.monotonic(), version="test")
    server = ControlServer(Dispatcher(ctx, REGISTRY), sock_path=Path(sock_path), bus=bus)
    return server, pool


def test_ping_status_and_no_loop_blocking(sock):
    server, pool = _build_server(sock)
    result: dict = {}

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)

            # ping + status round-trip
            result["pong"] = await ctl.call("ping", sock_path=sock)
            result["status"] = await ctl.call("status", sock_path=sock)

            # occupy a pool slot with a 1s agent (separate connection)
            tg.start_soon(lambda: ctl.call("agent", {"seconds": 1.0}, sock_path=sock))
            await anyio.sleep(0.2)  # let the agent reach the pool
            result["borrowed_during"] = pool.borrowed

            # concurrent ping MUST stay fast while the agent blocks a worker
            t = time.perf_counter()
            result["pong2"] = await ctl.call("ping", sock_path=sock)
            result["ping_dt"] = time.perf_counter() - t

            await anyio.sleep(1.1)  # let the agent finish
            tg.cancel_scope.cancel()

    anyio.run(scenario)

    assert result["pong"]["pong"] is True
    assert result["status"]["pool"]["limit"] == 4
    assert result["borrowed_during"] == 1            # agent really is off-loop in the pool
    assert result["pong2"]["pong"] is True
    assert result["ping_dt"] < 0.05                  # <50 ms: the no-loop-blocking gate


def test_method_not_found_is_typed(sock):
    server, _ = _build_server(sock)
    err: dict = {}

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            try:
                await ctl.call("does_not_exist", sock_path=sock)
            except RpcError as exc:
                err["code"] = exc.code
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert err["code"] == METHOD_NOT_FOUND


def test_invalid_params_rejected(sock):
    server, _ = _build_server(sock)
    err: dict = {}

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            try:
                await ctl.call("tell", {"text": "   "}, sock_path=sock)  # empty text
            except RpcError as exc:
                err["code"] = exc.code
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert err["code"] == -32602  # INVALID_PARAMS
