"""Trading-engine graph data for the deck's dedicated /trading page.

Turns the raw engine fire log (``~/.utah/cache/engine_fires.jsonl`` — written by
:mod:`utah.integrations.engine_bridge`) into per-engine series the page graphs:
a cumulative session-P&L curve and the individual trades (entry/stop/target,
direction, and realised P&L once closed).

REAL-OR-EMPTY, never painted: an engine with no fires comes back with empty
series and ``fired=False`` so the page shows an honest "no fires yet" state
instead of a fake line. Feed liveness comes from the ledger's live ticks — an
engine is only "producing" when the market is actually flowing; a logged-out
WealthCharts feed reads as dormant, not live (the fake-positive we refuse).

``aggregate`` is pure (events in → dict out) so it is unit-tested without a daemon,
ledger, or file. ``snapshot`` adds best-effort ledger/audit enrichment around it and
never raises — the page degrades to the pure view, it never 500s.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Iterable

log = logging.getLogger("utah.product.engine_graph")

#: The fire log written by the engine bridge — same path/env contract as the bridge.
EVENTS_PATH = os.path.expanduser(
    os.environ.get("UTAH_ENGINE_FIRES_LOG", "~/.utah/cache/engine_fires.jsonl"))

#: A tick older than this means the feed is effectively down — no engine can be "live"
#: (mirrors engine_status._FRESH_MS; calling a weekend-stale feed "live" is the lie).
_FRESH_MS = 5 * 60 * 1000


def parse_events(lines: Iterable[str]) -> list[dict]:
    """Parse JSONL fire events, skipping blank/corrupt lines (a torn write must never
    take down the page). Only dict rows with an ``engine`` are kept."""
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(ev, dict) and ev.get("engine"):
            out.append(ev)
    return out


def _roster() -> list[tuple[str, str]]:
    """(name, description) for every known engine, so the page lists them all — even
    ones that have never fired — instead of hiding a dormant engine."""
    try:
        from utah.product import trading
        return list(trading.ENGINES)
    except Exception:  # noqa: BLE001 — roster is best-effort; fires still graph without it
        return []


def aggregate(events: list[dict], roster: list[tuple[str, str]] | None = None) -> dict:
    """Pure: fire events (+ optional roster) → per-engine graph data.

    Per engine: time-ordered ``pnl_series`` (ts, session_pnl) for the P&L curve, paired
    ``trades`` (open→close with entry/stop/target/direction and realised pnl), live
    ``open_positions``, ``trades_today``, latest ``session_pnl``, ``last_ts`` and
    ``fired``. Engines in the roster with no events appear with empty series.
    """
    by_engine: dict[str, list[dict]] = {}
    for ev in events:
        by_engine.setdefault(str(ev["engine"]), []).append(ev)

    desc = {name: d for name, d in (roster or [])}
    names = list(desc.keys())
    for name in by_engine:  # include engines that fired but aren't in the roster
        if name not in desc:
            names.append(name)

    engines = []
    total_fires = 0
    total_pnl = 0.0
    for name in names:
        evs = sorted(by_engine.get(name, []), key=lambda e: e.get("ts", 0.0))
        pnl_series: list[dict] = []
        trades: list[dict] = []
        open_stack: list[dict] = []
        trades_today = 0
        session_pnl = 0.0
        for ev in evs:
            if "session_pnl" in ev and ev["session_pnl"] is not None:
                try:
                    session_pnl = float(ev["session_pnl"])
                    pnl_series.append({"ts": ev.get("ts"), "pnl": session_pnl})
                except (TypeError, ValueError):
                    pass
            if ev.get("trades_today") is not None:
                try:
                    trades_today = max(trades_today, int(ev["trades_today"]))
                except (TypeError, ValueError):
                    pass
            kind = ev.get("kind")
            if kind == "open":
                open_stack.append(ev)
            elif kind == "close":
                opened = open_stack.pop(0) if open_stack else None
                trades.append({
                    "open_ts": (opened or {}).get("ts"),
                    "close_ts": ev.get("ts"),
                    "direction": (ev.get("direction") or (opened or {}).get("direction")),
                    "entry": (opened or {}).get("entry", ev.get("entry")),
                    "stop": (opened or {}).get("stop"),
                    "target": (opened or {}).get("target"),
                    "session_pnl": ev.get("session_pnl"),
                })
        # closes can outnumber roster trades_today; the count_up is the engine's own tally,
        # so prefer it and fall back to realised-close count.
        trades_today = trades_today or len([t for t in trades])
        engines.append({
            "name": name,
            "desc": desc.get(name, ""),
            "fired": bool(evs),
            "last_ts": evs[-1].get("ts") if evs else None,
            "trades_today": trades_today,
            "session_pnl": round(session_pnl, 2),
            "open_positions": [
                {"direction": o.get("direction"), "entry": o.get("entry"),
                 "stop": o.get("stop"), "target": o.get("target"), "ts": o.get("ts")}
                for o in open_stack
            ],
            "pnl_series": pnl_series,
            "trades": trades,
        })
        total_fires += len(evs)
        total_pnl += session_pnl

    # producing engines first, then by |pnl|, so the page leads with what's actually working
    engines.sort(key=lambda e: (not e["fired"], -abs(e["session_pnl"])))
    return {
        "engines": engines,
        "totals": {
            "fires": total_fires,
            "session_pnl": round(total_pnl, 2),
            "engines_fired": sum(1 for e in engines if e["fired"]),
            "engines_total": len(engines),
        },
    }


def _feed_liveness() -> dict:
    """Best-effort: is the WealthCharts feed flowing? freshest live-tick age in ms, or
    None when there is no tick surface at all. Never raises."""
    try:
        from utah.product.ledger import Ledger
        ages = [t["age_ms"] for t in Ledger().live_ticks() if t.get("age_ms") is not None]
        freshest = min(ages) if ages else None
    except Exception:  # noqa: BLE001
        freshest = None
    return {"freshest_tick_ms": freshest,
            "feed_live": freshest is not None and freshest < _FRESH_MS}


def snapshot(now: float | None = None) -> dict:
    """The full payload for /api/trading: pure ``aggregate`` over the live fire log, plus
    best-effort feed liveness and proven per-engine edges. Degrades to empty-but-honest;
    never raises (the page must render even with no ledger/log)."""
    now = time.time() if now is None else now
    try:
        with open(EVENTS_PATH, encoding="utf-8") as fh:
            events = parse_events(fh)
    except OSError:
        events = []
    data = aggregate(events, _roster())
    data["feed"] = _feed_liveness()
    data["ts"] = now

    # mark each engine live only when the feed is actually flowing AND it fired recently.
    # Honest labels: don't say "market down" when the feed is up — distinguish a live-but-
    # quiet engine (no recent fire) from a genuinely down feed.
    feed_live = data["feed"]["feed_live"]
    for e in data["engines"]:
        recent = e["last_ts"] is not None and (now - float(e["last_ts"])) < 3600
        if feed_live and recent:
            e["state"] = "producing"
        elif e["fired"]:
            e["state"] = "idle (no recent fire)" if feed_live else "idle (market down)"
        else:
            e["state"] = "no fires yet"

    try:  # proven out-of-sample edge per engine, from the nightly audit
        from utah.product import engine_audit
        best = engine_audit.latest().get("best_edges", {}) or {}
        for e in data["engines"]:
            edge = best.get(e["name"])
            if edge and edge.get("symbol"):
                e["edge"] = {"symbol": edge["symbol"],
                             "win_rate": round(edge.get("win_rate", 0) * 100),
                             "net_pts": round(edge.get("net_pts", 0), 1)}
    except Exception:  # noqa: BLE001 — edges are a bonus, never block the page
        pass
    return data


__all__ = ["EVENTS_PATH", "parse_events", "aggregate", "snapshot"]
