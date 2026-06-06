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
from utah.daemon.rpc import RpcError
from utah.daemon.runtime import CONTROL_SOCK

_encode = msgspec.json.Encoder().encode
_decode = msgspec.json.Decoder().decode


class DaemonNotRunning(UtahError):
    """The control socket is absent or refusing connections."""


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
    resp = _decode(payload)
    if isinstance(resp, dict) and resp.get("error"):
        e = resp["error"]
        raise RpcError(e.get("code", -32603), e.get("message", "error"), e.get("data"))
    return resp.get("result") if isinstance(resp, dict) else None


async def subscribe(channels=None, *, sock_path=CONTROL_SOCK):
    """Async-iterate bus events pushed by the daemon. Yields ``{channel, event}``.

    The connection stays open and events are pushed (no polling). The first
    frame is the subscribe ack; subsequent frames are ``event`` notifications.
    """
    try:
        stream = await anyio.connect_unix(str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise DaemonNotRunning(f"daemon not reachable at {sock_path}: {exc}") from exc
    env: dict = {"jsonrpc": "2.0", "method": "subscribe", "id": 1}
    if channels:
        env["params"] = {"channels": list(channels)}
    async with stream:
        await frame.write_frame(stream, frame.KIND_JSON, _encode(env))
        await frame.read_frame(stream)  # subscribe ack
        while True:
            _kind, payload = await frame.read_frame(stream)
            msg = _decode(payload)
            if isinstance(msg, dict) and msg.get("method") == "event":
                yield msg.get("params")


def call_sync(method: str, params: object | None = None, **kwargs) -> object:
    """Blocking convenience for the CLI."""
    return anyio.run(lambda: call(method, params, **kwargs))
