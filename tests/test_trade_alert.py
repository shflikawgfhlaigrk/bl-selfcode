"""Rich Pushover trade-fire alerts — compose + send via alerts taxonomy."""
from __future__ import annotations

from utah import alerts
from utah.integrations import pushover
from utah.product import trade_alert
from tests.fakes import FakeFailureStore


def _capture():
    calls = []
    def sender(message, *, title="Utah", priority=0, **kw):
        calls.append({"message": message, "title": title, "priority": priority, **kw})
        return {"sent": True, "gated": False}
    sender.calls = calls
    return sender


def test_compose_includes_stop_target_rationale():
    msg = trade_alert.compose({
        "engine": "breakout", "direction": "long", "entry": 12.5,
        "stop": 10.5, "target": 14.5, "rationale": "trend breakout",
    })
    assert "breakout" in msg and "12.5" in msg and "10.5" in msg and "14.5" in msg
    assert "trend breakout" in msg


def test_alert_gated_without_creds(monkeypatch):
    monkeypatch.setattr(pushover, "_load_creds", lambda: None)
    r = trade_alert.send_fire_alert({"engine": "breakout", "direction": "long",
                                     "entry": 12.5, "stop": 10.5, "rationale": "why"})
    assert r["sent"] is False and r["gated"] is True


def test_alert_composes_and_sends(monkeypatch):
    monkeypatch.setattr(pushover, "_load_creds",
                        lambda: {"api_token": "t", "user_key": "u"})
    s = _capture()
    r = trade_alert.send_fire_alert(
        {"engine": "breakout", "direction": "long", "entry": 12.5,
         "stop": 10.5, "target": 14.5, "rationale": "trend breakout", "fire_id": 9},
        sender=s,
    )
    assert r["sent"] is True
    msg = s.calls[0]["message"]
    assert "BREAKOUT" in msg and "12.5" in msg and "10.5" in msg
    assert "trend breakout" in msg and "#9" in msg
    assert s.calls[0]["priority"] == 1


def test_available_reflects_creds(monkeypatch):
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": "t", "user_key": "u"})
    assert pushover.available() is True
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": ""})
    assert pushover.available() is False
    monkeypatch.setattr(pushover, "_load_creds", lambda: None)
    assert pushover.available() is False
