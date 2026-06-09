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


def _scout_frontier_blocking() -> dict:
    from utah.product import leads
    from utah.product.ledger import get_ledger

    return leads.scout_frontier(get_ledger())


async def scout_frontier(ctx: Context, params: object) -> dict:
    """Capability: tile the metro frontier and scout each tile -> record_lead.
    Ungated (free OSM); dedup at the ledger UNIQUE(name, region)."""
    with ctx.governor.admission():
        return await ctx.pool.run(_scout_frontier_blocking)


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
    from utah.product.ledger import SMB_LEAD_SOURCES, SMB_OUTREACH_CAMPAIGN, get_ledger

    if campaign != SMB_OUTREACH_CAMPAIGN:
        return {"campaign": campaign, "queued": 0, "sent": 0,
                "gated": "RPC outreach is SMB-only (osm/google_maps); probate is separate"}
    sources = tuple(SMB_LEAD_SOURCES)
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        rows = c.execute(
            "SELECT name, kind, contact, source FROM leads WHERE source = ANY(%s)",
            (list(sources),),
        ).fetchall()
    leads = [{"name": r[0], "kind": r[1], "contact": r[2] or {}, "source": r[3]} for r in rows]
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


def _brief_blocking() -> dict:
    from utah.product import brief

    return brief.run(speak_fn=None, can_email=False)   # compose from live state; email gated


async def morning_brief(ctx: Context, params: object) -> dict:
    """Capability (not an agent): compose a brief from live Utah state. Email delivery is
    gated on Michael's Gmail creds (documented); the composed brief is returned + speakable."""
    with ctx.governor.admission():
        return await ctx.pool.run(_brief_blocking)


def _watchdog_blocking() -> dict:
    from utah import watchdog
    return watchdog.check()


async def watchdog_check(ctx: Context, params: object) -> dict:
    """Capability (not an agent): health snapshot of Utah's own live state; genuine
    anomalies (daemon down, load critical) are documented to the AUDIT log."""
    with ctx.governor.admission():
        return await ctx.pool.run(_watchdog_blocking)


def _maintenance_blocking() -> dict:
    from utah import maintenance
    return maintenance.run()


async def maintenance_run(ctx: Context, params: object) -> dict:
    """Capability (not an agent): consolidate turns->facts + decay/archive faded memory.
    Brain/consolidate failures documented to the AUDIT log."""
    with ctx.governor.admission():
        return await ctx.pool.run(_maintenance_blocking)


def _run_engines_blocking() -> dict:
    from utah.product import trading
    from utah.product.ledger import get_ledger
    return trading.run(get_ledger())


async def run_engines(ctx: Context, params: object) -> dict:
    """Capability (not an agent): evaluate engine signals on the live feed -> record_fire.
    GATED on the WealthCharts feed; 0 fires + documented until Michael's WC login lands."""
    with ctx.governor.admission():
        return await ctx.pool.run(_run_engines_blocking)


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



# --- transparency drill-down: real detail behind EVERY deck panel -----------
def _sys_detail() -> dict:
    import os
    import psutil
    per = [round(x, 1) for x in psutil.cpu_percent(interval=0.15, percpu=True)]
    procs = []
    for pr in psutil.process_iter(["pid", "name", "cpu_percent"]):
        procs.append(pr.info)
    procs.sort(key=lambda x: -(x.get("cpu_percent") or 0.0))
    l1, l5, l15 = os.getloadavg()
    return {"per_core_pct": per, "load1": round(l1, 2), "load5": round(l5, 2),
            "load15": round(l15, 2),
            "top_processes": [{"pid": q["pid"], "name": q.get("name"),
                               "cpu": round(q.get("cpu_percent") or 0.0, 1)} for q in procs[:8]]}


def _ledger_recent(domain: str, limit: int) -> list:
    from utah.product.ledger import get_ledger
    return get_ledger().recent(domain, limit)


def _audit_detail(limit: int) -> list:
    from utah import failures
    return [{"ts": r.ts, "source": r.source, "kind": r.kind, "detail": r.detail}
            for r in failures.recent(limit)]


def _memory_detail() -> dict:
    from utah import memory
    b = memory.get_backend()
    return {"counts": b.live_counts(), "rows": b.list_memories(200, 0),
            "entities": b.list_entities(300)}


async def panel_detail(ctx: Context, params: object) -> dict:
    """Real detail behind a deck panel (the transparency rule: every panel clickable ->
    underlying truth). pool->16 slots, governor->18 cores+load+top procs, leads/probate/
    outreach/engines->rows, audit->failures, memory->rows+entities, spine/voice->state."""
    panel = str(_as_dict(params).get("panel", "")).strip()
    if panel == "pool":
        return {"panel": "pool", "limit": ctx.pool.limit, "busy": ctx.pool.borrowed,
                "idle": ctx.pool.available,
                "slots": [{"slot": i, "state": "busy" if i < ctx.pool.borrowed else "idle"}
                          for i in range(ctx.pool.limit)]}
    if panel == "governor":
        gov = ctx.governor.snapshot()
        with ctx.governor.admission():
            sysd = await ctx.pool.run(_sys_detail)
        return {"panel": "governor", **gov, **sysd}
    if panel == "spine":
        return {"panel": "spine", "version": ctx.version, "uptime_s": round(ctx.uptime_s, 1),
                "draining": ctx.shutdown.is_set(), "bus_subscribers": ctx.bus.subscribers,
                "bus_published": ctx.bus.published, "bus_dropped": ctx.bus.dropped}
    if panel in ("leads", "probate", "outreach", "engines"):
        dom = "fires" if panel == "engines" else panel
        with ctx.governor.admission():
            return {"panel": panel, "rows": await ctx.pool.run(_ledger_recent, dom, 50)}
    if panel == "audit":
        with ctx.governor.admission():
            return {"panel": "audit", "rows": await ctx.pool.run(_audit_detail, 50)}
    if panel == "memory":
        with ctx.governor.admission():
            return {"panel": "memory", **(await ctx.pool.run(_memory_detail))}
    if panel == "voice":
        from utah.voice import state
        return {"panel": "voice", **state.status()}
    if panel == "tasks":
        from utah.product import tasks
        with ctx.governor.admission():
            return {"panel": "tasks", "rows": await ctx.pool.run(lambda: tasks.list_tasks("open", 50))}
    if panel == "trackers":
        from utah.product import trackers
        with ctx.governor.admission():
            return {"panel": "trackers", "rows": await ctx.pool.run(lambda: trackers.recent("journal", 30))}
    if panel == "watchdog":
        from utah import watchdog
        with ctx.governor.admission():
            return {"panel": "watchdog", **(await ctx.pool.run(watchdog.check))}
    if panel in ("trading", "lab"):
        from utah.product import trading
        from utah.product.ledger import Ledger
        def _lab():
            try:
                lg = Ledger()
                fires = lg.counts().get("fires", 0)
                rows = lg.recent("fires", 30)
            except Exception:
                fires, rows = 0, []
            return {"panel": "trading", **trading.lab_state(fires), "rows": rows}
        with ctx.governor.admission():
            return await ctx.pool.run(_lab)
    if panel == "mail":
        from utah import mail
        rdy = mail.creds_available()
        return {"panel": "mail", "status": "ready" if rdy else "gated",
                "note": "ready" if rdy else "GATED: drop ~/.utah/secrets/gmail.json"}
    if panel == "marketer":
        from utah.product import marketer
        from utah.product.ledger import Ledger
        def _mk():
            try:
                rows = Ledger().recent("marketer", 15)
            except Exception:  # noqa: BLE001
                rows = []
            return {"panel": "marketer", "rows": rows,
                    "instagram": "ready" if marketer.creds_available("instagram") else "gated",
                    "tiktok": "ready" if marketer.creds_available("tiktok") else "gated",
                    "note": "email_spotlight LIVE (mail.send); IG/TikTok gated on creds"}
        with ctx.governor.admission():
            return await ctx.pool.run(_mk)
    if panel == "research":
        from utah import memory
        with ctx.governor.admission():
            hits = await ctx.pool.run(lambda: memory.get_backend().list_memories(20, 0))
        return {"panel": "research", "rows": hits, "note": "facts learned (web -> memory)"}
    if panel == "selfcode":
        from utah import selfcode, sica
        on = selfcode.enabled()
        am = selfcode.automerge_enabled()
        smoke = selfcode.kill_switch_smoke()    # live proof: refuses self-edits to safety
        _arch = sica.Archive()
        _best = _arch.best()
        return {"panel": "selfcode",
                # SICA governance: every attempt is utility-scored + archived.
                "sica": {"archived": _arch.count(),
                         "best_utility": (_best or {}).get("utility"),
                         "time_limit_s": sica.TIME_LIMIT_S, "cost_limit_usd": sica.COST_LIMIT_USD},
                "status": ("armed" if am else "propose-only") if on else "kill-switch",
                "enabled": on,
                "automerge": "ARMED — green merges to main + pushes origin" if am
                             else "OFF — proposes on a branch (human merges)",
                "mode": "auto-merge on green · suite-gated · kill-switch" if am
                        else "propose-only · isolated branch · suite-gated · never main",
                "kill_switch": str(selfcode.KILL_SWITCH),
                # Doc-13 tiered policy-as-data, surfaced for the transparency rule.
                "tiers": [{"tier": t, "label": selfcode.POLICY[t]["label"],
                           "automerge": selfcode.POLICY[t]["automerge"]}
                          for t in selfcode.TIER_ORDER],
                "supervised": selfcode._read_supervised(),
                "tier_A_needs": selfcode.POLICY["A"]["min_supervised"],
                "safety_files": list(selfcode.SAFETY_PATHS),
                "kill_switch_smoke": "PASS — refuses self-edits to safety" if smoke["refused"]
                                     else "FAIL — safety not enforced",
                "note": ("autonomy: Tier-A green proposals auto-merge after N supervised; "
                         "B/C batch-review, D off-limits; kill-switch overrides") if am
                        else "bounded: proposes a change on a branch, never merges"}
    if panel == "browser":
        from utah.integrations import browser
        b = browser.chrome_binary()
        return {"panel": "browser",
                "status": "ready" if b else "gated",
                "engine": "headless chrome · --dump-dom (JS-rendered DOM)",
                "binary": b or "",
                "note": "ready — JS-rendered fetch live" if b else "GATED: no Chrome found"}
    if panel == "selfcode_cycles":
        # SELF-CODE page · "correcting in himself" — recent self-code cycles from the
        # selfcode_log table (task/domain/utility/passed/merged), real-or-empty.
        from utah.product import selfcode_web
        with ctx.governor.admission():
            rows = await ctx.pool.run(lambda: selfcode_web.recent_cycles(30))
            stats = await ctx.pool.run(selfcode_web.cycle_stats)
        return {"panel": "selfcode_cycles", "rows": rows, "stats": stats,
                "note": "self-code cycles (selfcode_log) — what he's correcting in himself"}
    if panel == "selfcode_goals":
        # SELF-CODE page · "percentage goals from PRs" — every % traces to a real git
        # commit count or selfcode_log row count (never fabricated).
        from utah.product import selfcode_web
        with ctx.governor.admission():
            gls = await ctx.pool.run(selfcode_web.goals)
        return {"panel": "selfcode_goals", "goals": gls,
                "note": "progress wired off real selfcode(auto) commits + cycle outcomes"}
    return {"panel": panel, "rows": []}


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
    "scout_frontier": scout_frontier,
    "scout_probate": scout_probate,
    "queue_outreach": queue_outreach,
    "research": research,
    "morning_brief": morning_brief,
    "watchdog_check": watchdog_check,
    "maintenance_run": maintenance_run,
    "run_engines": run_engines,
    "panel_detail": panel_detail,
    "publish": publish,
    "shutdown": shutdown,
}
