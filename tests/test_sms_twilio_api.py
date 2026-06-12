"""The REAL Twilio REST boundary — request shape, auth, bounded timeout, the
2xx-with-embedded-error path, and URL safety when the creds file is malformed.

These pin the actual `_twilio_send` wire format (urlopen is faked at the edge,
the system under test is real) — the previously-untested half of utah/sms.py.
"""
from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from utah import sms


@pytest.fixture()
def twilio_env(tmp_path, monkeypatch):
    """Real-looking creds + isolated counter (never touch the live machine)."""
    creds = tmp_path / "twilio.json"
    creds.write_text(json.dumps({
        "account_sid": "AC" + "x" * 32,
        "auth_token": "tok" + "t" * 29,
        "from_number": "+15551234567",
    }))
    monkeypatch.setattr(sms, "TWILIO_CREDS", creds)
    monkeypatch.setattr(sms, "DAILY_COUNTER", tmp_path / "sms_daily.json")
    return creds


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(monkeypatch, payload):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["auth"] = req.get_header("Authorization")
        seen["data"] = req.data
        return _Resp(payload)

    monkeypatch.setattr(sms.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_twilio_request_is_authed_bounded_and_well_formed(twilio_env, monkeypatch):
    seen = _capture_urlopen(monkeypatch, {"sid": "SM1", "error_code": None})
    res = sms.send("+15550001111", "hello there")
    assert res == {"sent": True, "gated": False, "channel": "twilio"}
    assert seen["timeout"] == sms.HTTP_TIMEOUT_S              # bounded — never hangs the cron
    assert seen["url"].startswith("https://api.twilio.com/")
    assert "AC" + "x" * 32 in seen["url"]
    body = urllib.parse.parse_qs(seen["data"].decode())
    assert body["To"] == ["+15550001111"]
    assert body["From"] == ["+15551234567"]
    assert body["Body"] == ["hello there"]
    scheme, b64 = seen["auth"].split()
    assert scheme == "Basic"
    assert base64.b64decode(b64).decode() == "AC" + "x" * 32 + ":tok" + "t" * 29


def test_twilio_2xx_with_embedded_error_is_an_honest_failure(twilio_env, monkeypatch):
    """Twilio can 2xx with an error in the body (queued-then-rejected). That must
    surface as sent=False + a failure-feed row — never a fake success."""
    _capture_urlopen(monkeypatch, {"error_code": 30007, "error_message": "carrier block"})
    recorded = []
    monkeypatch.setattr("utah.failures.record",
                        lambda s, k, d: recorded.append((s, k, d)))
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is False and res["gated"] is False
    assert "30007" in res["error"] and "carrier block" in res["error"]
    assert recorded and recorded[0][:2] == ("sms", "send_failed")
    assert not sms.DAILY_COUNTER.exists()                     # failed send burns no cap slot


def test_sid_is_url_quoted_against_path_escape(twilio_env, monkeypatch):
    """A malformed/poisoned creds file with '/' in the sid must not be able to
    rewrite the request path — the sid is escaped into the URL, not spliced."""
    twilio_env.write_text(json.dumps({
        "account_sid": "AC123/../Other",
        "auth_token": "t" * 32,
        "from_number": "+15551234567",
    }))
    seen = _capture_urlopen(monkeypatch, {"sid": "SM1"})
    sms.send("+15550001111", "hi")
    assert "/AC123/../Other/" not in seen["url"]              # no raw path splice
    assert urllib.parse.quote("AC123/../Other", safe="") in seen["url"]


def test_network_failure_is_honest_and_recorded(twilio_env, monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection reset")
    monkeypatch.setattr(sms.urllib.request, "urlopen", boom)
    recorded = []
    monkeypatch.setattr("utah.failures.record",
                        lambda s, k, d: recorded.append((s, k, d)))
    res = sms.send("+15550001111", "hi")
    assert res["sent"] is False and "connection reset" in res["error"]
    assert recorded and recorded[0][:2] == ("sms", "send_failed")


def test_empty_body_rejected_before_any_send(twilio_env, monkeypatch):
    """A blank body is a caller bug — rejected honestly, no network/relay work."""
    def must_not_be_called(req, timeout=None):
        raise AssertionError("Twilio must not be reached for an empty body")
    monkeypatch.setattr(sms.urllib.request, "urlopen", must_not_be_called)
    for bad in ("", "   ", None):
        res = sms.send("+15550001111", bad)
        assert res["sent"] is False
        assert res["error"] == "empty body"   # exact: not a downstream network error


def test_corrupt_day_counter_degrades_to_zero(tmp_path, monkeypatch):
    counter = tmp_path / "sms_daily.json"
    counter.write_text("{not json at all")
    monkeypatch.setattr(sms, "DAILY_COUNTER", counter)
    assert sms._sends_today() == 0                            # corrupt → fail-open to 0, not crash
