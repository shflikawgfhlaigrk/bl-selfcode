"""The Pushover streams are WIRED to their live sources, not just defined.

Audit 2026-06-10: the alert taxonomy (utah/alerts.py) and transport were solid but two
of the four streams had ZERO callers — ``trade`` (designed for engine fires; the WC feed
it waited on is live) and ``leads_probate`` (the daily pipeline summary). These tests pin
the wiring: record_fire is the single trade chokepoint (real fires page, synthetic never),
and each pipeline cron pushes its run summary. Spies replace utah.alerts entrypoints —
no network, no real pushes.
"""
from __future__ import annotations

import json as _json

from utah import alerts
from utah.product import leads, outreach, probate
from utah.product import ledger as ledger_mod
from tests.test_leads import _NO_GATE, _RecLedger, _cursor_pair


def _spy(calls, name):
    def fn(*a, **k):
        calls.append((name, a, k))
        return {"sent": True}
    return fn


class _Cur:
    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return (77,)


class _Conn:
    def __enter__(self):
        return _Cur()

    def __exit__(self, *a):
        return False


def _stub_ledger(monkeypatch):
    lg = ledger_mod.Ledger.__new__(ledger_mod.Ledger)   # no DSN, no connect
    monkeypatch.setattr(lg, "_conn", lambda: _Conn(), raising=False)
    monkeypatch.setattr(lg, "_emit", lambda *a, **k: None, raising=False)
    return lg


def test_record_fire_pages_the_trade_stream(monkeypatch):
    calls: list = []
    monkeypatch.setattr(alerts, "trade_fire", _spy(calls, "trade"))
    lg = _stub_ledger(monkeypatch)
    fid = lg.record_fire("breakout", "long", entry=29376.5, symbol="CM.NQM6",
                         stop=29350.0, target=29420.0)
    assert fid == 77
    (name, a, k), = calls
    assert a[:2] == ("breakout", "long") and a[2] == 29376.5
    assert k["fire_id"] == 77 and k["stop"] == 29350.0 and k["target"] == 29420.0
    assert "CM.NQM6" in (k.get("rationale") or "")      # symbol reaches the phone


def test_record_fire_synthetic_never_pages(monkeypatch):
    calls: list = []
    monkeypatch.setattr(alerts, "trade_fire", _spy(calls, "trade"))
    lg = _stub_ledger(monkeypatch)
    lg.record_fire("demo", "long", entry=1.0, synthetic=True)
    assert calls == []                                   # demo fires never wake Michael


def test_leads_cron_pushes_its_daily_summary(monkeypatch):
    calls: list = []
    monkeypatch.setattr(alerts, "leads_probate", _spy(calls, "pipeline"))
    sample = _json.dumps({"elements": [
        {"tags": {"name": "Maple Diner", "amenity": "restaurant"}}]})
    load, save, _ = _cursor_pair(0)
    r = leads.run_scheduled(region="GA", target=1, bbox=(0, 0, 0.5, 0.5), max_tiles=4,
                            ledger=_RecLedger(), fetch=lambda q: sample,
                            cursor_load=load, cursor_save=save, foundation_gate=_NO_GATE)
    (name, a, k), = calls
    assert a[0] == r and k.get("kind") == "leads"        # the run result is what's pushed


def test_probate_cron_pushes_its_daily_summary(monkeypatch):
    calls: list = []
    monkeypatch.setattr(alerts, "leads_probate", _spy(calls, "pipeline"))
    monkeypatch.setattr(probate, "scout", lambda *a, **k: {"found": 5, "new": 2})
    r = probate.run_scheduled(ledger=object(), fetch=lambda *a, **k: "")
    (name, a, k), = calls
    assert a[0] == r == {"found": 5, "new": 2} and k.get("kind") == "probate"


def test_outreach_cron_pushes_its_daily_summary(monkeypatch):
    calls: list = []
    monkeypatch.setattr(alerts, "leads_probate", _spy(calls, "pipeline"))

    class FakeLedger:
        def uncontacted_email_leads(self, campaign, limit):
            return [{"name": "Joe's Diner", "kind": "restaurant",
                     "contact": {"email": "joe@example.com", "address": "1 Main St"}}][:limit]

        def is_contacted(self, r, c):
            return False

        def log_outreach(self, r, c, channel="email"):
            return True

        def record_mail(self, *a, **k):
            return True

    monkeypatch.setattr(outreach, "default_footer",
                        lambda: {"address": "28 Dogwood Rd, Newnan GA 30263",
                                 "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
                               foundation_gate=lambda cap: None,
                               channel="email", now_hour=10,
                               send_fn=lambda to, s, b: {"sent": True})
    (name, a, k), = calls
    assert a[0] == r and k.get("kind") == "outreach"
