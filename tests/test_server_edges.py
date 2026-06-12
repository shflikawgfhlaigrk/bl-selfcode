"""Control-server failure edges on a REAL unix socket: a crashing handler, an
UN-JSON-ABLE handler result, hostile/binary frames, notifications, and a
rejected peer — none of them may kill the server or leak a traceback to the
peer. The headline: 'a handler exception never crashes the server' must also
hold for the RESPONSE ENCODE step, the one path the old code left unguarded."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio
import msgspec
import pytest

from utah import UtahError
from utah.daemon import client as ctl
from utah.daemon import frame
from utah.daemon import server as server_mod
from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.peercred import PeerAuthError
from utah.daemon.pool import WorkerPool
from utah.daemon.rpc import INTERNAL_ERROR, OVERLOADED, UNAVAILABLE, RpcError
from utah.daemon.server import ControlServer

_encode = msgspec.json.Encoder().encode


@pytest.fixture
def sock():
    """Short AF_UNIX path (macOS caps sun_path ~104 chars; pytest tmp_path is too deep)."""
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        for p in (path,):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def _build(sock_path: str, extra_handlers: dict | None = None) -> ControlServer:
    async def _ok(_ctx, _params):
        return {"pong": True}

    async def _boom(_ctx, _params):
        raise RuntimeError("secret internal detail")

    async def _unjson(_ctx, _params):
        return object()  # not JSON-encodable

    async def _down(_ctx, _params):
        raise UtahError("memory down")

    handlers = {"ok": _ok, "boom": _boom, "unjson": _unjson, "down": _down,
                **(extra_handlers or {})}
    ctx = Context(pool=WorkerPool(limit=2),
                  governor=Governor(max_load_per_core=1e9, max_inflight=1000),
                  bus=Bus(), shutdown=anyio.Event(),
                  started_monotonic=time.monotonic(), version="test")
    return ControlServer(Dispatcher(ctx, handlers), sock_path=Path(sock_path))


def _run(server: ControlServer, scenario) -> dict:
    out: dict = {}

    async def go():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)
            with anyio.fail_after(10):
                await scenario(out)
            tg.cancel_scope.cancel()

    anyio.run(go)
    return out


# -- handler failures must be typed replies, never a dead server -------------------

def test_handler_crash_is_internal_error_and_the_server_survives(sock):
    server = _build(sock)

    async def scenario(out):
        try:
            await ctl.call("boom", sock_path=sock)
        except RpcError as exc:
            out["code"] = exc.code
            out["msg"] = str(exc)
        out["after"] = await ctl.call("ok", sock_path=sock)  # server still alive

    out = _run(server, scenario)
    assert out["code"] == INTERNAL_ERROR
    assert "secret internal detail" not in out["msg"]  # no exception detail leaks
    assert out["after"] == {"pong": True}


def test_unencodable_handler_result_is_internal_error_not_a_dead_server(sock):
    """A handler that returns something msgspec can't encode used to raise OUT of
    _respond_req — past the 'never crash' guards — and took the listener down."""
    server = _build(sock)

    async def scenario(out):
        try:
            await ctl.call("unjson", sock_path=sock)
        except RpcError as exc:
            out["code"] = exc.code
        out["after"] = await ctl.call("ok", sock_path=sock)

    out = _run(server, scenario)
    assert out["code"] == INTERNAL_ERROR
    assert out["after"] == {"pong": True}


def test_dependency_down_is_unavailable(sock):
    server = _build(sock)

    async def scenario(out):
        try:
            await ctl.call("down", sock_path=sock)
        except RpcError as exc:
            out["code"] = exc.code

    out = _run(server, scenario)
    assert out["code"] == UNAVAILABLE


# -- wire edges --------------------------------------------------------------------

def test_binary_frame_on_the_control_socket_is_ignored(sock):
    """Data-plane frames are not served here — but they must not kill the
    connection: a JSON ping on the SAME connection still answers."""
    server = _build(sock)

    async def scenario(out):
        stream = await anyio.connect_unix(sock)
        async with stream:
            await frame.write_frame(stream, frame.KIND_BINARY, b"\x00\x01\x02")
            req = _encode({"jsonrpc": "2.0", "method": "ok", "id": 7})
            await frame.write_frame(stream, frame.KIND_JSON, req)
            _kind, payload = await frame.read_frame(stream)
            out["resp"] = msgspec.json.decode(payload)

    out = _run(server, scenario)
    assert out["resp"]["id"] == 7
    assert out["resp"]["result"] == {"pong": True}


def test_notification_runs_but_gets_no_reply(sock):
    """id is None ⇒ notification: the handler executes, no response frame is
    queued — the next reply on the wire belongs to the FOLLOWING request."""
    ran: list[str] = []

    async def _note(_ctx, _params):
        ran.append("note")
        return {"ignored": True}

    server = _build(sock, extra_handlers={"note": _note})

    async def scenario(out):
        stream = await anyio.connect_unix(sock)
        async with stream:
            await frame.write_frame(
                stream, frame.KIND_JSON, _encode({"jsonrpc": "2.0", "method": "note"}))
            await frame.write_frame(
                stream, frame.KIND_JSON, _encode({"jsonrpc": "2.0", "method": "ok", "id": 2}))
            _kind, payload = await frame.read_frame(stream)
            out["first_reply"] = msgspec.json.decode(payload)

    out = _run(server, scenario)
    assert ran == ["note"]                      # the notification really ran
    assert out["first_reply"]["id"] == 2        # and produced no reply of its own


def test_rejected_peer_gets_a_closed_connection_not_service(sock, monkeypatch):
    """When peer-cred verification denies, the connection is closed before any
    frame is served (fail-closed at the door)."""
    monkeypatch.setattr(
        server_mod, "authorize",
        lambda raw: (_ for _ in ()).throw(PeerAuthError("peer uid 666 — denied")))
    server = _build(sock)

    async def scenario(out):
        stream = await anyio.connect_unix(sock)
        async with stream:
            with pytest.raises((frame.FrameError, anyio.BrokenResourceError, OSError)):
                req = _encode({"jsonrpc": "2.0", "method": "ok", "id": 1})
                await frame.write_frame(stream, frame.KIND_JSON, req)
                await frame.read_frame(stream)
        out["denied"] = True

    out = _run(server, scenario)
    assert out["denied"] is True


def test_stale_socket_file_is_replaced_on_bind(sock):
    """The flock singleton guarantees no live owner — a leftover socket file
    from a crash must not block the next boot's bind."""
    Path(sock).touch()  # a stale plain file squatting on the path
    server = _build(sock)

    async def scenario(out):
        out["pong"] = await ctl.call("ok", sock_path=sock)

    out = _run(server, scenario)
    assert out["pong"] == {"pong": True}


# -- shed visibility ----------------------------------------------------------------

def test_note_shed_records_once_per_minute_per_method(monkeypatch):
    """Sheds were invisible (96k blank polls, zero log lines) — _note_shed makes
    them visible WITHOUT becoming its own storm: 1 record/min/method."""
    from utah import failures

    recorded: list[tuple] = []
    monkeypatch.setattr(failures, "record", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(server_mod, "_shed_last", {})
    exc = RpcError(OVERLOADED, "load 9.9")

    server_mod._note_shed("status", exc)
    server_mod._note_shed("status", exc)        # within the minute — suppressed
    assert len(recorded) == 1

    server_mod._note_shed("tell", exc)          # a different method records
    assert len(recorded) == 2

    # past the window the same method records again
    server_mod._shed_last["status"] -= 61.0
    server_mod._note_shed("status", exc)
    assert len(recorded) == 3
