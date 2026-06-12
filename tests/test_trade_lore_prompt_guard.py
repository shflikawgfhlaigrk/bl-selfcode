"""Trade-lore prompt guard — a ledger scorecard with a junk win_rate (jsonb drift,
None vs string) must never crash assess_fire; the read just omits the percentage."""
from __future__ import annotations

from utah.product import trade_lore


_FIRE = {"id": 7, "engine": "breakout", "direction": "long", "entry": 100.0,
         "symbol": "NQ", "outcome": "win", "pnl": 12.5}


def test_assess_fire_survives_junk_win_rate_never_raises():
    scorecard = {"graded": 5, "wins": 4, "win_rate": "n/a", "net_pnl": 30.0}
    text = trade_lore.assess_fire(_FIRE, scorecard, [],
                                  think_fn=lambda q, c: "Clean breakout, record holds.")
    assert text is not None
    assert trade_lore.DISCLAIMER in text
    # and the junk value never leaks into the brain context as a fake percentage
    _, context = trade_lore._prompt(_FIRE, scorecard, [])
    assert "%" not in context.split("HISTORICAL")[0].split("wins")[1].split(",")[0]


def test_prompt_formats_real_win_rate():
    scorecard = {"graded": 10, "wins": 9, "win_rate": 0.9, "net_pnl": 55.0}
    _, context = trade_lore._prompt(_FIRE, scorecard, [])
    assert "(90%)" in context
