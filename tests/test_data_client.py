"""Data-plane client edges over REAL unix sockets: a missing socket is a typed
DaemonNotRunning (same contract as the control client — never a raw OSError a
CLI has to pattern-match), a wedged server is BOUNDED by the timeout, a JSON
error frame and an unknown frame kind both raise WinCodecError (fail-loud,
never garbage arrays), and the happy roundtrip preserves the array bit-exact."""
from __future__ import annotations

import os
import tempfile
import time

import anyio
import pytest

np = pytest.importorskip("numpy")

from utah import win
from utah.daemon import data_client, frame
from utah.daemon.client import DaemonNotRunning


@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="utdc", dir="/tmp")
    path = os.path.join(d, "d.sock")
    try:
        yield path
    finally:
        for fn in (lambda: os.unlink(path), lambda: os.rmdir(d)):
            try:
                fn()
            except OSError:
                pass


def _run_scripted(sock_path: str, reply, client_coro):
    """Real unix listener; *reply* is raw pre-framed bytes, a callable
    payload→raw-bytes (echo-style), or None (wedged: never answers)."""
    result: dict = {}

    async def handle(stream):
        async with stream:
            try:
                _kind, payload = await frame.read_frame(stream)
            except frame.FrameError:
                return
            if reply is None:
                await anyio.sleep(30)  # wedged server
            else:
                out = reply(payload) if callable(reply) else reply
                await stream.send(out)
                await anyio.sleep(30)  # hold open; the client closes

    async def scenario():
        listener = await anyio.create_unix_listener(sock_path)
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            try:
                result["value"] = await client_coro()
            except BaseException as exc:  # noqa: BLE001 — captured for assertion
                result["exc"] = exc
            tg.cancel_scope.cancel()
        await listener.aclose()

    anyio.run(scenario)
    return result


# -- missing socket → typed, like the control client ---------------------------

def test_missing_socket_raises_daemon_not_running(sock):
    async def go():
        with pytest.raises(DaemonNotRunning):
            await data_client.send_array(np.array([1.0]), sock_path=sock)

    anyio.run(go)


def test_send_array_sync_missing_socket_raises_daemon_not_running(sock):
    with pytest.raises(DaemonNotRunning):
        data_client.send_array_sync(np.array([1.0]), sock_path=sock)


# -- wedged server → bounded -----------------------------------------------------

def test_wedged_server_is_bounded_by_the_timeout(sock):
    t0 = time.monotonic()
    r = _run_scripted(
        sock, None,
        lambda: data_client.send_array(np.array([1.0]), sock_path=sock, timeout=0.3),
    )
    assert isinstance(r.get("exc"), TimeoutError)
    assert time.monotonic() - t0 < 5.0  # the timeout fired, not the 30s stall


# -- fail-loud replies -------------------------------------------------------------

def test_json_error_frame_raises_win_codec_error_with_the_message(sock):
    reply = frame.encode(frame.KIND_JSON, b'{"error": "decode_failed"}')
    r = _run_scripted(
        sock, reply,
        lambda: data_client.send_array(np.array([1.0]), sock_path=sock, timeout=5.0),
    )
    exc = r.get("exc")
    assert isinstance(exc, win.WinCodecError)
    assert "decode_failed" in str(exc)


def test_unknown_frame_kind_raises_win_codec_error(sock):
    reply = frame.encode(7, b"\x00\x01")  # neither JSON nor BINARY
    r = _run_scripted(
        sock, reply,
        lambda: data_client.send_array(np.array([1.0]), sock_path=sock, timeout=5.0),
    )
    assert isinstance(r.get("exc"), win.WinCodecError)
    assert "7" in str(r["exc"])


# -- happy path: bit-exact roundtrip ------------------------------------------------

def test_roundtrip_preserves_array_and_sends_the_name(sock):
    sent_name = {}

    def echo(payload: bytes) -> bytes:
        name, arr = win.decode_array(payload)
        sent_name["name"] = name
        return frame.encode(frame.KIND_BINARY, win.encode_array(arr * 2.0, name))

    arr = np.array([1.5, -2.25, float("inf")])
    r = _run_scripted(
        sock, echo,
        lambda: data_client.send_array(arr, name="ticks", sock_path=sock, timeout=5.0),
    )
    assert "exc" not in r, f"roundtrip failed: {r.get('exc')!r}"
    assert sent_name["name"] == "ticks"
    out = r["value"]
    assert out.tolist()[:2] == [3.0, -4.5]
    assert np.isinf(out[2])  # exact floats incl Inf — the point of the binary plane
