"""Deck panel drill-down — one handler per panel, registered by name.

Every clickable deck panel maps to a handler here. ``panel_detail`` dispatches
through :data:`PANEL_REGISTRY`; no inline ``if panel ==`` sprawl.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

try:
    import psutil
except ImportError:  # degraded gauge, never a dead panel module
    psutil = None  # type: ignore[assignment]

from utah.daemon.dispatch import Context

PanelHandler = Callable[[Context], Awaitable[dict]]


def _sys_detail() -> dict:
    """Live per-core/process gauges for the governor drill-down.

    Honest degradation: a missing psutil or a down loadavg gauge is reported
    as empty/None plus a ``note`` — never fabricated zeros that read as "idle".
    """
    try:
        l1, l5, l15 = (round(x, 2) for x in os.getloadavg())
    except OSError:  # "load average unobtainable" — report the gauge as down
        l1 = l5 = l15 = None
    out: dict = {"load1": l1, "load5": l5, "load15": l15}
    if psutil is None:
        out.update(per_core_pct=[], top_processes=[],
                   note="DEGRADED: psutil unavailable — per-core/process gauges off")
        return out
    per = [round(x, 1) for x in psutil.cpu_percent(interval=0.15, percpu=True)]
    procs = sorted(
        (pr.info for pr in psutil.process_iter(["pid", "name", "cpu_percent"])),
        key=lambda x: -(x.get("cpu_percent") or 0.0),
    )
    out.update(
        per_core_pct=per,
        top_processes=[
            {
                "pid": q["pid"],
                "name": q.get("name"),
                "cpu": round(q.get("cpu_percent") or 0.0, 1),
            }
            for q in procs[:8]
        ],
    )
    return out


def _ledger_recent(domain: str, limit: int) -> list:
    from utah.product.ledger import get_ledger

    return get_ledger().recent(domain, limit)


def _audit_detail(limit: int) -> list:
    from utah import failures

    return [
        {"ts": r.ts, "source": r.source, "kind": r.kind, "detail": r.detail}
        for r in failures.recent(limit)
    ]


def _memory_detail() -> dict:
    from utah import memory

    backend = memory.get_backend()
    return {
        "counts": backend.live_counts(),
        "rows": backend.list_memories(200, 0),
        "entities": backend.list_entities(300),
    }


async def _panel_pool(ctx: Context) -> dict:
    return {
        "panel": "pool",
        "limit": ctx.pool.limit,
        "busy": ctx.pool.borrowed,
        "idle": ctx.pool.available,
        "slots": [
            {"slot": i, "state": "busy" if i < ctx.pool.borrowed else "idle"}
            for i in range(ctx.pool.limit)
        ],
    }


async def _panel_governor(ctx: Context) -> dict:
    gov = ctx.governor.snapshot()
    with ctx.governor.read_admission():
        sysd = await ctx.pool.run(_sys_detail)
    return {"panel": "governor", **gov, **sysd}


async def _panel_spine(ctx: Context) -> dict:
    return {
        "panel": "spine",
        "version": ctx.version,
        "uptime_s": round(ctx.uptime_s, 1),
        "draining": ctx.shutdown.is_set(),
        "bus_subscribers": ctx.bus.subscribers,
        "bus_published": ctx.bus.published,
        "bus_dropped": ctx.bus.dropped,
    }


async def _panel_ledger(ctx: Context, *, panel: str, domain: str) -> dict:
    with ctx.governor.read_admission():
        rows = await ctx.pool.run(_ledger_recent, domain, 50)
    return {"panel": panel, "rows": rows}


async def _panel_leads(ctx: Context) -> dict:
    return await _panel_ledger(ctx, panel="leads", domain="leads")


async def _panel_probate(ctx: Context) -> dict:
    return await _panel_ledger(ctx, panel="probate", domain="probate")


async def _panel_outreach(ctx: Context) -> dict:
    return await _panel_ledger(ctx, panel="outreach", domain="outreach")


async def _panel_engines(ctx: Context) -> dict:
    return await _panel_ledger(ctx, panel="engines", domain="fires")


async def _panel_audit(ctx: Context) -> dict:
    with ctx.governor.read_admission():
        rows = await ctx.pool.run(_audit_detail, 50)
    return {"panel": "audit", "rows": rows}


async def _panel_memory(ctx: Context) -> dict:
    with ctx.governor.read_admission():
        detail = await ctx.pool.run(_memory_detail)
    return {"panel": "memory", **detail}


async def _panel_voice(ctx: Context) -> dict:
    from utah.voice import state

    return {"panel": "voice", **state.status()}


async def _panel_tasks(ctx: Context) -> dict:
    from utah.product import tasks

    with ctx.governor.read_admission():
        rows = await ctx.pool.run(lambda: tasks.list_tasks("open", 50))
    return {"panel": "tasks", "rows": rows}


async def _panel_trackers(ctx: Context) -> dict:
    from utah.product import trackers

    with ctx.governor.read_admission():
        rows = await ctx.pool.run(lambda: trackers.recent("journal", 30))
    return {"panel": "trackers", "rows": rows}


async def _panel_watchdog(ctx: Context) -> dict:
    from utah import watchdog

    with ctx.governor.read_admission():
        detail = await ctx.pool.run(watchdog.check)
    return {"panel": "watchdog", **detail}


async def _panel_trading(ctx: Context) -> dict:
    from utah.product import trading
    from utah.product.ledger import Ledger

    def _lab() -> dict:
        try:
            lg = Ledger()
            fires = lg.counts().get("fires", 0)
            rows = lg.recent("fires", 30)
        except Exception:  # noqa: BLE001
            fires, rows = 0, []
        try:
            ticks = lg.live_ticks()
        except Exception:  # noqa: BLE001 — surface may predate the wc_live table
            ticks = []
        # Real per-engine truth: GROUP BY totals + graded scorecard, not a count
        # over the 30-row recents window (18 'fires' beside a 2,044 ledger total).
        try:
            totals = lg.fires_by_engine()
            by_engine = {k: v["total"] for k, v in totals.items()}
            score = {n: lg.engine_scorecard(n) for n in trading.implemented_engines()}
        except Exception:  # noqa: BLE001 — minimal ledgers fall back to the window
            from collections import Counter
            totals, score = {}, {}
            by_engine = dict(Counter((r.get("engine") or "?") for r in rows))
        state = trading.lab_state(fires, by_engine=by_engine)
        for e in state["engines"]:
            e.update(score.get(e["name"], {}))
            e["fires_24h"] = totals.get(e["name"], {}).get("last_24h", 0)
        return {"panel": "trading", **state, "rows": rows, "recent_window": 30,
                "ticks": ticks}

    with ctx.governor.read_admission():
        return await ctx.pool.run(_lab)


async def _panel_mail(ctx: Context) -> dict:
    from utah import mail

    ready = mail.creds_available()
    return {
        "panel": "mail",
        "status": "ready" if ready else "gated",
        "note": "ready" if ready else "GATED: drop ~/.utah/secrets/gmail.json",
    }


async def _panel_marketer(ctx: Context) -> dict:
    from utah.integrations import social_post
    from utah.product.ledger import Ledger

    def _mk() -> dict:
        try:
            rows = Ledger().recent("marketer", 15)
        except Exception:  # noqa: BLE001
            rows = []
        snap = social_post.status()
        return {
            "panel": "marketer",
            "rows": rows,
            "email": snap["email"],
            "instagram": snap["channels"]["instagram"],
            "tiktok": snap["channels"]["tiktok"],
            "channels": snap["channels"],
            "note": snap["note"],
        }

    with ctx.governor.read_admission():
        return await ctx.pool.run(_mk)


async def _panel_research(ctx: Context) -> dict:
    from utah import memory

    with ctx.governor.read_admission():
        rows = await ctx.pool.run(lambda: memory.get_backend().list_memories(20, 0))
    return {
        "panel": "research",
        "rows": rows,
        "note": "facts learned (web -> memory)",
    }


async def _panel_selfcode(ctx: Context) -> dict:
    from utah import selfcode, sica

    on = selfcode.enabled()
    automerge = selfcode.automerge_enabled()
    smoke = selfcode.kill_switch_smoke()
    archive = sica.Archive()
    best = archive.best()
    return {
        "panel": "selfcode",
        "sica": {
            "archived": archive.count(),
            "best_utility": (best or {}).get("utility"),
            "time_limit_s": sica.TIME_LIMIT_S,
            "cost_limit_usd": sica.COST_LIMIT_USD,
        },
        "status": ("armed" if automerge else "propose-only") if on else "kill-switch",
        "enabled": on,
        "automerge": (
            "ARMED — green merges to main + pushes origin"
            if automerge
            else "OFF — proposes on a branch (human merges)"
        ),
        "mode": (
            "auto-merge on green · suite-gated · kill-switch"
            if automerge
            else "propose-only · isolated branch · suite-gated · never main"
        ),
        "kill_switch": str(selfcode.KILL_SWITCH),
        "tiers": [
            {
                "tier": tier,
                "label": selfcode.POLICY[tier]["label"],
                "automerge": selfcode.POLICY[tier]["automerge"],
            }
            for tier in selfcode.TIER_ORDER
        ],
        "supervised": selfcode._read_supervised(),
        "tier_A_needs": selfcode.POLICY["A"]["min_supervised"],
        "safety_files": list(selfcode.SAFETY_PATHS),
        "kill_switch_smoke": (
            "PASS — refuses self-edits to safety"
            if smoke["refused"]
            else "FAIL — safety not enforced"
        ),
        "note": (
            "autonomy: Tier-A green proposals auto-merge after N supervised; "
            "B/C batch-review, D off-limits; kill-switch overrides"
            if automerge
            else "bounded: proposes a change on a branch, never merges"
        ),
    }


async def _panel_browser(ctx: Context) -> dict:
    from utah.integrations import browser

    binary = browser.chrome_binary()
    ready = bool(binary)
    return {
        "panel": "browser",
        "status": "ready" if ready else "gated",
        "engine": "headless chrome · --dump-dom (JS-rendered DOM)",
        "binary": binary or "",
        "note": "ready — JS-rendered fetch live" if ready else "GATED: no Chrome found",
    }


async def _panel_selfcode_cycles(ctx: Context) -> dict:
    from utah.product import selfcode_web

    with ctx.governor.read_admission():
        rows = await ctx.pool.run(lambda: selfcode_web.recent_cycles(30))
        stats = await ctx.pool.run(selfcode_web.cycle_stats)
    return {
        "panel": "selfcode_cycles",
        "rows": rows,
        "stats": stats,
        "note": "self-code cycles (selfcode_log) — what he's correcting in himself",
    }


async def _panel_selfcode_goals(ctx: Context) -> dict:
    from utah.product import selfcode_web

    with ctx.governor.read_admission():
        goals = await ctx.pool.run(selfcode_web.goals)
    return {
        "panel": "selfcode_goals",
        "goals": goals,
        "note": "progress wired off real selfcode(auto) commits + cycle outcomes",
    }


PANEL_REGISTRY: dict[str, PanelHandler] = {
    "pool": _panel_pool,
    "governor": _panel_governor,
    "spine": _panel_spine,
    "leads": _panel_leads,
    "probate": _panel_probate,
    "outreach": _panel_outreach,
    "engines": _panel_engines,
    "audit": _panel_audit,
    "memory": _panel_memory,
    "voice": _panel_voice,
    "tasks": _panel_tasks,
    "trackers": _panel_trackers,
    "watchdog": _panel_watchdog,
    "trading": _panel_trading,
    "lab": _panel_trading,
    "mail": _panel_mail,
    "marketer": _panel_marketer,
    "research": _panel_research,
    "selfcode": _panel_selfcode,
    "browser": _panel_browser,
    "selfcode_cycles": _panel_selfcode_cycles,
    "selfcode_goals": _panel_selfcode_goals,
}
