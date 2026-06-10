"""Fire grader — fills ``fires.outcome`` / ``fires.pnl`` so the signal lane is MEASURABLE.

The fires ledger recorded real breakout signals nobody could grade (outcome/pnl NULL on
every row), so the signals package had no track record to sell. This module grades each
fire from the persisted ``bars`` table and writes the verdict back — cron-driven via
``com.utah.grade-fires`` every 15 minutes (:func:`run_scheduled`).

GRADING CONVENTION (explicit and changeable; the math REUSES utah/product/backtest.py —
``backtest._simulate_trade`` — so live grades and backtest grades can never diverge):

* entry  = the fire row's ``entry`` (the breakout bar's close, recorded by the engine).
* stop   = STRUCTURAL: the opposite extreme of the prior :data:`LOOKBACK` (20) closed-bar
  closes immediately before the breakout bar — a long stops at the window low, a short at
  the window high. Same stop as ``backtest.backtest()``.
* target = entry ± :data:`TARGET_R` (2.0) × risk, where risk = ``|entry − stop|``.
* exit   = walk the persisted 15s bar CLOSES after the fire, per-bar: first close through
  the stop → outcome ``'stop'``; through the target → ``'target'``; neither within
  :data:`HORIZON_MIN` (30) minutes → ``'timeout'``, marked to market at the last close
  inside the horizon.
* pnl    = signed POINTS: ``exit − entry`` for a LONG, ``entry − exit`` for a SHORT.

HONESTY RULE (repo doctrine — never fabricate): outcome ``'ungradable'`` with pnl NULL
when a fire cannot be graded from REAL data, specifically when

* its symbol has NO persisted post-fire bars — true for every fire recorded before bar
  persistence shipped (2026-06-09; the WC feed kept bars in memory only), or
* the fire row has no ``symbol`` AND more than one (or zero) symbols hold bars in its
  window — we never guess which instrument's prices to grade against (a NULL-symbol fire
  IS graded when exactly one bar stream covers its window: the only candidate), or
* fewer than ``lookback`` prior bars exist (the structural stop can't be reconstructed), or
* the breakout window is zero-width (risk = 0) / the row has no entry price.

Prices come ONLY from the ``bars`` table (real closed bars persisted by
``utah/integrations/wc_feed.py`` as they close; ``bars.ts`` = bar CLOSE time). A fire is
graded exactly once (``UPDATE … WHERE outcome IS NULL``) and only after its evaluation
horizon has elapsed.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from utah import failures
from utah.product import backtest

log = logging.getLogger("utah.product.fire_grader")

LOOKBACK = 20        # prior closed bars defining the breakout window (trading.evaluate)
TARGET_R = 2.0       # R-multiple target (backtest.backtest default)
HORIZON_MIN = 30     # evaluation horizon: timeout + mark-to-market after 30 minutes


def grade(direction: str, entry: float, prior_closes, post_closes, *,
          lookback: int = LOOKBACK, target_r: float = TARGET_R) -> dict:
    """Grade ONE fire from real bar closes. PURE (unit-tested with synthetic bars).

    ``prior_closes``: chronological closes of the bars before the breakout bar (the
    structural-stop window). ``post_closes``: chronological closes after the fire, already
    clipped to the evaluation horizon. Returns ``{outcome, pnl, reason}`` where outcome is
    ``target | stop | timeout | ungradable`` and pnl is signed points (None if ungradable).
    """
    if direction not in ("long", "short"):
        return {"outcome": "ungradable", "pnl": None,
                "reason": f"unknown direction {direction!r}"}
    if entry is None:
        return {"outcome": "ungradable", "pnl": None, "reason": "fire has no entry price"}
    prior = list(prior_closes)
    if len(prior) < lookback:
        return {"outcome": "ungradable", "pnl": None,
                "reason": f"insufficient prior bars ({len(prior)} < {lookback}) "
                          "to reconstruct the structural stop"}
    window = prior[-lookback:]
    stop = min(window) if direction == "long" else max(window)
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "ungradable", "pnl": None,
                "reason": "zero-risk breakout window (stop == entry)"}
    post = list(post_closes)
    if not post:
        return {"outcome": "ungradable", "pnl": None,
                "reason": "no post-fire bars persisted (bar persistence began after "
                          "this fire) — a price is never invented"}
    t = backtest._simulate_trade([entry] + post, 0, direction, entry, stop, target_r)
    outcome = "timeout" if t.outcome == "eod" else t.outcome
    pnl = (t.exit - entry) if direction == "long" else (entry - t.exit)
    return {"outcome": outcome, "pnl": round(pnl, 4),
            "reason": f"stop {stop} target {t.target} exit {t.exit} "
                      f"after {t.bars_held} bars"}


def _grade_one(ledger, fire: dict, horizon: timedelta, lookback: int,
               target_r: float) -> dict:
    """Resolve the fire's bar stream and grade it. Raises only on store errors
    (run_scheduled catches those per-fire)."""
    ts = fire["ts"]
    end = ts + horizon
    symbol = fire.get("symbol")
    if not symbol:
        candidates = ledger.bar_symbols_between(ts, end)
        if len(candidates) == 1:
            symbol = candidates[0]      # the only bar stream covering the window
        else:
            return {"outcome": "ungradable", "pnl": None,
                    "reason": "no symbol on the fire and no unambiguous bar stream "
                              f"in its window ({len(candidates)} candidates)"}
    # last lookback+1 closes at/just before the fire; the final one is the breakout bar
    # itself (the engine records the fire within ~1s of the bar closing), so drop it.
    prior = ledger.bars_before(symbol, ts, lookback + 1)[:-1]
    post = ledger.bars_between(symbol, ts, end)
    return grade(fire["direction"], fire.get("entry"), prior, post,
                 lookback=lookback, target_r=target_r)


def run_scheduled(ledger=None, *, horizon_min: int = HORIZON_MIN,
                  lookback: int = LOOKBACK, target_r: float = TARGET_R) -> dict:
    """Grade every ungraded, non-synthetic fire older than its evaluation horizon and
    UPDATE its outcome/pnl (exactly once per fire). Cron entrypoint
    (``com.utah.grade-fires``, every 15 min). Never raises; never fabricates."""
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    out = {"checked": 0, "target": 0, "stop": 0, "timeout": 0,
           "ungradable": 0, "errors": 0}
    try:
        ledger.init_schema()                      # bars table + fires.symbol exist
        fires = ledger.ungraded_fires(older_than_minutes=horizon_min)
    except Exception as exc:  # noqa: BLE001 — store down: record + report, don't crash cron
        failures.record("trading", "grader_store", f"fire grader store unreachable: {exc}")
        return {**out, "error": str(exc)}
    horizon = timedelta(minutes=horizon_min)
    for fire in fires:
        out["checked"] += 1
        try:
            g = _grade_one(ledger, fire, horizon, lookback, target_r)
            ledger.grade_fire(fire["id"], g["outcome"], g["pnl"])
            out[g["outcome"]] += 1
            log.info("graded fire %s: %s pnl=%s (%s)", fire["id"], g["outcome"],
                     g["pnl"], g["reason"])
        except Exception as exc:  # noqa: BLE001 — one bad fire never blocks the rest
            out["errors"] += 1
            failures.record("trading", "grade_failed", f"fire {fire['id']}: {exc}")
    return out


__all__ = ["grade", "run_scheduled", "LOOKBACK", "TARGET_R", "HORIZON_MIN"]
