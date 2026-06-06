"""Control server — unix socket, peer-cred, framed JSON-RPC, concurrent clients.

Each connection is its own structured task, so a slow ``tell`` on one client
never stalls a ``ping`` on another — the loop only ever *dispatches*; heavy work
lives in the pool. Per-connection: verify the peer is the owner (fail-closed),
then read framed JSON-RPC requests and write framed responses until the peer
closes. Every error becomes a typed JSON-RPC error object; a handler exception
never crashes the server.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import anyio
from anyio.abc import SocketAttribute

from utah import UtahError, failures
from utah.daemon import frame, rpc
from utah.daemon.dispatch import Dispatcher
from utah.daemon.peercred import PeerAuthError, authorize
from utah.daemon.rpc import INTERNAL_ERROR, UNAVAILABLE, RpcError

log = logging.getLogger("utah.daemon.server")


class ControlServer:
    def __init__(
        self,
        dispatcher: Dispatcher,
        *,
        sock_path: Path,
        bus=None,
        max_frame: int = frame.MAX_FRAME_BYTES,
    ) -> None:
        self._dispatcher = dispatcher
        self._sock_path = sock_path
        self._bus = bus
        self._max_frame = max_frame

    async def serve(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """Bind the socket, signal readiness, then serve until cancelled."""
        # Singleton flock guarantees no live peer owns this path → a leftover
        # socket file is stale; remove it so the bind succeeds.
        try:
            if self._sock_path.exists():
                self._sock_path.unlink()
        except OSError:
            pass
        listener = await anyio.create_unix_listener(str(self._sock_path))
        os.chmod(self._sock_path, 0o600)
        log.info("control socket bound: %s", self._sock_path)
        task_status.started()
        try:
            await listener.serve(self._handle_connection)
        finally:
            await listener.aclose()

    async def _handle_connection(self, stream) -> None:
        # 1. Peer-cred: owner-only, fail-closed.
        try:
            raw = stream.extra(SocketAttribute.raw_socket)
            authorize(raw)
        except (PeerAuthError, Exception) as exc:
            log.warning("rejected connection: %s", exc)
            await stream.aclose()
            return
        # 2. Framed request/response loop.
        async with stream:
            while True:
                try:
                    kind, payload = await frame.read_frame(stream, self._max_frame)
                except frame.FrameError:
                    return  # peer closed or sent a bad frame → end this connection
                if kind != frame.KIND_JSON:
                    continue  # data-plane frames are not served on the control socket
                try:
                    req = rpc.parse(payload)
                except RpcError as exc:
                    await self._safe_write(stream, rpc.err(None, exc.code, str(exc), exc.data))
                    continue
                if req.method == "subscribe" and self._bus is not None:
                    await self._subscribe(stream, req)  # connection becomes a stream
                    return
                if req.method == "tell_stream":
                    await self._tell_stream(stream, req)  # connection becomes a stream
                    return
                response = await self._respond_req(req)
                if response is None:
                    continue  # notification: no reply
                if not await self._safe_write(stream, response):
                    return

    async def _safe_write(self, stream, payload: bytes) -> bool:
        try:
            await frame.write_frame(stream, frame.KIND_JSON, payload, self._max_frame)
            return True
        except frame.FrameError:
            return False

    async def _subscribe(self, stream, req) -> None:
        """Hold the connection open and push bus events until the client closes."""
        channels = req.params.get("channels") if isinstance(req.params, dict) else None
        sub = self._bus.subscribe(channels)
        ack = rpc.ok(req.id, {"subscribed": sorted(sub.channels) if sub.channels else "all"})
        if not await self._safe_write(stream, ack):
            self._bus._remove(sub.id)
            return
        async with sub:
            async with anyio.create_task_group() as tg:
                async def pump() -> None:
                    async for msg in sub.receive:
                        if not await self._safe_write(stream, rpc.notify("event", msg)):
                            tg.cancel_scope.cancel()
                            return

                async def watch_close() -> None:
                    try:
                        while True:
                            await frame.read_frame(stream, self._max_frame)
                    except frame.FrameError:
                        tg.cancel_scope.cancel()

                tg.start_soon(pump)
                tg.start_soon(watch_close)

    async def _tell_stream(self, stream, req) -> None:
        """Run core.tell_stream off-loop and push one frame per (channel, chunk)
        event, so chat/voice render the brain's reasoning live. A slow turn never
        blocks the loop (the blocking generator runs in a worker thread)."""
        from anyio import from_thread, to_thread

        from utah import core

        text = req.params.get("text", "") if isinstance(req.params, dict) else ""
        if not await self._safe_write(stream, rpc.ok(req.id, {"streaming": True})):
            return
        send, recv = anyio.create_memory_object_stream(256)

        async with anyio.create_task_group() as tg:
            async def produce() -> None:
                def _work() -> None:
                    for channel, chunk in core.tell_stream(text):
                        from_thread.run(send.send, {"channel": channel, "chunk": chunk})
                try:
                    await to_thread.run_sync(_work)
                except Exception:  # never crash the connection; the client ends on close
                    log.exception("tell_stream producer crashed")
                finally:
                    await send.aclose()

            async def pump() -> None:
                async with recv:
                    async for ev in recv:
                        if not await self._safe_write(stream, rpc.notify("tell_event", ev)):
                            tg.cancel_scope.cancel()
                            return

            tg.start_soon(produce)
            tg.start_soon(pump)

    async def _respond_req(self, req) -> bytes | None:
        try:
            result = await self._dispatcher.dispatch(req)
        except RpcError as exc:
            return None if req.is_notification else rpc.err(req.id, exc.code, str(exc), exc.data)
        except UtahError as exc:  # a live dependency (memory/brain/embed) is down
            log.warning("handler dependency unavailable: %s", exc)
            failures.record("daemon", "dependency_unavailable", f"{req.method}: {exc}")
            return None if req.is_notification else rpc.err(req.id, UNAVAILABLE, str(exc))
        except Exception as exc:  # never leak a traceback to the peer or crash
            log.exception("handler crashed: %s", req.method)
            failures.record("daemon", "handler_crash", f"{req.method}: {exc}")
            return None if req.is_notification else rpc.err(
                req.id, INTERNAL_ERROR, f"internal error in {req.method}"
            )
        return None if req.is_notification else rpc.ok(req.id, result)
