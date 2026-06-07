"""Data server — the WIN binary plane on its own unix socket (docs 3 & 8).

The sibling of :class:`~utah.daemon.server.ControlServer`: same length-prefixed
framing, same owner-only peer-cred (fail-closed), but it serves ``KIND_BINARY``
frames carrying zero-copy numeric payloads (:mod:`utah.win`) instead of JSON-RPC.
This is the path for hot numeric bursts (trading ticks, telemetry, int16 audio)
where JSON's per-element parse/alloc and NaN/Inf loss are unacceptable.

**Fail-loud:** a frame that isn't a valid WIN payload is answered with an explicit
JSON error frame and documented to the failure log — it is *never* silently
treated as success (Ace's cbor2-inert sin). A handler crash never kills the server.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import anyio
import numpy as np
from anyio.abc import SocketAttribute

from utah import failures, win
from utah.daemon import frame, rpc
from utah.daemon.peercred import PeerAuthError, authorize

log = logging.getLogger("utah.daemon.data_server")


def stats_handler(payload: bytes) -> bytes:
    """Default data capability: ingest a numeric array (zero-copy) and return its
    summary ``[count, min, max, mean, sum]`` as a WIN array. Proves a real binary
    round-trip; revenue feeds swap their own handler in the same shape."""
    _name, arr = win.decode_array(payload)         # zero-copy view; raises if malformed
    a = np.asarray(arr, dtype=np.float64).ravel()
    if a.size:
        stats = np.array([a.size, a.min(), a.max(), a.mean(), a.sum()], dtype=np.float64)
    else:
        stats = np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return win.encode_array(stats, name="stats")


class DataServer:
    def __init__(self, *, sock_path, max_frame: int = frame.MAX_FRAME_BYTES,
                 handler=stats_handler) -> None:
        self._sock_path = Path(sock_path)
        self._max_frame = max_frame
        self._handler = handler

    async def serve(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """Bind the data socket, signal readiness, then serve until cancelled."""
        try:
            if self._sock_path.exists():
                self._sock_path.unlink()        # flock guarantees no live owner → stale
        except OSError:
            pass
        listener = await anyio.create_unix_listener(str(self._sock_path))
        os.chmod(self._sock_path, 0o600)
        log.info("data socket bound: %s", self._sock_path)
        task_status.started()
        try:
            await listener.serve(self._handle_connection)
        finally:
            await listener.aclose()

    async def _handle_connection(self, stream) -> None:
        try:
            authorize(stream.extra(SocketAttribute.raw_socket))   # owner-only, fail-closed
        except (PeerAuthError, Exception) as exc:
            log.warning("data: rejected connection: %s", exc)
            await stream.aclose()
            return
        async with stream:
            while True:
                try:
                    kind, payload = await frame.read_frame(stream, self._max_frame)
                except frame.FrameError:
                    return                       # peer closed / bad frame → end connection
                if kind != frame.KIND_BINARY:
                    continue                     # JSON control frames aren't served here
                try:
                    reply = self._handler(payload)
                except win.WinCodecError as exc:
                    failures.record("data", "decode_failed", str(exc))
                    await self._safe_write(stream, frame.KIND_JSON,
                                           rpc.err(None, rpc.INVALID_PARAMS, f"win decode error: {exc}"))
                    continue                     # fail-loud: explicit error, never silent
                except Exception as exc:         # never let a handler crash the server
                    log.exception("data handler crashed")
                    failures.record("data", "handler_crash", str(exc))
                    await self._safe_write(stream, frame.KIND_JSON,
                                           rpc.err(None, rpc.INTERNAL_ERROR, "data handler error"))
                    continue
                if not await self._safe_write(stream, frame.KIND_BINARY, reply):
                    return

    async def _safe_write(self, stream, kind: int, payload: bytes) -> bool:
        try:
            await frame.write_frame(stream, kind, payload, self._max_frame)
            return True
        except (frame.FrameError, anyio.BrokenResourceError, anyio.ClosedResourceError,
                anyio.EndOfStream, ConnectionError, OSError):
            return False                         # peer vanished mid-write — never crash
