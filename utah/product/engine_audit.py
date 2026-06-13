"""Nightly engine audit — a full per-(engine, symbol) edge review of the trading fleet.

Michael's standing order (2026-06-13): every night, audit the engines, give a full review,
converge over 6 months. This module is that review. For every implemented engine × every
backtestable symbol it runs the held-out OOS backtest (via ``trading._backtest_engine``),
records the verdict (edge proven? win% / net / sample / why), and:

  • :func:`audit`        — pure core, all boundaries injected, returns the fleet matrix.
  • :func:`render`       — the full human report (markdown).
  • :func:`summary_line` — one line for the nightly brief / push.
  • :func:`run_scheduled`— the cron entry: audit on real bars, persist report, push brief.

Honesty is the whole point: nothing is painted. A pair with no proven edge says so and
says what it would take. The audit is the instrument that makes the 6-month convergence
measurable — each night's report is a dated, comparable snapshot of where the fleet stands.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from utah import failures

log = logging.getLogger("utah.product.engine_audit")

#: Where nightly reports land (json + md, one pair per date). Under ~/.utah so they
#: survive daemon reloads and accumulate into the 6-month trail.
AUDIT_DIR: Path = Path(os.environ.get(
    "UTAH_AUDIT_DIR", os.path.expanduser("~/.utah/audit")))


def audit(*, engines=None, symbols_fn=None, score_fn=None, generated_at: str | None = None) -> dict:
    """Run every *engine* against every symbol and return the fleet edge matrix.

    ``score_fn(engine, symbol) -> scorecard`` is injected (tests pass a table; production
    reads the symbol's real bars and runs the engine's archetype backtest). Defensive per
    pair — a backtest that raises becomes an honest ``edge_proven=False`` row with an
    ``error`` field, never sinking the audit. Pure given its boundaries; never raises."""
    from utah.product import trading

    engs = tuple(engines) if engines is not None else trading.implemented_engines()
    try:
        symbols = list((symbols_fn or trading._backtestable_symbols)() or [])
    except Exception as exc:  # noqa: BLE001 — dead symbol source = empty universe, honest
        failures.record("engine_audit", "symbols_failed", str(exc))
        symbols = []
    scorer = score_fn or _default_score_fn()

    fleet: list[dict] = []
    for engine in engs:
        for symbol in symbols:
            fleet.append(_pair_row(engine, symbol, scorer))
    return {"generated_at": generated_at, "engines": list(engs), "symbols": symbols,
            "fleet": fleet, "summary": _summarize(engs, fleet)}


def _pair_row(engine: str, symbol: str, scorer) -> dict:
    """One (engine, symbol) verdict — never raises (a failed backtest is an honest row)."""
    try:
        sc = scorer(engine, symbol) or {}
    except Exception as exc:  # noqa: BLE001 — one bad pair never sinks the fleet review
        failures.record("engine_audit", "pair_failed", f"{engine}/{symbol}: {exc}")
        return {"engine": engine, "symbol": symbol, "edge_proven": False,
                "win_rate": None, "net_pts": None, "trades": 0,
                "reason": f"backtest error: {exc}", "error": str(exc)}
    return {"engine": engine, "symbol": symbol,
            "edge_proven": bool(sc.get("edge_proven")),
            "win_rate": sc.get("win_rate"), "net_pts": sc.get("net_pts"),
            "trades": sc.get("trades", 0), "total_r": sc.get("total_r"),
            "archetype": sc.get("archetype"), "reason": sc.get("reason", "")}


def _summarize(engines, fleet: list[dict]) -> dict:
    """Fleet-level rollup: proven count, proven symbols grouped by engine, totals."""
    proven = [r for r in fleet if r["edge_proven"]]
    by_engine = {e: sorted(r["symbol"] for r in proven if r["engine"] == e) for e in engines}
    return {"total_pairs": len(fleet), "proven": len(proven),
            "engines_with_edge": sorted(e for e, syms in by_engine.items() if syms),
            "symbols_with_edge": sorted({r["symbol"] for r in proven}),
            "by_engine": by_engine}


def _default_score_fn():
    """Production scorer: read each symbol's real bars ONCE (cached), run each engine's
    archetype backtest on them. Closures over ``trading`` so the import stays lazy."""
    from utah.product import trading

    cache: dict[str, list] = {}

    def fn(engine: str, symbol: str) -> dict:
        if symbol not in cache:
            cache[symbol] = trading._ohlc_bars(symbol)
        return trading._backtest_engine(engine, cache[symbol])

    return fn


def _fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def _fmt_num(v) -> str:
    return f"{v:+.2f}" if isinstance(v, (int, float)) else "—"


def render(result: dict) -> str:
    """The full nightly review as markdown — a dated, comparable snapshot of the fleet."""
    s = result["summary"]
    lines = [
        f"# Trading Engine Audit — {result.get('generated_at') or 'now'}",
        "",
        f"**{s['proven']} of {s['total_pairs']}** (engine,symbol) pairs prove held-out OOS edge.",
        f"Engines with edge: {', '.join(s['engines_with_edge']) or 'none'}.",
        f"Symbols with edge: {', '.join(s['symbols_with_edge']) or 'none'}.",
        "",
        "| engine | symbol | edge | win% | net pts | trades | why |",
        "|---|---|---|---|---|---|---|",
    ]
    # proven first, then by net descending — the deepest, most actionable at the top
    for r in sorted(result["fleet"], key=lambda x: (not x["edge_proven"],
                                                     -(x["net_pts"] or -1e9))):
        mark = "✅ EDGE" if r["edge_proven"] else "—"
        why = (r.get("reason") or "")[:80]
        lines.append(f"| {r['engine']} | {r['symbol']} | {mark} | "
                     f"{_fmt_pct(r['win_rate'])} | {_fmt_num(r['net_pts'])} | "
                     f"{r['trades']} | {why} |")
    return "\n".join(lines) + "\n"


def summary_line(result: dict) -> str:
    """One-line verdict for the nightly brief / push."""
    s = result["summary"]
    edges = s["engines_with_edge"]
    where = (" via " + ", ".join(f"{e}({'/'.join(s['by_engine'][e])})" for e in edges)) if edges else ""
    return (f"Engine audit: {s['proven']}/{s['total_pairs']} pairs prove OOS edge"
            f"{where}.")


def run_scheduled(*, audit_fn=None, sender=None, now=None) -> dict:
    """Cron entry: run the audit on real bars, persist the dated report (json + md),
    push the one-line summary to the brief. Best-effort I/O — a write or push failure is
    recorded, never raised. Returns the summary + the paths written."""
    from datetime import datetime, timezone

    ts = (now or (lambda: datetime.now(timezone.utc)))()
    result = (audit_fn or audit)(generated_at=ts.isoformat())
    stamp = ts.strftime("%Y-%m-%d")
    paths = {}
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        jpath = AUDIT_DIR / f"audit-{stamp}.json"
        mpath = AUDIT_DIR / f"audit-{stamp}.md"
        jpath.write_text(json.dumps(result, indent=2, default=str))
        mpath.write_text(render(result))
        (AUDIT_DIR / "latest.md").write_text(render(result))
        paths = {"json": str(jpath), "md": str(mpath)}
    except Exception as exc:  # noqa: BLE001 — a write failure must not break the cron
        failures.record("engine_audit", "write_failed", str(exc))
    line = summary_line(result)
    try:
        from utah import alerts
        alerts.brief(line, sender=sender)
    except Exception as exc:  # noqa: BLE001 — alerting must never break the cron
        failures.record("engine_audit", "brief_failed", str(exc))
    log.info("engine_audit: %s", line)
    return {"summary": result["summary"], "line": line, "paths": paths}
