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

import logging

from utah import failures

log = logging.getLogger("utah.product.trading")

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


def implemented_engines() -> tuple[str, ...]:
    return tuple(ENGINE_RULES)


def lab_state(fires: int = 0, by_engine: dict | None = None) -> dict:
    """Trading Engine Lab state for the deck — real-or-gated, never fabricated.
    Per-engine truth: ``live`` only for engines with REAL rules while the feed is up;
    implemented-but-gated is ``dormant``; nameplates are ``awaiting port`` always."""
    live = feed_available()
    counts = by_engine or {}
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
                     "fires": int(counts.get(n, 0))}
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
    ctx = _fire_context(closes, sig, lookback=lookback)
    fire_id = ledger.record_fire(
        sig["engine"], sig["direction"], sig["entry"], synthetic=False,
        stop=ctx.get("stop"), target=ctx.get("target"), rationale=ctx.get("rationale"),
    )
    log.info("trading: %s %s @ %.4f -> fire %s", sig["engine"], sig["direction"], sig["entry"], fire_id)
    try:  # page the phone on a real fire (gates/dedups itself; never raises here)
        from utah.product import trade_alert

        alert = {**sig, **ctx, "fire_id": fire_id}
        trade_alert.send_fire_alert(alert)
    except Exception as exc:  # noqa: BLE001
        failures.record("trading", "alert_failed", str(exc))
    return {"fires": 1, "signal": sig, "fire_id": fire_id, **ctx}


__all__ = ["evaluate", "run", "feed_available", "lab_state", "ENGINES", "ENGINE_RULES", "implemented_engines"]
