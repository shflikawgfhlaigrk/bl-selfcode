"""Proven-but-unfed edge detection + alert.

The edge gate fires only where an engine proves held-out OOS edge on a symbol's real
bars — but the live WC feed is PASSIVE (it streams whatever charts are open). 2026-06-13:
the one proven edge (CM.NQM6 mean-reversion, 88% / +491 pts) was NOT in the live roster
(the feed was on US ETFs), so the system correctly fired nothing and sat silent. This
surfaces that gap: a proven edge on a symbol that isn't streaming → page Michael to open
that chart, instead of the edge going uncaptured in silence.
"""
from __future__ import annotations

from utah import alerts, failures
from utah.product import trading
from tests.fakes import FakeFailureStore


def _edge(ok, win=0.88, net=491.0, n=25):
    def fn(engine, symbol):
        return {"ok": ok, "reason": "test",
                "scorecard": {"win_rate": win, "net_pts": net, "trades": n}}
    return fn


# ── detection ────────────────────────────────────────────────────────────────

def test_unfed_edges_flags_proven_edge_not_in_live_feed():
    edges = trading.unfed_edges(
        candidate_symbols_fn=lambda: ["CM.NQM6", "US.SPY"],
        live_symbols_fn=lambda: ["US.SPY", "US.QQQ"],   # NQ is NOT streaming
        edge_fn=_edge(True), engines=["meanrev"])
    assert len(edges) == 1
    e = edges[0]
    assert e["symbol"] == "CM.NQM6" and e["engine"] == "meanrev"
    assert e["win_rate"] == 0.88 and e["net_pts"] == 491.0


def test_unfed_edges_excludes_symbols_already_live():
    # NQ proves edge AND is streaming → not "unfed", nothing to alert
    edges = trading.unfed_edges(
        candidate_symbols_fn=lambda: ["CM.NQM6"],
        live_symbols_fn=lambda: ["CM.NQM6"],
        edge_fn=_edge(True), engines=["meanrev"])
    assert edges == []


def test_unfed_edges_excludes_unproven_symbols():
    edges = trading.unfed_edges(
        candidate_symbols_fn=lambda: ["US.SPY"],
        live_symbols_fn=lambda: [],
        edge_fn=_edge(False), engines=["meanrev", "breakout"])
    assert edges == []


def test_unfed_edges_defensive_when_sources_fail():
    def boom():
        raise RuntimeError("db down")
    # a dead symbol source must not crash the cron that calls this
    assert trading.unfed_edges(candidate_symbols_fn=boom,
                               live_symbols_fn=lambda: [], edge_fn=_edge(True)) == []


# ── alert ────────────────────────────────────────────────────────────────────

def test_alert_unfed_edges_pages_once_with_actionable_message(monkeypatch):
    monkeypatch.setattr(alerts, "in_quiet_hours", lambda *a, **k: False)  # time-independent
    failures.set_store(FakeFailureStore())
    sent = []
    def sender(message, *, title=None, priority=0, **kw):
        sent.append((title, message)); return {"sent": True}
    edges = [{"engine": "meanrev", "symbol": "CM.NQM6",
              "win_rate": 0.88, "net_pts": 491.0, "trades": 25, "reason": "edge proven"}]
    r = trading.alert_unfed_edges(edges_fn=lambda: edges, sender=sender)
    assert r["unfed"] == 1 and r["paged"] == 1
    title, msg = sent[0]
    assert "CM.NQM6" in msg and "meanrev" in msg.lower()
    assert "open" in msg.lower() and "chart" in msg.lower()   # tells Michael what to DO


def test_alert_unfed_edges_no_edges_no_page():
    failures.set_store(FakeFailureStore())
    sent = []
    r = trading.alert_unfed_edges(edges_fn=lambda: [],
                                  sender=lambda *a, **k: sent.append(1) or {"sent": True})
    assert r["unfed"] == 0 and r["paged"] == 0 and sent == []


def test_alert_unfed_edges_dedups_within_ttl(monkeypatch):
    monkeypatch.setattr(alerts, "in_quiet_hours", lambda *a, **k: False)  # time-independent
    failures.set_store(FakeFailureStore())
    alerts._reset_seen_for_tests()
    sent = []
    sender = lambda message, **kw: sent.append(message) or {"sent": True}
    edges = [{"engine": "meanrev", "symbol": "CM.NQM6", "win_rate": 0.88,
              "net_pts": 491.0, "trades": 25, "reason": "x"}]
    trading.alert_unfed_edges(edges_fn=lambda: edges, sender=sender)
    trading.alert_unfed_edges(edges_fn=lambda: edges, sender=sender)
    assert len(sent) == 1                                     # second is deduped, not re-paged


def test_alert_unfed_edges_never_raises_on_sender_error():
    failures.set_store(FakeFailureStore())
    def boom(*a, **k):
        raise RuntimeError("push down")
    edges = [{"engine": "meanrev", "symbol": "CM.NQM6", "win_rate": 0.88,
              "net_pts": 491.0, "trades": 25, "reason": "x"}]
    r = trading.alert_unfed_edges(edges_fn=lambda: edges, sender=boom)  # must not raise
    assert r["unfed"] == 1 and r["paged"] == 0
