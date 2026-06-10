"""Per-day text cap — same reputation guard as mail.py's PER_ACCOUNT_DAILY.

iMessage sends ride Michael's PERSONAL Apple ID. The outreach cron can pull
~46 text candidates/run × 10 runs/day; uncapped, that volume of cold texts to
strangers is exactly the pattern that gets an Apple ID's iMessage deactivated.
Cap mirrors the mail lane: a disk-persisted per-day counter shared across cron
processes, default 30/day, env-overridable (UTAH_SMS_PER_DAY). A capped send is
a documented GATE (prospect keeps their one shot), never a failure-feed storm.
"""
from __future__ import annotations

import json

import pytest

from utah import sms


@pytest.fixture()
def sms_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sms, "TWILIO_CREDS", tmp_path / "twilio.json")  # absent → iMessage path
    counter = tmp_path / "sms_daily.json"
    monkeypatch.setattr(sms, "DAILY_COUNTER", counter)
    return counter


def _relay_ok(sent):
    return lambda to, body: sent.append(to) or {"sent": True, "channel": "imessage"}


def test_sends_count_toward_cap(sms_env, monkeypatch):
    sent = []
    monkeypatch.setattr("utah.integrations.imessage.send", _relay_ok(sent))
    monkeypatch.setattr(sms, "PER_DAY", 2)
    assert sms.send("+15550000001", "a")["sent"] is True
    assert sms.send("+15550000002", "b")["sent"] is True
    res = sms.send("+15550000003", "c")
    assert res["sent"] is False
    assert res["gated"] is True
    assert "cap" in res["reason"]
    assert sent == ["+15550000001", "+15550000002"]


def test_counter_persists_across_processes(sms_env, monkeypatch):
    """The counter lives on disk — a fresh import (new cron process) honors it."""
    sent = []
    monkeypatch.setattr("utah.integrations.imessage.send", _relay_ok(sent))
    monkeypatch.setattr(sms, "PER_DAY", 1)
    sms.send("+15550000001", "a")
    data = json.loads(sms_env.read_text())
    assert data["count"] == 1
    # simulate another process the same day: counter file already at cap
    res = sms.send("+15550000002", "b")
    assert res["sent"] is False and res["gated"] is True


def test_counter_resets_next_day(sms_env, monkeypatch):
    sent = []
    monkeypatch.setattr("utah.integrations.imessage.send", _relay_ok(sent))
    monkeypatch.setattr(sms, "PER_DAY", 1)
    sms_env.write_text(json.dumps({"date": "2020-01-01", "count": 99}))
    assert sms.send("+15550000001", "a")["sent"] is True


def test_failed_relay_does_not_consume_cap(sms_env, monkeypatch):
    """A gated/failed iMessage must not burn a cap slot."""
    monkeypatch.setattr(
        "utah.integrations.imessage.send",
        lambda to, body: {"sent": False, "gated": True, "reason": "no grant"},
    )
    monkeypatch.setattr(sms, "PER_DAY", 5)
    sms.send("+15550000001", "a")
    data = json.loads(sms_env.read_text()) if sms_env.exists() else {"count": 0}
    assert data.get("count", 0) == 0


def test_injected_send_fn_bypasses_cap(sms_env, monkeypatch):
    """Tests/tools injecting send_fn are not subject to the production cap."""
    monkeypatch.setattr(sms, "PER_DAY", 0)
    res = sms.send("+15550000001", "a", send_fn=lambda to, body: None)
    assert res["sent"] is True
