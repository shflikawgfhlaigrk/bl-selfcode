"""Control-client edges over REAL unix sockets: typed DaemonNotRunning when the
socket is absent, bounded timeouts when a wedged daemon accepts but never
replies (including the subscribe handshake, which previously could hang
forever), and typed errors for malformed/error responses."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio
import pytest

from utah.daemon import client as ctl
from utah.daemon import frame, rpc
from utah.daemon.client import DaemonNotRunning
from utah.daemon.rpc import RpcError


@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        for fn in (lambda: os.unlink(path), lambda: os.rmdir(d)):
            try:
                fn()
            except OSError:
                pass


async def _serve_scripted(listener, reply: bytes | None):
    """One-shot fake daemon: read the request frame then send *reply* (raw
    bytes, pre-framed) — or stall forever when reply is None (wedged daemon)."""

    async def handle(stream):
        async with stream:
            try:
                await frame.read_frame(stream)
            except frame.FrameError:
                return
            if reply is None:
                await anyio.sleep(30)  # wedged: never answers
            else:
                await stream.send(reply)
                await anyio.sleep(30)  # hold the conn open; client closes it

    await listener.serve(handle)


def _run_against_server(sock_path: str, reply: bytes | None, client_coro):
    """Bind a real unix listener, run the client coroutine against it."""
    result: dict = {}

    async def scenario():
        listener = await anyio.create_unix_listener(sock_path)
        async with anyio.create_task_group() as tg:
            tg.start_soon(_serve_scripted, listener, reply)
            try:
                result["value"] = await client_coro()
            except BaseException as exc:  # noqa: BLE001 — captured for assertion
                result["exc"] = exc
            tg.cancel_scope.cancel()
        await listener.aclose()

    anyio.run(scenario)
    return result


# -- missing socket → typed DaemonNotRunning (never a raw OSError) ------------

def test_call_missing_socket_raises_daemon_not_running(sock):
    async def go():
        with pytest.raises(DaemonNotRunning):
            await ctl.call("ping", sock_path=sock)

    anyio.run(go)


def test_call_sync_missing_socket_raises_daemon_not_running(sock):
    with pytest.raises(DaemonNotRunning):
        ctl.call_sync("ping", sock_path=sock)


def test_subscribe_missing_socket_raises_daemon_not_running(sock):
    async def go():
        agen = ctl.subscribe(["engine"], sock_path=sock)
        with pytest.raises(DaemonNotRunning):
            await agen.__anext__()

    anyio.run(go)


def test_tell_stream_missing_socket_raises_daemon_not_running(sock):
    async def go():
        agen = ctl.tell_stream("hi", sock_path=sock)
        with pytest.raises(DaemonNotRunning):
            await agen.__anext__()

    anyio.run(go)


# -- wedged daemon → bounded, not forever --------------------------------------

def test_call_times_out_on_unresponsive_daemon(sock):
    t0 = time.monotonic()
    r = _run_against_server(sock, None, lambda: ctl.call("ping", sock_path=sock, timeout=0.3))
    assert isinstance(r.get("exc"), TimeoutError)
    assert time.monotonic() - t0 < 5.0  # bounded, not the 30s server stall


def test_subscribe_handshake_is_bounded(sock):
    """The subscribe ack must be under a timeout: a daemon that accepts the
    connection but never acks previously hung the subscriber FOREVER."""

    async def client():
        agen = ctl.subscribe(["engine"], sock_path=sock, ack_timeout=0.3)
        return await agen.__anext__()

    t0 = time.monotonic()
    r = _run_against_server(sock, None, client)
    elapsed = time.monotonic() - t0
    assert isinstance(r.get("exc"), TimeoutError)
    assert elapsed < 5.0  # the ack timeout fired, not an unbounded hang


# -- response decoding: typed, never a raw msgspec leak ------------------------

def test_error_envelope_raises_rpc_error_with_code_and_data(sock):
    reply = frame.encode(
        frame.KIND_JSON,
        b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"shed","data":{"load":9.9}}}',
    )
    r = _run_against_server(sock, reply, lambda: ctl.call("status", sock_path=sock, timeout=5.0))
    exc = r.get("exc")
    assert isinstance(exc, RpcError)
    assert exc.code == -32000
    assert exc.data == {"load": 9.9}


def test_result_envelope_returns_result(sock):
    reply = frame.encode(frame.KIND_JSON, b'{"jsonrpc":"2.0","id":1,"result":{"pong":true}}')
    r = _run_against_server(sock, reply, lambda: ctl.call("ping", sock_path=sock, timeout=5.0))
    assert r["value"] == {"pong": True}


def test_malformed_response_raises_typed_parse_error(sock):
    """A daemon replying garbage must surface as a typed RpcError(PARSE_ERROR),
    not a raw msgspec.DecodeError the CLI has never heard of."""
    reply = frame.encode(frame.KIND_JSON, b"this is not json")
    r = _run_against_server(sock, reply, lambda: ctl.call("ping", sock_path=sock, timeout=5.0))
    exc = r.get("exc")
    assert isinstance(exc, RpcError)
    assert exc.code == rpc.PARSE_ERROR
