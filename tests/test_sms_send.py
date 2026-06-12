"""send() boundary hardening — honest results even when the bookkeeping fails.

The send result is the OUTREACH LEDGER's source of truth: a message that
actually went out must report ``sent=True`` even if the day-counter write
fails afterward (disk full / bad perms must not flip a delivered text into a
recorded failure), and ``send`` must never raise (it is a never-raises
boundary fn). An empty recipient is rejected before any network/relay work.
"""
from __future__ import annotations

import json

import pytest

from utah import sms


@pytest.fixture()
def sms_env(tmp_path, monkeypatch):
    """Isolate creds + counter from the live machine (cf. test_sms_daily_cap)."""
    monkeypatch.setattr(sms, "TWILIO_CREDS", tmp_path / "twilio.json")
    monkeypatch.setattr(sms, "DAILY_COUNTER", tmp_path / "sms_daily.json")
    return tmp_path


def _block_counter(tmp_path, monkeypatch):
    """Point DAILY_COUNTER somewhere unwritable: its parent is a regular FILE."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(sms, "DAILY_COUNTER", blocker / "sms_daily.json")


def test_counter_write_failure_does_not_flip_a_sent_relay_message(sms_env, monkeypatch):
    """iMessage went out; the day-counter write then fails. The result must stay
    sent=True and send() must not raise (it would have crashed the outreach cron)."""
    _block_counter(sms_env, monkeypatch)
    monkeypatch.setattr(
        "utah.integrations.imessage.send",
        lambda to, body: {"sent": True, "channel": "imessage"},
    )
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is True
    assert res["channel"] == "imessage"


def test_counter_write_failure_does_not_flip_a_sent_twilio_message(sms_env, monkeypatch):
    """Twilio accepted the message; a failing counter write must not turn that
    into sent=False (dishonest signal) nor into a phantom send_failed row."""
    (sms_env / "twilio.json").write_text(json.dumps({
        "account_sid": "AC" + "x" * 32,
        "auth_token": "t" * 32,
        "from_number": "+15551234567",
    }))
    _block_counter(sms_env, monkeypatch)
    monkeypatch.setattr(sms, "_twilio_send", lambda to, body: None)
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is True
    assert res["channel"] == "twilio"


def test_twilio_success_records_one_cap_slot(sms_env, monkeypatch):
    (sms_env / "twilio.json").write_text(json.dumps({
        "account_sid": "AC" + "x" * 32,
        "auth_token": "t" * 32,
        "from_number": "+15551234567",
    }))
    monkeypatch.setattr(sms, "_twilio_send", lambda to, body: None)
    assert sms.send("+15550001111", "hi")["sent"] is True
    data = json.loads((sms_env / "sms_daily.json").read_text())
    assert data["count"] == 1


def test_twilio_failure_is_honest_and_recorded(sms_env, monkeypatch):
    """A failing Twilio call returns sent=False with the error AND lands in the
    failure feed — never a silent drop, never a fake success."""
    (sms_env / "twilio.json").write_text(json.dumps({
        "account_sid": "AC" + "x" * 32,
        "auth_token": "t" * 32,
        "from_number": "+15551234567",
    }))
    recorded = []
    monkeypatch.setattr(
        "utah.failures.record",
        lambda source, kind, detail: recorded.append((source, kind, detail)),
    )
    def boom(to, body):
        raise RuntimeError("twilio error 21211: invalid 'To' number")
    monkeypatch.setattr(sms, "_twilio_send", boom)
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is False and res["gated"] is False
    assert "21211" in res["error"]
    assert recorded and recorded[0][:2] == ("sms", "send_failed")
    # a failed send must not burn a cap slot
    assert not (sms_env / "sms_daily.json").exists()


def test_empty_recipient_rejected_before_any_send(sms_env, monkeypatch):
    """A blank `to` is a caller bug: rejected honestly, no relay/network attempt."""
    def must_not_be_called(to, body):
        raise AssertionError("relay must not be reached for an empty recipient")
    monkeypatch.setattr("utah.integrations.imessage.send", must_not_be_called)
    for bad in ("", "   ", None):
        res = sms.send(bad, "hi")
        assert res["sent"] is False
        assert "recipient" in res["error"]


def test_injected_send_fn_failure_is_honest(sms_env, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "utah.failures.record",
        lambda source, kind, detail: recorded.append((source, kind, detail)),
    )
    def boom(to, body):
        raise OSError("socket closed")
    res = sms.send("+15550001111", "hi", send_fn=boom)
    assert res["sent"] is False and "socket closed" in res["error"]
    assert recorded and recorded[0][:2] == ("sms", "send_failed")


def test_imessage_gate_reason_propagates(sms_env, monkeypatch):
    """No Twilio + gated iMessage → honest gate carrying the relay's own reason."""
    monkeypatch.setattr(
        "utah.integrations.imessage.send",
        lambda to, body: {"sent": False, "gated": True, "reason": "no Automation grant"},
    )
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is False and res["gated"] is True
    assert res["reason"] == "no Automation grant"
