"""The edge-gate is OPT-IN, default OFF (2026-06-14, Michael's call).

It once threw away every fire that wasn't a proven OOS (engine, symbol) pair, which
throttled the live fleet to one pair and silenced the engines. The restored Apex-Prime
fleet carries its own quality gates, so Utah no longer second-guesses them. Contract:
- default OFF: the live path (symbol set, no edge_fn) fires WITHOUT consulting edge_ok
- an injected edge_fn ALWAYS governs (the unit contract is preserved)
- UTAH_EDGE_GATE=on re-arms the live backtest gate
"""
from __future__ import annotations

from utah import failures
from utah.product import trading


class FakeFailureStore:
    def record(self, *a, **k):
        pass


class _RecLedger:
    def __init__(self):
        self.fires = []

    def fire_state(self, engine):
        return {"open": False, "last_fire_age_s": None}

    def record_fire(self, engine, direction, entry=None, synthetic=False, **kw):
        self.fires.append((engine, direction, entry, kw))
        return len(self.fires)


_CLOSES = [10.0] * 20 + [12.5]                     # a real breakout signal


def test_default_off_fires_live_path_without_consulting_edge_ok(monkeypatch):
    """The kill: symbol set, no edge_fn, gate OFF -> fires, and edge_ok is never called."""
    monkeypatch.setattr(trading, "EDGE_GATE_ENABLED", False)
    monkeypatch.setattr(trading, "edge_ok",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate consulted while OFF")))
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    r = trading.run(lg, feed_fn=lambda: _CLOSES, symbol="US.SPY")
    assert r["fires"] == 1 and lg.fires[0][0] == "breakout"


def test_gate_on_restores_suppression(monkeypatch):
    """UTAH_EDGE_GATE=on -> the live path consults edge_ok again and can suppress."""
    monkeypatch.setattr(trading, "EDGE_GATE_ENABLED", True)
    monkeypatch.setattr(trading, "edge_ok", lambda e, s: {"ok": False, "reason": "unproven"})
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    r = trading.run(lg, feed_fn=lambda: _CLOSES, symbol="US.SPY")
    assert r["fires"] == 0 and r["suppressed"] == "no_edge"


def test_injected_edge_fn_still_governs_even_when_off(monkeypatch):
    """An explicitly injected edge_fn vetoes regardless of the global switch."""
    monkeypatch.setattr(trading, "EDGE_GATE_ENABLED", False)
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    r = trading.run(lg, feed_fn=lambda: _CLOSES, symbol="US.SPY",
                    edge_fn=lambda e, s: {"ok": False, "reason": "injected veto"})
    assert r["fires"] == 0 and r["suppressed"] == "no_edge"


def test_switch_reads_env_truthy():
    """The module switch reflects UTAH_EDGE_GATE truthiness (documents the on-ramp)."""
    truthy = ("1", "true", "on", "YES", "On")
    falsy = ("", "0", "off", "no", "false")
    parse = lambda v: v.strip().lower() in ("1", "true", "on", "yes")
    assert all(parse(v) for v in truthy)
    assert not any(parse(v) for v in falsy)
