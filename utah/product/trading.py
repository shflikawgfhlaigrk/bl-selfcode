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
    ("shadow",    "shadow book · confirmation overlay"),
    ("ctx_alpha", "context engine — alpha regime"),
    ("ctx_bravo", "context engine — bravo regime"),
    ("barber",    "barber regime filter"),
    ("perp",      "perp / perp_v2 momentum"),
    ("research",  "research-signal engine"),
    ("bible",     "rule-bible engine"),
    ("antigrav",  "antigrav emoji-signal engine"),
)


def lab_state(fires: int = 0) -> dict:
    """Trading Engine Lab state for the deck — real-or-gated, never fabricated.
    Lists the engine roster and the live/gated feed status; every engine is DORMANT
    until :func:`feed_available` flips (Michael's WealthCharts login)."""
    live = feed_available()
    return {
        "feed": "live" if live else "gated",
        "note": "live WealthCharts feed" if live
                else "GATED: WealthCharts login (Michael) — engines go live when the feed lands",
        "fires": fires,
        "engines": [{"name": n, "kind": k, "state": "live" if live else "dormant"}
                    for n, k in ENGINES],
    }


def evaluate(closes: list[float], *, lookback: int = 20, engine: str = "breakout") -> dict | None:
    """Reference signal: a close above the prior ``lookback`` high is a LONG fire; below the
    prior low is a SHORT. Pure. Ace's engines add rules on this same interface."""
    if not closes or len(closes) < lookback + 1:
        return None
    prior = closes[-lookback - 1:-1]
    last = closes[-1]
    if last > max(prior):
        return {"engine": engine, "direction": "long", "entry": last}
    if last < min(prior):
        return {"engine": engine, "direction": "short", "entry": last}
    return None


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
    fire_id = ledger.record_fire(sig["engine"], sig["direction"], sig["entry"], synthetic=False)
    log.info("trading: %s %s @ %.4f -> fire %s", sig["engine"], sig["direction"], sig["entry"], fire_id)
    try:  # page the phone on a real fire (gates/dedups itself; never raises here)
        from utah import alerts
        alerts.trade_fire(sig["engine"], sig["direction"], sig["entry"], fire_id=fire_id)
    except Exception as exc:  # noqa: BLE001
        failures.record("trading", "alert_failed", str(exc))
    return {"fires": 1, "signal": sig, "fire_id": fire_id}


__all__ = ["evaluate", "run", "feed_available", "lab_state", "ENGINES"]
