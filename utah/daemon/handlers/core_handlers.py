"""Core control-plane handlers — wired to the LIVE system.

``ping``/``status`` answer on the event loop in microseconds (the liveness the
dashboard's spine pills read). ``tell`` runs the real brain (``utah.core.tell``
— real Postgres + real Claude CLI) **off-loop in the bounded pool**, gated by
the governor. ``agent`` runs a real off-loop unit of work — it is the Phase-0
gate's "an agent runs in the worker pool while a concurrent ping stays <50 ms".
Nothing here is faked; the daemon binds to live reality.
"""
from __future__ import annotations

import time

from utah.daemon.dispatch import Context
from utah.daemon.rpc import INVALID_PARAMS, RpcError


def _as_dict(params: object) -> dict:
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    raise RpcError(INVALID_PARAMS, "params must be an object")


async def ping(ctx: Context, params: object) -> dict:
    return {"pong": True, "uptime_s": ctx.uptime_s}


async def status(ctx: Context, params: object) -> dict:
    return {
        "version": ctx.version,
        "uptime_s": ctx.uptime_s,
        "pool": {
            "limit": ctx.pool.limit,
            "borrowed": ctx.pool.borrowed,
            "available": ctx.pool.available,
        },
        "governor": ctx.governor.snapshot(),
        "bus": {
            "subscribers": ctx.bus.subscribers,
            "published": ctx.bus.published,
            "dropped": ctx.bus.dropped,
        },
        "draining": ctx.shutdown.is_set(),
    }


async def publish(ctx: Context, params: object) -> dict:
    """Publish one event to a channel; returns how many subscribers got it.

    This is how every program pushes live state to its dashboard panel
    (backend produces → surface reflects). Never blocks on a slow subscriber.
    """
    p = _as_dict(params)
    channel = str(p.get("channel", "")).strip()
    if not channel:
        raise RpcError(INVALID_PARAMS, "publish requires a 'channel'")
    event = p.get("event", {})
    if not isinstance(event, dict):
        raise RpcError(INVALID_PARAMS, "publish 'event' must be an object")
    return {"delivered": ctx.bus.publish(channel, event)}


def _tell_blocking(text: str):
    # imported lazily so the daemon module graph stays light; LIVE brain + pg.
    from utah import core

    return core.tell(text)


async def tell(ctx: Context, params: object) -> dict:
    text = str(_as_dict(params).get("text", "")).strip()
    if not text:
        raise RpcError(INVALID_PARAMS, "tell requires a non-empty 'text'")
    with ctx.governor.admission():
        reply = await ctx.pool.run(_tell_blocking, text)
    return {
        "text": reply.text,
        "source": reply.source.value,
        "hits": len(reply.hits),
    }


def _memory_stats_blocking() -> dict:
    from utah import memory

    backend = memory.get_backend()
    counts = getattr(backend, "live_counts", None)
    return counts() if counts else {}


async def memory_stats(ctx: Context, params: object) -> dict:
    """Live memory gauges for the dashboard (real counts, off-loop)."""
    with ctx.governor.admission():
        return await ctx.pool.run(_memory_stats_blocking)


def _memory_list_blocking(limit: int, offset: int) -> list:
    from utah import memory

    fn = getattr(memory.get_backend(), "list_memories", None)
    return fn(limit, offset) if fn else []


async def memory_list(ctx: Context, params: object) -> dict:
    """The actual live memory ROWS behind the gauge — deck drill-down (off-loop)."""
    p = _as_dict(params)
    limit = max(1, min(int(p.get("limit", 50)), 200))
    offset = max(0, int(p.get("offset", 0)))
    with ctx.governor.admission():
        return {"rows": await ctx.pool.run(_memory_list_blocking, limit, offset)}


def _entities_blocking(limit: int) -> list:
    from utah import memory

    fn = getattr(memory.get_backend(), "list_entities", None)
    return fn(limit) if fn else []


async def memory_entities(ctx: Context, params: object) -> dict:
    """The actual entities behind the gauge — deck drill-down (off-loop)."""
    p = _as_dict(params)
    limit = max(1, min(int(p.get("limit", 100)), 500))
    with ctx.governor.admission():
        return {"entities": await ctx.pool.run(_entities_blocking, limit)}


def _busy(seconds: float) -> float:
    t = time.perf_counter()
    time.sleep(seconds)  # a real blocking unit of work, off the loop
    return round(time.perf_counter() - t, 3)


async def agent(ctx: Context, params: object) -> dict:
    """Run a real off-loop task in the pool (the no-loop-blocking gate)."""
    seconds = float(_as_dict(params).get("seconds", 0.2))
    seconds = max(0.0, min(seconds, 30.0))  # bounded: never a forever task
    with ctx.governor.admission():
        ran = await ctx.pool.run(_busy, seconds)
    return {"ran_s": ran}


async def shutdown(ctx: Context, params: object) -> dict:
    """Trigger a graceful drain + verified exit (the daemon awaits this event)."""
    ctx.shutdown.set()
    return {"stopping": True}


REGISTRY = {
    "ping": ping,
    "status": status,
    "tell": tell,
    "agent": agent,
    "memory_stats": memory_stats,
    "memory_list": memory_list,
    "memory_entities": memory_entities,
    "publish": publish,
    "shutdown": shutdown,
}
