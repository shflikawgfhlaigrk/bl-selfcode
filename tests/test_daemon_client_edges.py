"""Control-client stream + envelope edges over REAL unix sockets (complements
test_daemon_client.py, which pins missing-socket/timeout/decode). These pin:
the exact JSON-RPC envelope on the wire (method/params/id/jsonrpc), tell_stream
ending CLEANLY when the daemon dies mid-turn (EOF without ``done`` — the voice
loop must not traceback on a daemon restart), tell_stream stopping AT ``done``,
and subscribe yielding only ``event`` notifications."""
from __future__ import annotations

import json
import os
import tempfile

import anyio
import pytest

from utah.daemon import client as ctl
from utah.daemon import frame


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


def _run_scripted(sock_path: str, replies: list[bytes], client_coro,
                  close_after: bool = True):
    """Real unix listener: read ONE request frame, capture it, send each reply
    frame, then close (close_after) or hold. Returns {request, value | exc}."""
    result: dict = {}

    async def handle(stream):
        async with stream:
            try:
                _kind, payload = await frame.read_frame(stream)
                result["request"] = json.loads(payload)
            except frame.FrameError:
                return
            for r in replies:
                await stream.send(r)
            if not close_after:
                await anyio.sleep(30)

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


def _json_frame(obj: dict) -> bytes:
    return frame.encode(frame.KIND_JSON, json.dumps(obj).encode())


_ACK = _json_frame({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})


def _tell_event(channel: str, chunk: str) -> bytes:
    return _json_frame({"jsonrpc": "2.0", "method": "tell_event",
                        "params": {"channel": channel, "chunk": chunk}})


# -- the wire envelope ----------------------------------------------------------

def test_call_sends_a_correct_jsonrpc_envelope(sock):
    reply = _json_frame({"jsonrpc": "2.0", "id": 7, "result": "pong"})
    r = _run_scripted(
        sock, [reply],
        lambda: ctl.call("tell", {"text": "hi"}, sock_path=sock,
                         timeout=5.0, request_id=7),
    )
    assert r["value"] == "pong"
    assert r["request"] == {"jsonrpc": "2.0", "method": "tell",
                            "params": {"text": "hi"}, "id": 7}


def test_call_omits_params_key_when_none(sock):
    """Some handlers reject ``params: null`` — no params means no key at all."""
    r = _run_scripted(sock, [_ACK], lambda: ctl.call("ping", sock_path=sock, timeout=5.0))
    assert "params" not in r["request"]


def test_call_non_dict_response_returns_none_not_a_crash(sock):
    reply = _json_frame(["not", "an", "envelope"])
    r = _run_scripted(sock, [reply], lambda: ctl.call("ping", sock_path=sock, timeout=5.0))
    assert r["value"] is None


# -- tell_stream lifecycle --------------------------------------------------------

async def _collect(agen) -> list:
    return [ev async for ev in agen]


def test_tell_stream_ends_cleanly_on_eof_without_done(sock):
    """The daemon dying mid-turn closes the socket before ``done`` arrives.
    The generator must END (StopAsyncIteration), never leak a FrameError into
    the voice/chat loop — a daemon restart is an ended turn, not a crash."""
    replies = [_ACK, _tell_event("answer", "half a thou")]
    r = _run_scripted(
        sock, replies,
        lambda: _collect(ctl.tell_stream("hi", sock_path=sock, timeout=5.0)),
        close_after=True,
    )
    assert "exc" not in r, f"expected clean end, got {r.get('exc')!r}"
    assert r["value"] == [{"channel": "answer", "chunk": "half a thou"}]


def test_tell_stream_stops_at_done_event(sock):
    replies = [
        _ACK,
        _tell_event("answer", "the answer"),
        _tell_event("done", "the answer"),
        _tell_event("answer", "AFTER-DONE MUST NOT ARRIVE"),
    ]
    r = _run_scripted(
        sock, replies,
        lambda: _collect(ctl.tell_stream("hi", sock_path=sock, timeout=5.0)),
        close_after=False,  # server holds the conn; the client must stop itself
    )
    channels = [ev["channel"] for ev in r["value"]]
    assert channels == ["answer", "done"]


def test_tell_stream_ignores_non_tell_event_frames(sock):
    replies = [
        _ACK,
        _json_frame({"jsonrpc": "2.0", "method": "event",
                     "params": {"channel": "engine", "event": {}}}),  # bus noise
        _tell_event("done", "x"),
    ]
    r = _run_scripted(
        sock, replies,
        lambda: _collect(ctl.tell_stream("hi", sock_path=sock, timeout=5.0)),
        close_after=False,
    )
    assert [ev["channel"] for ev in r["value"]] == ["done"]


# -- subscribe filtering ------------------------------------------------------------

def test_subscribe_yields_only_event_notifications(sock):
    replies = [
        _ACK,  # subscribe ack
        _json_frame({"jsonrpc": "2.0", "method": "tell_event",
                     "params": {"channel": "answer", "chunk": "noise"}}),
        _json_frame({"jsonrpc": "2.0", "method": "event",
                     "params": {"channel": "engine", "event": {"fired": "x"}}}),
    ]

    async def first_event():
        agen = ctl.subscribe(["engine"], sock_path=sock)
        try:
            return await agen.__anext__()
        finally:
            await agen.aclose()

    r = _run_scripted(sock, replies, first_event, close_after=False)
    assert r["value"] == {"channel": "engine", "event": {"fired": "x"}}


def test_subscribe_sends_channel_list_in_params(sock):
    replies = [_ACK,
               _json_frame({"jsonrpc": "2.0", "method": "event",
                            "params": {"channel": "risk", "event": {}}})]

    async def first_event():
        agen = ctl.subscribe(["risk", "engine"], sock_path=sock)
        try:
            return await agen.__anext__()
        finally:
            await agen.aclose()

    r = _run_scripted(sock, replies, first_event, close_after=False)
    assert r["request"]["method"] == "subscribe"
    assert sorted(r["request"]["params"]["channels"]) == ["engine", "risk"]
