"""OAuth hardening — security(1) absence is a miss (not a crash), the Ace→Utah token
copy is HONEST about a rejected Keychain write, and the refreshed-token persistence
path inside gmail_profile survives a write failure (the in-memory token still works)."""
from __future__ import annotations

import json
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from utah import config
from utah.integrations import oauth


def _iso(delta_s: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


# ── security(1) boundary ──────────────────────────────────────────────────────

def test_security_binary_missing_is_a_miss_not_a_crash(monkeypatch):
    def gone(*a, **k):
        raise FileNotFoundError("/usr/bin/security")

    monkeypatch.setattr(subprocess, "run", gone)
    assert oauth.read_blob() is None
    assert oauth.write_blob({"token": "t"}) is False     # honest: nothing was written


def test_security_returns_failed_completedprocess_on_timeout(monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=8)

    monkeypatch.setattr(subprocess, "run", hang)
    p = oauth._security("find-generic-password", "-s", "svc")
    assert p.returncode != 0 and p.stdout == ""


# ── copy honesty: a rejected write is NOT a copy ──────────────────────────────

def test_copy_ace_to_utah_false_when_keychain_write_rejected(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob",
                        lambda service=None, account="gmail": {"token": "t"})
    monkeypatch.setattr(oauth, "gmail_profile",
                        lambda service=None: {"email": config.OWNER_EMAIL})
    monkeypatch.setattr(oauth, "write_blob", lambda b, **k: False)
    assert oauth.copy_ace_to_utah_if_correct() is False


def test_copy_ace_to_utah_true_when_write_lands(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob",
                        lambda service=None, account="gmail": {"token": "t"})
    monkeypatch.setattr(oauth, "gmail_profile",
                        lambda service=None: {"email": config.OWNER_EMAIL.upper()})
    wrote = []
    monkeypatch.setattr(oauth, "write_blob", lambda b, **k: wrote.append(b) or True)
    assert oauth.copy_ace_to_utah_if_correct() is True   # case-insensitive owner match
    assert wrote == [{"token": "t"}]


# ── refresh persistence inside gmail_profile ──────────────────────────────────

class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gmail_profile_refreshes_expired_blob_and_survives_write_failure(monkeypatch):
    """An expired blob refreshes and the profile call proceeds on the in-memory token
    even when the Keychain refuses to persist it — degraded, never dead."""
    monkeypatch.setattr(oauth, "read_blob",
                        lambda **k: {"token": "old", "refresh_token": "r",
                                     "expiry": _iso(-5)})
    monkeypatch.setattr(oauth, "refresh_blob",
                        lambda b: {**b, "token": "FRESH", "expiry": _iso(3600)})
    monkeypatch.setattr(oauth, "write_blob", lambda b, **k: False)   # keychain says no
    seen = {}

    def fake_urlopen(req, timeout=20):
        seen["auth"] = req.headers.get("Authorization")
        return _Resp({"emailAddress": "x@y.com", "messagesTotal": 1, "threadsTotal": 1})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    p = oauth.gmail_profile()
    assert seen["auth"] == "Bearer FRESH" and p["messages_total"] == 1


def test_gmail_profile_http_401_is_oautherror_with_code(monkeypatch):
    import io

    monkeypatch.setattr(oauth, "read_blob", lambda **k: {"token": "T", "expiry": _iso(3600)})

    def unauthorized(req, timeout=20):
        raise urllib.error.HTTPError("u", 401, "nope", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    with pytest.raises(oauth.OAuthError, match="401"):
        oauth.gmail_profile()
