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


def _ledger_snapshot_blocking(limit: int) -> dict:
    """One read of the product ledger: counts + recent rows per revenue domain.
    The Postgres schema Ace's producers transition into (leads/probate/outreach/fires)."""
    from utah.product.ledger import get_ledger

    lg = get_ledger()
    return {
        "counts": lg.counts(),
        "leads": lg.recent("leads", limit),
        "probate": lg.recent("probate", limit),
        "outreach": lg.recent("outreach", limit),
        "fires": lg.recent("fires", limit),
    }


async def ledger_snapshot(ctx: Context, params: object) -> dict:
    """Live product-ledger snapshot for the deck's revenue panels (off-loop, governed)."""
    limit = max(1, min(int(_as_dict(params).get("limit", 8)), 100))
    with ctx.governor.admission():
        return await ctx.pool.run(_ledger_snapshot_blocking, limit)


def _scout_leads_blocking() -> dict:
    """Run the leads capability against live OSM, writing the ledger (which pushes the
    deck). The daemon's ledger singleton was built with publish=bus.publish at boot."""
    from utah.product import leads
    from utah.product.ledger import get_ledger

    return leads.scout(get_ledger())


async def scout_leads(ctx: Context, params: object) -> dict:
    """Capability (not an agent): find local no-website SMBs and write them to the
    ledger. Triggerable on demand; each new lead lights the deck by push."""
    with ctx.governor.admission():
        return await ctx.pool.run(_scout_leads_blocking)


def _scout_probate_blocking() -> dict:
    from utah.product import probate
    from utah.product.ledger import get_ledger

    return probate.scout(get_ledger())


async def scout_probate(ctx: Context, params: object) -> dict:
    """Capability (not an agent): scrape GPN for ring estate/probate notices and write
    them to the ledger. Fragile source — every failure is documented to the AUDIT log."""
    with ctx.governor.admission():
        return await ctx.pool.run(_scout_probate_blocking)


def _queue_outreach_blocking(campaign: str) -> dict:
    import psycopg

    from utah import config
    from utah.product import outreach
    from utah.product.ledger import get_ledger

    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        rows = c.execute("SELECT name, kind, contact FROM leads").fetchall()
    leads = [{"name": r[0], "kind": r[1], "contact": r[2] or {}} for r in rows]
    return outreach.queue(get_ledger(), campaign, leads)


async def queue_outreach(ctx: Context, params: object) -> dict:
    """Capability (not an agent): compose + lint + suppression-queue outreach for the
    ledger's leads. Send stays GATED (documented) until Michael's creds/address land."""
    campaign = str(_as_dict(params).get("campaign", "smb_no_website"))
    with ctx.governor.admission():
        return await ctx.pool.run(_queue_outreach_blocking, campaign)


def _research_blocking(query: str, k: int) -> dict:
    from utah.product import researcher

    return researcher.research(query, k=k)


async def research(ctx: Context, params: object) -> dict:
    """Capability (not an agent): web search → fetch → extract durable facts (grounded) →
    store in memory. Failures (block/empty/fetch) documented to the AUDIT log."""
    p = _as_dict(params)
    query = str(p.get("query", "")).strip()
    if not query:
        raise RpcError(INVALID_PARAMS, "research requires a 'query'")
    k = max(1, min(int(p.get("k", 4)), 8))
    with ctx.governor.admission():
        return await ctx.pool.run(_research_blocking, query, k)


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
    "ledger_snapshot": ledger_snapshot,
    "scout_leads": scout_leads,
    "scout_probate": scout_probate,
    "queue_outreach": queue_outreach,
    "research": research,
    "publish": publish,
    "shutdown": shutdown,
}
