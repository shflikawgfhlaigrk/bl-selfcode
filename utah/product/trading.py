"""Trading capability — engine signals into the fires ledger.

Ace's trading cluster (trader/trading_ooda/commentary/prediction/...) transitions HERE as a
capability behind the brain, not an agent. Ready-skeleton, GATED on the WealthCharts market
feed (Michael's WC Chrome login). The pipeline is wired: feed → :func:`evaluate` →
``ledger.record_fire``. A reference BREAKOUT engine is the starter; Ace's 8 engines (apex,
shadow, perp_v2, …) drop additional rules onto the same ``evaluate`` interface when the feed
is live. With no feed it records ZERO fires and documents the gate — faking fires is
forbidden (the ledger's ``synthetic`` flag exists for exactly that line).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from utah import failures

log = logging.getLogger("utah.product.trading")

#: The APEX DASH CONFIG file — the single source of truth the deck's Trading Engine Lab
#: bars live-update from. :func:`fleet_backtest` WRITES the per-engine held-out OOS
#: scorecards here (every number measured from the real ``bars`` table); the deck reads
#: them every poll via :func:`dash_config`, so the lab never re-runs a backtest inline
#: and never paints a number. Under ~/.utah so it survives across daemon reloads.
APEX_DASH_CONFIG: Path = Path(
    os.environ.get("UTAH_APEX_DASH_CONFIG",
                   os.path.expanduser("~/.utah/cache/apex_dash.json")))

#: sentinel so ``lab_state(backtests=None)`` (hermetic unit tests) means "no stats",
#: while ``lab_state()`` (the deck) means "auto-load the measured config from disk".
_AUTO = object()

#: The engine roster merged from Ace's live fleet (fleet_grader.ENGINES) plus the
#: reference ``breakout`` starter. Each drops rules onto the same :func:`evaluate`
#: interface; all stay DORMANT until the WealthCharts feed lands (no synthetic fires).
ENGINES = (
    ("breakout",  "reference · prior-N high/low breakout (live starter)"),
    ("meanrev",   "mean-reversion · fades a 2σ z-score extreme (live)"),
    ("shadow",    "shadow book · confirmation overlay"),
    ("ctx_alpha", "context engine — alpha regime"),
    ("ctx_bravo", "context engine — bravo regime"),
    ("barber",    "barber regime filter"),
    ("perp",      "perp / perp_v2 momentum"),
    ("research",  "research-signal engine"),
    ("bible",     "rule-bible engine"),
    ("antigrav",  "antigrav emoji-signal engine"),
)


#: Minimum flat time between fires for ONE engine, seconds. 15 min ≈ the bar regime
#: the engines read (20×1-min lookback): re-arming faster than this re-trades the
#: same structure. Set after the 2026-06-10 storm (749 fires/day). Lives here (not
#: config.py) so the engine's contract is self-contained.
FIRE_COOLDOWN_S = float(os.environ.get("UTAH_FIRE_COOLDOWN_S", "900"))


def _breakout(closes: list[float], lookback: int) -> dict | None:
    prior = closes[-lookback - 1:-1]
    last = closes[-1]
    if last > max(prior):
        return {"engine": "breakout", "direction": "long", "entry": last}
    if last < min(prior):
        return {"engine": "breakout", "direction": "short", "entry": last}
    return None


#: z-score band the mean-reversion engine fades. 2σ = a genuine statistical extreme on
#: the lookback window, not noise; zero-variance windows can never fire.
MEANREV_Z = 2.0


def _meanrev(closes: list[float], lookback: int) -> dict | None:
    prior = closes[-lookback - 1:-1]
    last = closes[-1]
    mean = sum(prior) / len(prior)
    var = sum((c - mean) ** 2 for c in prior) / len(prior)
    if var <= 0.0:
        return None
    z = (last - mean) / (var ** 0.5)
    if z <= -MEANREV_Z:
        return {"engine": "meanrev", "direction": "long", "entry": last}
    if z >= MEANREV_Z:
        return {"engine": "meanrev", "direction": "short", "entry": last}
    return None


#: Engines with REAL rules. Everything else on the roster is an honest nameplate
#: awaiting its port from the Ace bundles — the deck must never show it live
#: (2026-06-10: feed-live flipped all nine to LIVE while one produced).
ENGINE_RULES = {"breakout": _breakout, "meanrev": _meanrev}


#: Each implemented engine's BACKTEST archetype — the function (in backtest.py) that
#: proves edge on the real bars, plus the human label the deck shows. THE numbers shown
#: on the apex dash are the held-out OOS stats this produces live; nothing is ever painted.
#: No win%/net is hardcoded here ON PURPOSE: it drifts as the live feed appends bars, so any
#: literal would go stale and become a lie. meanrev is the high-win mean-reversion archetype
#: (fade a z-extreme to a tight target behind a wide stop); breakout is the trend archetype.
#: The real, current stats are whatever ``fleet_backtest`` / ``prove_meanrev`` measure now.
ENGINE_ARCHETYPE = {
    "meanrev": "mean-reversion",
    "breakout": "breakout",
}

#: Default mean-reversion config — a single robust cfg held out-of-sample (NOT fitted
#: per-symbol live, which would overfit). ``max_hold=80`` bars (20 min on 15s bars) is the
#: wide-stop tail-cap: a structural bound on the rare unbounded drawdown, not a curve-fit.
#: ``win_floor=0.87`` is the GOAL gate — ``edge_proven`` stays False until the live OOS
#: actually clears it AND is net-positive, so the dash never shows a proven edge it lacks.
#: ``min_trades`` is the SAMPLE FLOOR for "edge proven" — the fire gate's guard against
#: betting on noise. Proven live 2026-06-13: at min_trades=1 the breakout gate passed on
#: 4–11-trade "edges" (CM.NQM6 n=4 win=0.50, CM.MNQM6 n=11 net+1.6R) — pure small-sample
#: luck, exactly the overfit the edge gate exists to refuse. At a 20-trade OOS floor the
#: only engine/symbol that proves edge on the current ~3-day bar history is mean-reversion
#: on NQ futures (CM.NQM6: OOS win 88%, +491 pts, n=25) — the robust winner the parameter
#: sweep also found. Raise as the captured history deepens.
EDGE_MIN_TRADES = int(os.environ.get("UTAH_EDGE_MIN_TRADES", "20"))
MEANREV_CFG = {"lookback": 20, "z_enter": 2.0, "tgt_frac": 0.6, "stop_mult": 8.0,
               "win_floor": 0.87, "oos_frac": 0.4, "min_trades": EDGE_MIN_TRADES,
               "max_hold": 80}
BREAKOUT_CFG = {"lookback": 20, "target_r": 2.0, "min_trades": EDGE_MIN_TRADES}


def implemented_engines() -> tuple[str, ...]:
    return tuple(ENGINE_RULES)


def _empty_scorecard(engine: str) -> dict:
    """Honest empty backtest — what the dash shows before the bars exist. NEVER a
    fabricated win rate (None, not 0.0, so the deck renders '—' not a fake 0%)."""
    return {"engine": engine, "archetype": ENGINE_ARCHETYPE.get(engine, engine),
            "trades": 0, "wins": 0, "win_rate": None, "net_pts": 0.0,
            "total_r": 0.0, "edge_proven": False,
            "reason": "no bars yet — backtest arms when the feed has persisted bars"}


def _backtest_engine(engine: str, bars: list) -> dict:
    """Run *engine*'s archetype backtest on OHLC *bars* (``(o,h,l,c)`` tuples) and
    return its held-out OOS scorecard for the dash. Pure given the bars; never raises
    on thin/empty input (returns the honest empty scorecard)."""
    from utah.product import backtest

    if not bars or len(bars) < MEANREV_CFG["lookback"] + 2:
        return _empty_scorecard(engine)
    archetype = ENGINE_ARCHETYPE.get(engine, engine)
    if engine == "meanrev":
        v = backtest.prove_meanrev(bars, **MEANREV_CFG)
        oos = v["out_of_sample"]
        return {"engine": engine, "archetype": archetype,
                "trades": oos["trades"], "wins": oos["wins"],
                "win_rate": oos["win_rate"] if oos["trades"] else None,
                "net_pts": oos["net_pts"], "total_r": oos["total_r"],
                "edge_proven": v["edge_proven"], "reason": v["reason"]}
    if engine == "breakout":
        # breakout's backtest is closes-only (a naked trend break); feed it the closes,
        # held-out OOS via the same chronological split for an apples-to-apples dash row.
        closes = [b[3] for b in bars]
        split = int(len(closes) * (1.0 - MEANREV_CFG["oos_frac"]))
        v = backtest.prove_edge(closes[split:], **BREAKOUT_CFG)
        n = v["trades"]
        return {"engine": engine, "archetype": archetype, "trades": n,
                "wins": v["wins"], "win_rate": round(v["wins"] / n, 4) if n else None,
                "net_pts": round(v["total_r"], 4), "total_r": v["total_r"],
                "edge_proven": v["edge_proven"], "reason": v["reason"]}
    return _empty_scorecard(engine)


def _ohlc_bars(symbol: str, limit: int = 5000) -> list:
    """The most-traded symbol's OHLC bars from the live ``bars`` table, chronological.
    Read-only, lazy psycopg (same pattern as leads_status); any DB error => [] so the
    dash shows the honest empty scorecard rather than crashing or faking."""
    import psycopg

    from utah import config

    try:
        with psycopg.connect(
                config.DB_DSN, autocommit=True,
                connect_timeout=config.DB_CONNECT_TIMEOUT,
                options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as cx:
            rows = cx.execute(
                "SELECT o::float8,h::float8,l::float8,c::float8 FROM bars "
                "WHERE symbol=%s ORDER BY ts, id LIMIT %s", (symbol, int(limit)),
            ).fetchall()
        return [tuple(r) for r in rows]
    except Exception:  # noqa: BLE001 — cold/unreachable DB => honest empty, never fake
        return []


def _busiest_symbol() -> str | None:
    """The symbol with the most persisted bars — the deepest, most honest sample to
    backtest the fleet on. None if the DB is cold/unreachable."""
    import psycopg

    from utah import config

    try:
        with psycopg.connect(
                config.DB_DSN, autocommit=True,
                connect_timeout=config.DB_CONNECT_TIMEOUT,
                options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as cx:
            row = cx.execute(
                "SELECT symbol FROM bars GROUP BY symbol ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def fleet_backtest(*, ohlc_fn=None) -> dict:
    """THE APEX DASH CONFIG BUILDER. For every implemented engine, run its archetype
    backtest on the real OHLC bars and return ``{engine: scorecard}`` — the held-out
    OOS win/net the dash bars live-update from. ``ohlc_fn(engine) -> bars`` is injectable
    (tests pass synthetic bars; production reads the busiest symbol's bars). Every number
    is measured; with no bars each engine gets the honest empty scorecard."""
    if ohlc_fn is None:
        symbol = _busiest_symbol()
        bars = _ohlc_bars(symbol) if symbol else []
        ohlc_fn = lambda _eng: bars  # noqa: E731 — same deep sample for every engine
    out: dict[str, dict] = {}
    for engine in implemented_engines():
        try:
            out[engine] = _backtest_engine(engine, list(ohlc_fn(engine) or []))
        except Exception as exc:  # noqa: BLE001 — one bad engine never sinks the dash
            failures.record("trading", "backtest_failed", f"{engine}: {exc}")
            out[engine] = _empty_scorecard(engine)
    _write_dash_config(out)
    return out


def _write_dash_config(engines: dict) -> None:
    """Persist the per-engine scorecards to the apex dash config (atomic-ish write).
    Failure to write is logged, never raised — the backtest result still returns."""
    from datetime import datetime, timezone

    try:
        APEX_DASH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated": datetime.now(timezone.utc).isoformat(),
                   "config": MEANREV_CFG, "engines": engines}
        tmp = APEX_DASH_CONFIG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(APEX_DASH_CONFIG)
    except Exception as exc:  # noqa: BLE001
        failures.record("trading", "dash_config_write_failed", str(exc))


def dash_config() -> dict:
    """The cheap read the deck uses every poll: ``{engine: scorecard}`` from the apex dash
    config file, or ``{}`` if it has never been built. Never a DB hit, never a crash —
    the deck's bar-builders live-update from this without re-running a backtest inline."""
    try:
        if not APEX_DASH_CONFIG.exists():
            return {}
        return json.loads(APEX_DASH_CONFIG.read_text()).get("engines", {})
    except Exception:  # noqa: BLE001 — corrupt/partial config => honest empty
        return {}


def lab_state(fires: int = 0, by_engine: dict | None = None, backtests=_AUTO) -> dict:
    """Trading Engine Lab state for the deck — real-or-gated, never fabricated.
    Per-engine truth: ``live`` only for engines with REAL rules while the feed is up;
    implemented-but-gated is ``dormant``; nameplates are ``awaiting port`` always.

    ``backtests`` (``{engine: scorecard}``) auto-populates each engine row's ``backtest``
    with its held-out OOS win/net so the deck's bar-builders live-update from measured
    numbers. Default (the deck path) reads the persisted apex dash config via
    :func:`dash_config` — cheap, no inline backtest. Pass an explicit dict to override,
    or ``None`` (hermetic unit tests) for no stats. Engines without a backtest carry
    ``None`` (the deck renders '—'), never a painted stat."""
    live = feed_available()
    counts = by_engine or {}
    bt = dash_config() if backtests is _AUTO else (backtests or {})
    n_live = sum(1 for n, _ in ENGINES if live and n in ENGINE_RULES)
    return {
        "feed": "live" if live else "gated",
        "note": (f"live WealthCharts feed — {n_live} of {len(ENGINES)} engines producing; "
                 f"the rest await their port from the Ace bundles") if live
                else "GATED: WealthCharts login (Michael) — engines go live when the feed lands",
        "fires": fires,
        "live_engines": n_live,
        "engines": [{"name": n, "kind": k,
                     "state": ("live" if live else "dormant") if n in ENGINE_RULES
                              else "awaiting port",
                     "fires": int(counts.get(n, 0)),
                     "backtest": bt.get(n)}
                    for n, k in ENGINES],
    }


#: Per-(engine, symbol) edge verdict cache: ``{(engine, symbol): (computed_at, verdict)}``.
#: The gate runs a real backtest, so it is cached behind a TTL — at most one backtest per
#: (engine, symbol) per ``EDGE_TTL_S`` rather than one per bar flush (the fire path can run
#: every few seconds). Module-level so both fire paths (run, wc_feed.flush) share it.
_EDGE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
#: 15 min — the bar regime the engines read; re-proving faster wastes a backtest on the
#: same window. Env-tunable so a deploy can tighten/loosen without a code change.
EDGE_TTL_S = float(os.environ.get("UTAH_EDGE_TTL_S", "900"))


def edge_ok(engine: str, symbol: str, *, ohlc_fn=None, score_fn=None,
            ttl_s: float = EDGE_TTL_S, now=None) -> dict:
    """Does *engine* currently have PROVEN held-out OOS edge on *symbol*'s real bars?

    The fire gate. A fire records ONLY when this returns ``ok=True`` — so the engine
    never makes a negative-expectancy bet (2026-06-12: the un-gated breakout fired
    1,461 times at 21.5% on a 2R target = net −4,384 pts; the data's edge lives only in
    mean-reversion on index futures). ``ok`` is exactly the archetype backtest's
    ``edge_proven`` (real sample AND positive OOS), so the gate tightens/loosens with the
    live data and never fires blind on thin/empty bars. Cached per (engine, symbol) behind
    ``ttl_s``. All boundaries injected for unit-proof; production reads the symbol's bars
    and runs the engine's archetype backtest."""
    clock = now or time.time
    key = (engine, symbol)
    hit = _EDGE_CACHE.get(key)
    if hit is not None and (clock() - hit[0]) < ttl_s:
        return hit[1]
    bars = list((ohlc_fn or _ohlc_bars)(symbol) or [])
    if len(bars) < MEANREV_CFG["lookback"] + 2:
        # Not enough bars to prove anything — NEVER fire blind on thin/empty data.
        # Not cached: edge arms the moment the feed has persisted enough bars.
        return {"ok": False, "reason": f"insufficient bars ({len(bars)}) to prove edge",
                "scorecard": _empty_scorecard(engine)}
    score = (score_fn or _backtest_engine)(engine, bars)
    verdict = {"ok": bool(score.get("edge_proven")),
               "reason": score.get("reason", ""), "scorecard": score}
    _EDGE_CACHE[key] = (clock(), verdict)
    return verdict


#: A symbol counts as "live" if a bar arrived within this window (ts_recorded). The feed
#: is passive — it streams whatever WC charts are open — so a proven-edge symbol that
#: stops streaming is INVISIBLE to firing until its chart is reopened. 10 min spans the
#: 15-min grader cadence without flapping on a single slow bar.
LIVE_SYMBOL_WINDOW_MIN = int(os.environ.get("UTAH_LIVE_SYMBOL_WINDOW_MIN", "10"))


def _backtestable_symbols(min_bars: int | None = None) -> list[str]:
    """Symbols with enough persisted bars to OOS-backtest — the edge-search universe.
    Read-only, defensive (DB error → []), same lazy psycopg pattern as _ohlc_bars."""
    import psycopg

    from utah import config
    floor = min_bars if min_bars is not None else (MEANREV_CFG["lookback"] + EDGE_MIN_TRADES)
    try:
        with psycopg.connect(config.DB_DSN, autocommit=True,
                             connect_timeout=config.DB_CONNECT_TIMEOUT,
                             options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as cx:
            rows = cx.execute("SELECT symbol FROM bars GROUP BY symbol "
                              "HAVING count(*) >= %s ORDER BY symbol", (int(floor),)).fetchall()
        return [r[0] for r in rows]
    except Exception:  # noqa: BLE001 — cold/unreachable DB => no universe, never crash
        return []


def _live_symbols(window_min: int | None = None) -> list[str]:
    """Symbols streaming RIGHT NOW — a bar recorded within the live window. Read-only,
    defensive ([] on any DB error)."""
    import psycopg

    from utah import config
    win = window_min if window_min is not None else LIVE_SYMBOL_WINDOW_MIN
    try:
        with psycopg.connect(config.DB_DSN, autocommit=True,
                             connect_timeout=config.DB_CONNECT_TIMEOUT,
                             options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as cx:
            rows = cx.execute("SELECT DISTINCT symbol FROM bars "
                              "WHERE ts_recorded > now() - (%s * interval '1 minute')",
                              (int(win),)).fetchall()
        return [r[0] for r in rows]
    except Exception:  # noqa: BLE001
        return []


def unfed_edges(*, candidate_symbols_fn=None, live_symbols_fn=None,
                edge_fn=None, engines=None) -> list[dict]:
    """(engine, symbol) pairs that PROVE held-out OOS edge but are NOT in the live feed —
    the actionable gap: the edge can't be captured because its chart isn't streaming
    (2026-06-13: CM.NQM6 meanrev proved 88%/+491pt while the feed was on US ETFs). All
    boundaries injected for unit-proof; production reads the bars table + the edge gate.
    Defensive: any source error → ``[]`` (a dead DB must never break the cron)."""
    try:
        cands = (candidate_symbols_fn or _backtestable_symbols)()
        live = set((live_symbols_fn or _live_symbols)())
    except Exception:  # noqa: BLE001 — symbol-source failure = no actionable gap this tick
        return []
    ef = edge_fn or (lambda e, s: edge_ok(e, s))
    out: list[dict] = []
    for sym in cands:
        if sym in live:
            continue                      # streaming now → firing path already sees it
        for eng in (engines or implemented_engines()):
            try:
                v = ef(eng, sym)
            except Exception:  # noqa: BLE001 — one bad backtest never sinks the sweep
                continue
            if v.get("ok"):
                sc = v.get("scorecard", {})
                out.append({"engine": eng, "symbol": sym,
                            "win_rate": sc.get("win_rate"), "net_pts": sc.get("net_pts"),
                            "trades": sc.get("trades"), "reason": v.get("reason", "")})
    return out


def alert_unfed_edges(*, edges_fn=None, sender=None) -> dict:
    """Page Michael (once per engine+symbol, deduped) about a proven-but-unfed edge so the
    opportunity isn't lost in silence — 'open the NQ chart'. Rides the periodic grade-fires
    cron. Best-effort: a paging failure is recorded, never raised."""
    edges = (edges_fn or unfed_edges)()
    paged = 0
    for e in edges:
        try:
            from utah import alerts
            r = alerts.unfed_edge(e["engine"], e["symbol"], win_rate=e.get("win_rate"),
                                  net_pts=e.get("net_pts"), sender=sender)
            if r.get("sent"):
                paged += 1
        except Exception as exc:  # noqa: BLE001 — alerting must never break the cron
            failures.record("trading", "unfed_alert_failed", f"{e.get('symbol')}: {exc}")
    return {"unfed": len(edges), "paged": paged, "edges": edges}


def evaluate(closes: list[float], *, lookback: int = 20, engine: str = "breakout") -> dict | None:
    """One engine's signal on the closed-bar series. Pure. Unknown/unported engine
    names NEVER fabricate a signal — they return None until their rules land."""
    if not closes or len(closes) < lookback + 1:
        return None
    rule = ENGINE_RULES.get(engine)
    return rule(closes, lookback) if rule else None


def feed_available() -> bool:
    """Whether the live WealthCharts feed is reachable — delegates to the WC CDP bridge
    (Michael's logged-in chrome-wc on the debug port). Flips True when that session is up;
    False otherwise so engines stay dormant and never fabricate fires."""
    try:
        from utah.integrations import wc_feed
        return wc_feed.feed_available()
    except Exception:  # noqa: BLE001 — any bridge import/probe failure = feed unavailable
        return False


def _live_feed() -> list[float]:  # pragma: no cover — activates with the real WC bridge
    raise RuntimeError("WealthCharts feed not wired (Michael's WC Chrome login required)")


def _fire_context(closes: list[float], sig: dict, *, lookback: int = 20,
                  target_r: float = 2.0) -> dict:
    """Stop/target/rationale for a fire — ARCHETYPE-AWARE.

    The geometry MUST match the archetype the engine's edge is proven under, or the
    live fire can't capture the backtested edge (2026-06-12: every engine fired with
    breakout geometry, so the mean-reversion engine — proven positive with a tight
    target / wide stop — was live-stopped as a breakout and bled). Breakout keeps the
    structural stop + R-multiple target (= fire_grader / backtest.backtest). Mean-
    reversion gets the tight-target-toward-the-mean / wide-z-stop shape from
    backtest._mr_trades, using MEANREV_CFG's tgt_frac and stop_mult."""
    if len(closes) < lookback + 1:
        return {"stop": None, "target": None,
                "rationale": f"{sig['engine']} {sig['direction']} signal"}
    window = closes[-lookback - 1:-1][-lookback:]
    direction, entry = sig["direction"], sig["entry"]
    if ENGINE_ARCHETYPE.get(sig["engine"]) == "mean-reversion":
        return _meanrev_context(window, direction, entry, lookback)
    stop = min(window) if direction == "long" else max(window)
    risk = abs(entry - stop)
    if risk <= 0:
        return {"stop": stop, "target": None,
                "rationale": f"{lookback}-bar {direction} breakout @ {entry}"}
    target = entry + target_r * risk if direction == "long" else entry - target_r * risk
    return {
        "stop": round(stop, 4),
        "target": round(target, 4),
        "rationale": (f"{lookback}-bar {direction} breakout @ {entry:.4f}, "
                      f"structural stop {stop:.4f}, target {target:.4f} ({target_r}R)"),
    }


def _meanrev_context(window: list[float], direction: str, entry: float,
                     lookback: int) -> dict:
    """Mean-reversion fire geometry — tight target a fraction of the way back to the
    rolling mean, wide stop ``stop_mult`` sigma beyond entry (exactly backtest._mr_trades
    so a live fire's risk/reward matches the proven OOS shape). Degrades to a bare
    rationale (no levels) on a zero-variance window — never fabricates a level."""
    mean = sum(window) / len(window)
    var = sum((c - mean) ** 2 for c in window) / len(window)
    if var <= 0.0:
        return {"stop": None, "target": None,
                "rationale": f"mean-revert {direction} @ {entry:.4f} (flat window)"}
    sd = var ** 0.5
    tgt_frac, stop_mult = MEANREV_CFG["tgt_frac"], MEANREV_CFG["stop_mult"]
    if direction == "long":
        target = entry + tgt_frac * (mean - entry)
        stop = entry - stop_mult * sd
    else:
        target = entry - tgt_frac * (entry - mean)
        stop = entry + stop_mult * sd
    z = (entry - mean) / sd
    return {
        "stop": round(stop, 4),
        "target": round(target, 4),
        "rationale": (f"mean-revert {direction} @ {entry:.4f} (z={z:+.2f}), "
                      f"target {target:.4f} ({tgt_frac:.0%} to mean {mean:.4f}), "
                      f"stop {stop:.4f} ({stop_mult:g}σ)"),
    }


def run(ledger, *, feed_fn=None, lookback: int = 20, engine: str = "breakout",
        symbol: str | None = None, edge_fn=None) -> dict:
    """Pull recent closes from the feed, evaluate, and record a real fire on signal.
    GATED on two things: (1) the live WC feed (no feed → 0 fires, documented); (2) PROVEN
    EDGE — a signal fires only when *engine* currently proves held-out OOS edge on *symbol*
    (:func:`edge_ok`), so the engine never takes a negative-expectancy bet. The edge gate
    is active when a ``symbol`` is known (the live path passes it); with no symbol AND no
    ``edge_fn`` the gate can't evaluate a stream and is skipped (the pure-pipeline unit
    contract). ``edge_fn(engine, symbol) -> {ok,...}`` is injectable for tests.
    Never fabricates. Never raises."""
    if feed_fn is None and not feed_available():
        failures.record("trading", "feed_gated",
                        "engine fires gated: no live WealthCharts feed (Michael's WC login). "
                        "Faking fires is forbidden — record_fire stays at 0 until the feed lands.")
        return {"fires": 0, "gated": True, "signal": None}
    try:
        closes = (feed_fn or _live_feed)()
    except Exception as exc:  # noqa: BLE001
        failures.record("trading", "feed_failed", str(exc))
        return {"fires": 0, "error": str(exc), "signal": None}

    sig = evaluate(closes, lookback=lookback, engine=engine)
    if not sig:
        return {"fires": 0, "signal": None}
    # EDGE GATE: never bet without proven held-out edge on this symbol. Active when a
    # symbol is known (live path) or an edge_fn is injected (tests); skipped otherwise so
    # the pure-pipeline unit contract (fire on signal) is preserved.
    if edge_fn is not None or symbol is not None:
        verdict = (edge_fn or (lambda e, s: edge_ok(e, s)))(engine, symbol)
        if not verdict.get("ok"):
            return {"fires": 0, "signal": sig, "suppressed": "no_edge",
                    "edge_reason": verdict.get("reason", "")}
    # ONE POSITION PER ENGINE + COOLDOWN (2026-06-10: 749 fires in a day — a
    # persisting breakout re-fired every bar; stats and alerts were garbage).
    # Open fire (ungraded) = in a trade: no new fire until the grader closes it.
    # After ANY fire, a flat-time cooldown before re-arming. Defensive getattr
    # keeps minimal test ledgers working (same pattern as outreach).
    try:
        state = getattr(ledger, "fire_state",
                        lambda e: {"open": False, "last_fire_age_s": None})(engine)
    except Exception as exc:  # noqa: BLE001 — unknown position state: fail SAFE, no fire
        failures.record("trading", "state_failed",
                        f"{engine}: fire_state unreadable ({exc}) — suppressing the fire "
                        "rather than firing blind into an unknown position")
        return {"fires": 0, "error": str(exc), "signal": sig, "suppressed": "state_unknown"}
    if state.get("open"):
        return {"fires": 0, "signal": sig, "suppressed": "in_position"}
    age = state.get("last_fire_age_s")
    if age is not None and age < FIRE_COOLDOWN_S:
        return {"fires": 0, "signal": sig,
                "suppressed": f"cooldown ({int(FIRE_COOLDOWN_S - age)}s left)"}
    ctx = _fire_context(closes, sig, lookback=lookback)
    try:
        fire_id = ledger.record_fire(
            sig["engine"], sig["direction"], sig["entry"], synthetic=False,
            stop=ctx.get("stop"), target=ctx.get("target"), rationale=ctx.get("rationale"),
        )
    except Exception as exc:  # noqa: BLE001 — pg down mid-fire must not crash the cron
        failures.record("trading", "record_failed", f"{engine}: {exc}")
        return {"fires": 0, "error": str(exc), "signal": sig, **ctx}
    log.info("trading: %s %s @ %.4f -> fire %s", sig["engine"], sig["direction"], sig["entry"], fire_id)
    try:  # page the phone on a real fire (gates/dedups itself; never raises here)
        from utah.product import trade_alert

        alert = {**sig, **ctx, "fire_id": fire_id}
        trade_alert.send_fire_alert(alert)
    except Exception as exc:  # noqa: BLE001
        failures.record("trading", "alert_failed", str(exc))
    return {"fires": 1, "signal": sig, "fire_id": fire_id, **ctx}


__all__ = ["evaluate", "run", "feed_available", "lab_state", "ENGINES", "ENGINE_RULES",
           "implemented_engines", "fleet_backtest", "dash_config", "ENGINE_ARCHETYPE",
           "MEANREV_CFG", "APEX_DASH_CONFIG", "edge_ok", "EDGE_TTL_S",
           "unfed_edges", "alert_unfed_edges"]
