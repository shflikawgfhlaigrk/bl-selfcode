"""OAuth Keychain adapter — every boundary proven with injected subprocess/HTTP:
blob read/parse, expiry math (skew, Z-suffix, naive-UTC), refresh success/failure
taxonomy (invalid_grant, non-200, 200-without-token, network down → OAuthError, never
a stray KeyError/URLError), Keychain write honesty, and the never-raises email helpers."""
from __future__ import annotations

import json
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from utah import config
from utah.integrations import oauth


def _cp(returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=["security"], returncode=returncode,
                                       stdout=stdout, stderr="")


# ── read_blob ─────────────────────────────────────────────────────────────────

def test_read_blob_parses_keychain_json(monkeypatch):
    blob = {"token": "t", "refresh_token": "r"}
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(0, json.dumps(blob) + "\n"))
    assert oauth.read_blob() == blob


def test_read_blob_missing_item_returns_none(monkeypatch):
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(44, ""))
    assert oauth.read_blob() is None


def test_read_blob_empty_payload_returns_none(monkeypatch):
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(0, "   \n"))
    assert oauth.read_blob() is None


def test_read_blob_corrupt_json_raises_oautherror(monkeypatch):
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(0, "{not json"))
    with pytest.raises(oauth.OAuthError, match="corrupt"):
        oauth.read_blob()


def test_read_blob_non_darwin_is_none(monkeypatch):
    monkeypatch.setattr(oauth.platform, "system", lambda: "Linux")
    assert oauth.read_blob() is None
    assert oauth.write_blob({"a": 1}) is False   # nothing written off-macOS


def test_security_timeout_is_a_miss_not_a_crash(monkeypatch):
    """A wedged Keychain (security(1) hang) must read as 'no blob', not blow the
    OAuthError contract with a raw TimeoutExpired."""
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=8)

    monkeypatch.setattr(subprocess, "run", hang)
    assert oauth.read_blob() is None


# ── write_blob honesty ────────────────────────────────────────────────────────

def test_write_blob_reports_success_and_failure(monkeypatch):
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(0))
    assert oauth.write_blob({"token": "t"}) is True
    monkeypatch.setattr(oauth, "_security", lambda *a: _cp(36))
    assert oauth.write_blob({"token": "t"}) is False


# ── _expired ──────────────────────────────────────────────────────────────────

def _iso(delta_s: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def test_expired_taxonomy():
    assert oauth._expired({}) is True                                   # no token at all
    assert oauth._expired({"token": "t"}) is True                       # no expiry
    assert oauth._expired({"token": "t", "expiry": "garbage"}) is True  # unparseable
    assert oauth._expired({"token": "t", "expiry": _iso(3600)}) is False
    assert oauth._expired({"token": "t", "expiry": _iso(30)}) is True   # inside 60s skew
    assert oauth._expired({"token": "t", "expiry": _iso(-10)}) is True


def test_expired_handles_z_suffix_and_naive_utc():
    z = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert oauth._expired({"token": "t", "expiry": z}) is False
    naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert oauth._expired({"token": "t", "expiry": naive}) is False


# ── refresh_blob ──────────────────────────────────────────────────────────────

def test_refresh_blob_success_updates_token_and_expiry(monkeypatch):
    monkeypatch.setattr(oauth, "_post_form",
                        lambda url, f: (200, {"access_token": "NEW", "expires_in": 100,
                                              "refresh_token": "R2"}))
    blob = oauth.refresh_blob({"refresh_token": "r", "client_id": "c", "client_secret": "s"})
    assert blob["token"] == "NEW" and blob["refresh_token"] == "R2"
    assert oauth._expired(blob) is False


def test_refresh_blob_requires_refresh_token():
    with pytest.raises(oauth.OAuthError, match="refresh_token"):
        oauth.refresh_blob({"token": "t"})


def test_refresh_blob_invalid_grant_is_named(monkeypatch):
    monkeypatch.setattr(oauth, "_post_form",
                        lambda url, f: (400, {"error": "invalid_grant"}))
    with pytest.raises(oauth.OAuthError, match="invalid_grant"):
        oauth.refresh_blob({"refresh_token": "r", "client_id": "c", "client_secret": "s"})


def test_refresh_blob_non_200_raises_oautherror(monkeypatch):
    monkeypatch.setattr(oauth, "_post_form", lambda url, f: (500, "server sad"))
    with pytest.raises(oauth.OAuthError, match="500"):
        oauth.refresh_blob({"refresh_token": "r", "client_id": "c", "client_secret": "s"})


def test_refresh_blob_200_without_access_token_is_oautherror_not_keyerror(monkeypatch):
    """Google answering 200 with an odd payload must surface as OAuthError —
    a raw KeyError escapes the contract every caller catches."""
    monkeypatch.setattr(oauth, "_post_form", lambda url, f: (200, {"scope": "x"}))
    with pytest.raises(oauth.OAuthError, match="access_token"):
        oauth.refresh_blob({"refresh_token": "r", "client_id": "c", "client_secret": "s"})


def test_refresh_blob_falls_back_to_google_json_for_client(monkeypatch, tmp_path):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "google.json").write_text(
        json.dumps({"client_id": "GC", "client_secret": "GS"}))
    monkeypatch.setattr(config, "UTAH_HOME", tmp_path)
    seen = {}

    def post(url, fields):
        seen.update(fields)
        return 200, {"access_token": "T", "expires_in": 60}

    monkeypatch.setattr(oauth, "_post_form", post)
    blob = oauth.refresh_blob({"refresh_token": "r"})
    assert seen["client_id"] == "GC" and seen["client_secret"] == "GS"
    assert blob["client_id"] == "GC"             # persisted for next refresh


# ── _post_form network failure → OAuthError ───────────────────────────────────

def test_post_form_network_down_is_oautherror(monkeypatch):
    def dead(req, timeout=20):
        raise urllib.error.URLError(OSError("dns down"))

    monkeypatch.setattr("urllib.request.urlopen", dead)
    with pytest.raises(oauth.OAuthError, match="unreachable"):
        oauth._post_form(oauth.TOKEN_URL, {"a": "b"})


def test_post_form_http_error_returns_code_and_parsed_body(monkeypatch):
    import io

    def http_400(req, timeout=20):
        raise urllib.error.HTTPError(oauth.TOKEN_URL, 400, "Bad", {},
                                     io.BytesIO(b'{"error":"invalid_grant"}'))

    monkeypatch.setattr("urllib.request.urlopen", http_400)
    status, payload = oauth._post_form(oauth.TOKEN_URL, {"a": "b"})
    assert status == 400 and payload == {"error": "invalid_grant"}


# ── access_token ──────────────────────────────────────────────────────────────

def test_access_token_fresh_blob_skips_refresh(monkeypatch):
    def no_refresh(blob):
        raise AssertionError("refresh must not fire on a fresh token")

    monkeypatch.setattr(oauth, "refresh_blob", no_refresh)
    assert oauth.access_token({"token": "T", "expiry": _iso(3600)}) == "T"


def test_access_token_expired_blob_refreshes(monkeypatch):
    monkeypatch.setattr(oauth, "refresh_blob",
                        lambda b: {**b, "token": "FRESH", "expiry": _iso(3600)})
    assert oauth.access_token({"token": "old", "expiry": _iso(-5),
                               "refresh_token": "r"}) == "FRESH"


def test_access_token_no_token_after_refresh_raises(monkeypatch):
    monkeypatch.setattr(oauth, "refresh_blob", lambda b: {"expiry": _iso(3600)})
    with pytest.raises(oauth.OAuthError, match="no access token"):
        oauth.access_token({"refresh_token": "r"})


# ── gmail_profile / email helpers ─────────────────────────────────────────────

class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gmail_profile_happy_path(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob",
                        lambda service=oauth.UTAH_SERVICE, account="gmail":
                        {"token": "T", "expiry": _iso(3600)})
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=20: _Resp({"emailAddress": "x@y.com",
                                                       "messagesTotal": 5,
                                                       "threadsTotal": 2}))
    p = oauth.gmail_profile()
    assert p["messages_total"] == 5 and p["service"] == oauth.UTAH_SERVICE


def test_gmail_profile_no_blob_raises(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob", lambda **k: None)
    with pytest.raises(oauth.OAuthError, match="no OAuth token"):
        oauth.gmail_profile()


def test_gmail_profile_network_down_is_oautherror_not_urlerror(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob", lambda **k: {"token": "T", "expiry": _iso(3600)})

    def dead(req, timeout=20):
        raise urllib.error.URLError(OSError("offline"))

    monkeypatch.setattr("urllib.request.urlopen", dead)
    with pytest.raises(oauth.OAuthError):
        oauth.gmail_profile()


def test_email_helpers_never_raise_on_oautherror(monkeypatch):
    def boom(**k):
        raise oauth.OAuthError("no token")

    monkeypatch.setattr(oauth, "gmail_profile", boom)
    assert oauth.ace_gmail_email() is None
    assert oauth.utah_gmail_email() is None


def test_copy_ace_to_utah_refuses_wrong_account(monkeypatch):
    monkeypatch.setattr(oauth, "read_blob", lambda service=None, account="gmail": {"token": "t"})
    monkeypatch.setattr(oauth, "gmail_profile",
                        lambda service=None: {"email": "wrong@person.com"})
    wrote = []
    monkeypatch.setattr(oauth, "write_blob", lambda b, **k: wrote.append(b) or True)
    assert oauth.copy_ace_to_utah_if_correct() is False
    assert wrote == []                           # never copies a wrong-account token
