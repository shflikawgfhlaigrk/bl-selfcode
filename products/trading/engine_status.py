"""Engine-state capability — Ace's LIVE trading-engine/lab state.

Grounded in the ledger + nightly audit so Ace can answer "what's your engine state"
from real data, and still say "I don't know" honestly if the ledger is unreadable.
Never paints a number. Mirrors the deck's trading panel, synchronously.
"""
from __future__ import annotations


# A symbol is only really LIVE if its last tick is fresh. The chrome bridge can be "up"
# (feed_available True) all weekend while the market is closed and nothing ticks — calling
# that "live" is the fake-positive we refuse. >5 min stale = market/feed down, can't fire.
_FRESH_MS = 5 * 60 * 1000


def answer(text: str = "") -> str:
    try:
        from utah.product import trading
        from utah.product.ledger import Ledger
        lg = Ledger()
        fires = lg.counts().get("fires", 0)
        try:
            ages = [t["age_ms"] for t in lg.live_ticks() if t.get("age_ms") is not None]
            freshest_ms = min(ages) if ages else None
        except Exception:  # noqa: BLE001 — no wc_live surface => treat as no flow
            freshest_ms = None
        try:
            by_engine = {k: v.get("total", 0) for k, v in lg.fires_by_engine().items()}
        except Exception:  # noqa: BLE001
            by_engine = {}
        state = trading.lab_state(fires, by_engine=by_engine)
        try:
            from utah.product import engine_audit
            best = engine_audit.latest().get("best_edges", {}) or {}
        except Exception:  # noqa: BLE001
            best = {}
    except Exception:  # noqa: BLE001
        return ("I don't know — I couldn't read my live engine/ledger state just now, "
                "so I won't paint you a number.")
    engines = state.get("engines", [])
    if not engines:
        return "I don't know — no engine state came back from the ledger this turn."

    flowing = freshest_ms is not None and freshest_ms < _FRESH_MS
    if not flowing:
        if freshest_ms is None:
            head = ("The market is DOWN right now — no live ticks at all, so no engine can fire "
                    "or be wired. Nothing trades until the feed is back. Standing setup:")
        else:
            mins = round(freshest_ms / 60000)
            head = (f"The market is DOWN right now — freshest tick is {mins} min old, so no engine "
                    f"can fire or be wired until the feed returns. Standing setup:")
    else:
        head = (f"Market is LIVE — feed flowing, {state.get('live_engines', 0)} of {len(engines)} "
                f"engines producing, {state.get('fires', fires)} fires total. Off the ledger:")

    lines = []
    for e in engines:
        name = e.get("name", "?")
        st = e.get("state", "?")
        if not flowing and st == "live":   # honest: can't be 'live' with the market down
            st = "ready (idle — market down)"
        edge = best.get(name) or {}
        es = ""
        if edge.get("symbol"):
            es = (f", proven edge on {edge['symbol']} "
                  f"{round(edge.get('win_rate', 0) * 100)}%/{round(edge.get('net_pts', 0), 1)}pt")
        lines.append(f"- {name}: {st}{es}")
    return head + "\n" + "\n".join(lines)
