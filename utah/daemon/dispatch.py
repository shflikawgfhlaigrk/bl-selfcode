"""Method dispatch — a table, not 92 inline handlers.

Ace's daemon inlined 92 ``_handle_*`` in one 8.7k-line file; one import error
took the whole thing down. Utah maps method → handler in a table; each handler
is a small async function in ``handlers/`` that receives a :class:`Context`
(the live pool, governor, and shutdown trigger) and the request params. Unknown
methods are a typed ``METHOD_NOT_FOUND``, never a crash.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import anyio

from utah.daemon.bus import Bus
from utah.daemon.pool import WorkerPool
from utah.daemon.governor import Governor
from utah.daemon.rpc import METHOD_NOT_FOUND, Request, RpcError

Handler = Callable[["Context", object], Awaitable[object]]


@dataclass(slots=True)
class Context:
    """Live wiring handed to every handler (no injectable stand-ins)."""

    pool: WorkerPool
    governor: Governor
    bus: Bus
    shutdown: anyio.Event
    started_monotonic: float
    version: str

    @property
    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 3)


class Dispatcher:
    def __init__(self, ctx: Context, handlers: dict[str, Handler]) -> None:
        # Validate at BOOT, not at the first 3am call: a sync function wired as
        # a handler would only explode when its return value is awaited, which
        # the server surfaces as a generic INTERNAL_ERROR. Fail loud here.
        for name, fn in handlers.items():
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(
                    f"handler {name!r} must be an async function, "
                    f"got {type(fn).__name__}"
                )
        self._ctx = ctx
        self._handlers = dict(handlers)  # snapshot: caller mutation can't rewire us

    @property
    def methods(self) -> list[str]:
        return sorted(self._handlers)

    async def dispatch(self, req: Request) -> object:
        handler = self._handlers.get(req.method)
        if handler is None:
            raise RpcError(METHOD_NOT_FOUND, f"method not found: {req.method}")
        return await handler(self._ctx, req.params)
