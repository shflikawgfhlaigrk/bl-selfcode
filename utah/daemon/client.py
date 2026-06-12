"""Control client — frame a JSON-RPC call over the unix socket, get a result.

Used by the ``utah`` CLI and by live tests. One call = one connection
(connect → write framed request → read framed response → close). A typed error
response raises :class:`RpcError`; a missing socket raises
:class:`DaemonNotRunning` (so the CLI can say "start it" instead of a traceback).
"""
from __future__ import annotations

import anyio
import msgspec

from utah import UtahError
from utah.daemon import frame
from utah.daemon.rpc import PARSE_ERROR, RpcError
from utah.daemon.runtime import CONTROL_SOCK

_encode = msgspec.json.Encoder().encode
_decode = msgspec.json.Decoder().decode


class DaemonNotRunning(UtahError):
    """The control socket is absent or refusing connections."""


def _decode_response(payload: bytes) -> object:
    """Decode a response frame; garbage from the peer is a typed RpcError
    (PARSE_ERROR), never a raw msgspec exception the caller has no name for."""
    try:
        return _decode(payload)
    except msgspec.DecodeError as exc:
        raise RpcError(PARSE_ERROR, f"daemon sent a malformed response: {exc}") from exc


async def call(
    method: str,
    params: object | None = None,
    *,
    sock_path=CONTROL_SOCK,
    timeout: float = 120.0,
    request_id: int = 1,
) -> object:
    envelope: dict = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        envelope["params"] = params
    request = _encode(envelope)
    try:
        stream = await anyio.connect_unix(str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise DaemonNotRunning(f"daemon not reachable at {sock_path}: {exc}") from exc
    async with stream:
        with anyio.fail_after(timeout):
            await frame.write_frame(stream, frame.KIND_JSON, request)
            _kind, payload = await frame.read_frame(stream)
    resp = _decode_response(payload)
    if isinstance(resp, dict) and resp.get("error"):
        e = resp["error"]
        raise RpcError(e.get("code", -32603), e.get("message", "error"), e.get("data"))
    return resp.get("result") if isinstance(resp, dict) else None


async def subscribe(channels=None, *, sock_path=CONTROL_SOCK, ack_timeout: float = 10.0):
    """Async-iterate bus events pushed by the daemon. Yields ``{channel, event}``.

    The connection stays open and events are pushed (no polling). The first
    frame is the subscribe ack — bounded by *ack_timeout* so a wedged daemon
    can never hang the subscriber forever; the event stream itself is
    deliberately unbounded (push channel, events arrive whenever they arrive).
    """
    try:
        stream = await anyio.connect_unix(str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise DaemonNotRunning(f"daemon not reachable at {sock_path}: {exc}") from exc
    env: dict = {"jsonrpc": "2.0", "method": "subscribe", "id": 1}
    if channels:
        env["params"] = {"channels": list(channels)}
    async with stream:
        with anyio.fail_after(ack_timeout):
            await frame.write_frame(stream, frame.KIND_JSON, _encode(env))
            await frame.read_frame(stream)  # subscribe ack
        while True:
            _kind, payload = await frame.read_frame(stream)
            msg = _decode_response(payload)
            if isinstance(msg, dict) and msg.get("method") == "event":
                yield msg.get("params")


async def tell_stream(text: str, *, sock_path=CONTROL_SOCK, timeout: float = 180.0):
    """Stream a turn: yields ``{channel, chunk}`` events (source/thinking/answer/
    done) as the daemon produces them. The connection stays open and frames are
    pushed (first frame = ack); ends on the ``done`` event or when the peer closes.
    """
    try:
        stream = await anyio.connect_unix(str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise DaemonNotRunning(f"daemon not reachable at {sock_path}: {exc}") from exc
    env = {"jsonrpc": "2.0", "method": "tell_stream", "id": 1, "params": {"text": text}}
    async with stream:
        with anyio.fail_after(timeout):
            await frame.write_frame(stream, frame.KIND_JSON, _encode(env))
            await frame.read_frame(stream)  # ack
            while True:
                try:
                    _kind, payload = await frame.read_frame(stream)
                except (frame.FrameError, anyio.BrokenResourceError,
                        anyio.ClosedResourceError, OSError):
                    # Stream closed (daemon restart / EOF, all wrapped in
                    # FrameError on the read side; raw anyio/OS errors cover
                    # the transport itself): the turn simply ends — callers
                    # resubmit. Anything else is a real bug and propagates.
                    return
                msg = _decode_response(payload)
                if isinstance(msg, dict) and msg.get("method") == "tell_event":
                    ev = msg.get("params") or {}
                    yield ev
                    if ev.get("channel") == "done":
                        return


def call_sync(method: str, params: object | None = None, **kwargs) -> object:
    """Blocking convenience for the CLI."""
    return anyio.run(lambda: call(method, params, **kwargs))
