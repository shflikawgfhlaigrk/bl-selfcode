"""Streaming RPC: `tell_stream` holds the connection open and pushes one frame
per (channel, chunk) event from core.tell_stream — so chat/voice render the
brain's reasoning live over the control socket. Real socket; the brain is
monkeypatched out (the relay is what's under test, core.tell_stream has its own).
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio
import pytest

from utah import core
from utah.daemon import client as ctl
from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.handlers import REGISTRY
from utah.daemon.pool import WorkerPool
from utah.daemon.server import ControlServer


@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _build_server(sock_path):
    pool = WorkerPool(limit=4)
    gov = Governor(max_load_per_core=1e9, max_inflight=1000)
    bus = Bus()
    ctx = Context(pool=pool, governor=gov, bus=bus, shutdown=anyio.Event(),
                  started_monotonic=time.monotonic(), version="test")
    return ControlServer(Dispatcher(ctx, REGISTRY), sock_path=Path(sock_path), bus=bus)


def test_tell_stream_relays_brain_events_over_the_socket(sock, monkeypatch):
    monkeypatch.setattr(core, "tell_stream", lambda t: iter([
        ("source", "brain"), ("thinking", "reasoning"),
        ("answer", "the answer"), ("done", "the answer"),
    ]))
    server = _build_server(sock)
    got: list[dict] = []

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            async for ev in ctl.tell_stream("hi", sock_path=sock):
                got.append(ev)
            tg.cancel_scope.cancel()

    anyio.run(scenario)

    chans = [e["channel"] for e in got]
    assert "thinking" in chans and "answer" in chans
    assert got[-1]["channel"] == "done"
    thinking = "".join(e["chunk"] for e in got if e["channel"] == "thinking")
    answer = "".join(e["chunk"] for e in got if e["channel"] == "answer")
    assert thinking == "reasoning"
    assert answer == "the answer"


def test_safe_write_swallows_broken_pipe_returns_false():
    """A client vanishing mid-stream makes the socket write raise BrokenResourceError;
    _safe_write MUST swallow it and return False — never let it crash the daemon."""
    server = ControlServer(None, sock_path=Path("/tmp/none.sock"))

    class DeadStream:
        async def send(self, data):
            raise anyio.BrokenResourceError()

    async def go():
        return await server._safe_write(DeadStream(), b'{"jsonrpc":"2.0","method":"event"}')

    assert anyio.run(go) is False


def test_tell_stream_survives_client_disconnect_mid_stream(sock, monkeypatch):
    """The headline regression: a client that disconnects mid-turn must NOT crash
    the server. tell_stream yields many events; the client reads one then closes."""
    monkeypatch.setattr(core, "tell_stream", lambda t: iter(
        [("answer", f"chunk{i}") for i in range(50)] + [("done", "x")]))
    server = _build_server(sock)
    crashed = {}

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            # read exactly one event then drop the connection
            agen = ctl.tell_stream("hi", sock_path=sock)
            await agen.__anext__()
            await agen.aclose()  # client disconnects mid-stream
            await anyio.sleep(0.3)  # give the server a chance to crash if it would
            # server still alive? a fresh ping must succeed
            crashed["ping_ok"] = (await ctl.call("ping", sock_path=sock)).get("pong") is True
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert crashed["ping_ok"] is True  # daemon survived the mid-stream disconnect


def test_tell_stream_passes_the_text_to_core(sock, monkeypatch):
    seen = {}
    def _capture(text):
        seen["text"] = text
        return iter([("answer", "ok"), ("done", "ok")])
    monkeypatch.setattr(core, "tell_stream", _capture)
    server = _build_server(sock)

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            async for _ in ctl.tell_stream("where does Michael live?", sock_path=sock):
                pass
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    assert seen["text"] == "where does Michael live?"
