"""Grade a fire by its OWN recorded stop/target, not always-breakout geometry.

Why (data, 2026-06-12): every fire — including mean-reversion — was graded by the
breakout structural stop + 2R target the grader RE-DERIVES from prior closes, so the
`meanrev` rows showed physically impossible results (a 'stop' outcome with +69.8 pnl).
A fire now records its archetype's real stop/target; the grader walks the post bars
against THOSE levels. Structural reconstruction stays as the fallback when a fire
carries no levels (old rows) or carries geometrically-insane ones.
"""
from __future__ import annotations

from utah.product import fire_grader as fg

PRIOR = [10.0] * 20          # flat structural window (breakout stop = 10.0 for a long)


def test_grades_meanrev_long_against_recorded_levels_target():
    # mean-revert LONG: entry 8 (extreme low), tight target 9.2 (toward mean), wide stop 4.
    # post rallies to 9.5 -> hits the recorded target, NOT a 2R breakout target.
    g = fg.grade("long", 8.0, PRIOR, [8.5, 9.3, 9.5], stop=4.0, target=9.2)
    assert g["outcome"] == "target"
    assert round(g["pnl"], 2) == round(9.2 - 8.0, 2)        # +1.2 pts to the real target


def test_grades_against_recorded_stop_as_a_loss():
    # same fire; post falls through the wide stop first -> a real loss (negative pnl)
    g = fg.grade("long", 8.0, PRIOR, [6.0, 4.0, 9.9], stop=4.0, target=9.2)
    assert g["outcome"] == "stop"
    assert g["pnl"] < 0 and round(g["pnl"], 2) == round(4.0 - 8.0, 2)   # -4.0 pts


def test_meanrev_short_recorded_levels():
    # short: entry 12, tight target 10.8 (toward mean, below), wide stop 16 (above)
    g = fg.grade("short", 12.0, PRIOR, [11.5, 10.8, 10.0], stop=16.0, target=10.8)
    assert g["outcome"] == "target" and round(g["pnl"], 2) == round(12.0 - 10.8, 2)


def test_recorded_levels_timeout_marks_to_market():
    g = fg.grade("long", 8.0, PRIOR, [8.1, 8.2, 8.3], stop=4.0, target=9.2)
    assert g["outcome"] == "timeout"
    assert round(g["pnl"], 2) == round(8.3 - 8.0, 2)        # mark-to-market, never invented


def test_no_recorded_levels_falls_back_to_structural_breakout():
    # no stop/target passed -> the existing structural path (breakout window) is used
    g = fg.grade("long", 12.0, PRIOR, [13.0, 14.5], )       # breakout @12, stop 10, 2R target 16
    assert g["outcome"] in ("target", "stop", "timeout")
    assert "stop 10.0" in g["reason"] or "target" in g["reason"]


def test_insane_recorded_levels_fall_back_to_structural():
    # target on the WRONG side for a long (below entry) = the old garbled geometry;
    # refuse to grade by it, fall back to structural reconstruction instead.
    g = fg.grade("long", 12.0, PRIOR, [13.0, 16.5], stop=11.9, target=11.0)
    # structural: breakout long @12, window-low stop 10.0, 2R target = 16.0 -> 16.5 hits target
    assert g["outcome"] == "target"
