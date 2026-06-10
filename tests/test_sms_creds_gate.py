"""creds_available must mean USABLE Twilio creds, not just a file on disk.

Live incident 2026-06-09: ``twilio.json`` existed with empty ``account_sid`` and
``from_number`` (only ``auth_token`` filled). ``creds_available()`` returned True
on bare ``.exists()``, so every text took the Twilio branch, failed 387×/day
(HTTP 404 from the empty-sid URL), polluted the failure ledger, and — the real
cost — made the iMessage fallback (Michael's explicit 2026-06-09 directive)
unreachable code. 797 phone-only leads sat stranded behind a gutted JSON file.
"""
from __future__ import annotations

import json

import pytest

from utah import sms


@pytest.fixture()
def creds_file(tmp_path, monkeypatch):
    path = tmp_path / "twilio.json"
    monkeypatch.setattr(sms, "TWILIO_CREDS", path)
    # Isolate the daily counter too — a successful (patched) relay send records
    # to it, and an unpatched path would write the PRODUCTION counter from tests
    # (live incident: com.utah.verify's pytest loop burned real cap slots).
    monkeypatch.setattr(sms, "DAILY_COUNTER", tmp_path / "sms_daily.json")
    return path


def test_no_file_means_unavailable(creds_file):
    assert sms.creds_available() is False


def test_complete_creds_available(creds_file):
    creds_file.write_text(json.dumps({
        "account_sid": "AC" + "x" * 32,
        "auth_token": "t" * 32,
        "from_number": "+15551234567",
    }))
    assert sms.creds_available() is True


def test_empty_sid_and_from_means_unavailable(creds_file):
    # The exact live state that stranded the phone leads.
    creds_file.write_text(json.dumps({
        "account_sid": "",
        "auth_token": "t" * 32,
        "from_number": "",
    }))
    assert sms.creds_available() is False


def test_whitespace_or_missing_keys_unavailable(creds_file):
    creds_file.write_text(json.dumps({"account_sid": "  ", "auth_token": "t"}))
    assert sms.creds_available() is False


def test_malformed_json_unavailable(creds_file):
    creds_file.write_text("{not json")
    assert sms.creds_available() is False


def test_incomplete_creds_fall_back_to_imessage(creds_file, monkeypatch):
    """With a gutted twilio.json, send() must reach the iMessage relay."""
    creds_file.write_text(json.dumps({"account_sid": "", "auth_token": "t", "from_number": ""}))
    relayed = []
    monkeypatch.setattr(
        "utah.integrations.imessage.send",
        lambda to, body: relayed.append(to) or {"sent": True, "channel": "imessage"},
    )
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is True
    assert res["channel"] == "imessage"
    assert relayed == ["+15550001111"]
