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
MEANREV_CFG = {"lookback": 20, "z_enter": 2.0, "tgt_frac": 0.6, "stop_mult": 8.0,
               "win_floor": 0.87, "oos_frac": 0.4, "min_trades": 1, "max_hold": 80}
BREAKOUT_CFG = {"lookback": 20, "target_r": 2.0, "min_trades": 1}


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
    """Structural stop/target + rationale from the breakout window (same math as fire_grader)."""
    if len(closes) < lookback + 1:
        return {"stop": None, "target": None,
                "rationale": f"{sig['engine']} {sig['direction']} breakout"}
    window = closes[-lookback - 1:-1][-lookback:]
    direction, entry = sig["direction"], sig["entry"]
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


def run(ledger, *, feed_fn=None, lookback: int = 20, engine: str = "breakout") -> dict:
    """Pull recent closes from the feed, evaluate, and record a real fire on signal.
    GATED: with no feed it records 0 fires + documents the gate. Never fabricates. Never raises."""
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
           "MEANREV_CFG", "APEX_DASH_CONFIG"]
