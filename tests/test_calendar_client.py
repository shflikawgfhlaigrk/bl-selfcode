"""Calendar — a REAL Google Calendar v3 client behind the same honest gate.

The old skeleton said "wired" but its real client could only raise — with creds present
it failed every time (documented-as-live). These tests prove the genuine path: access
token (direct, or minted from a refresh token), bounded HTTP, event id round-trip, and
honest gates/validation everywhere else. HTTP is injected — zero network in tests.
"""
from __future__ import annotations

import json

from utah import failures
from utah.integrations import calendar
from tests.fakes import FakeFailureStore


def _creds(tmp_path, monkeypatch, payload) -> None:
    p = tmp_path / "google.json"
    p.write_text(json.dumps(payload))
    monkeypatch.setattr(calendar, "GOOGLE_CREDS", p)


# --- auth_available is a REAL gate, not a stat() ------------------------------
def test_auth_unavailable_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar, "GOOGLE_CREDS", tmp_path / "nope.json")
    assert calendar.auth_available() is False


def test_auth_unavailable_when_garbled_or_unusable(tmp_path, monkeypatch):
    p = tmp_path / "google.json"
    p.write_text("{broken")
    monkeypatch.setattr(calendar, "GOOGLE_CREDS", p)
    assert calendar.auth_available() is False
    # parseable but unusable: client_id+secret with no refresh_token can mint nothing
    p.write_text(json.dumps({"client_id": "x", "client_secret": "y"}))
    assert calendar.auth_available() is False


def test_auth_available_with_token_or_refresh_trio(tmp_path, monkeypatch):
    _creds(tmp_path, monkeypatch, {"access_token": "AT"})
    assert calendar.auth_available() is True
    _creds(tmp_path, monkeypatch,
           {"client_id": "i", "client_secret": "s", "refresh_token": "r"})
    assert calendar.auth_available() is True


# --- the real client ----------------------------------------------------------
def test_create_event_posts_to_google_with_bearer_token(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(tmp_path, monkeypatch, {"access_token": "AT-123"})
    calls = []

    def fake_post(url, payload, headers=None, **kw):
        calls.append((url, payload, headers or {}))
        return {"id": "evt-77"}

    monkeypatch.setattr(calendar, "_http_post_json", fake_post)
    r = calendar.create_event("Standup", "2026-06-13T09:00:00-04:00",
                              "2026-06-13T09:15:00-04:00")
    assert r == {"created": True, "gated": False, "id": "evt-77", "title": "Standup"}
    url, payload, headers = calls[0]
    assert "calendars/primary/events" in url
    assert headers["Authorization"] == "Bearer AT-123"
    assert payload["summary"] == "Standup"
    assert payload["start"] == {"dateTime": "2026-06-13T09:00:00-04:00"}


def test_refresh_token_is_exchanged_before_the_event_post(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(tmp_path, monkeypatch,
           {"client_id": "CID", "client_secret": "SEC", "refresh_token": "REF"})
    calls = []

    def fake_post(url, payload, headers=None, **kw):
        calls.append((url, payload, headers or {}))
        if "oauth2" in url:
            assert payload["grant_type"] == "refresh_token" and payload["refresh_token"] == "REF"
            return {"access_token": "MINTED"}
        return {"id": "evt-1"}

    monkeypatch.setattr(calendar, "_http_post_json", fake_post)
    r = calendar.create_event("Call", "2026-06-13T10:00:00Z", "2026-06-13T10:30:00Z")
    assert r["created"] is True and r["id"] == "evt-1"
    assert len(calls) == 2 and "oauth2" in calls[0][0]
    assert calls[1][2]["Authorization"] == "Bearer MINTED"


def test_all_day_dates_use_date_not_datetime(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(tmp_path, monkeypatch, {"access_token": "AT"})
    seen = {}

    def fake_post(url, payload, headers=None, **kw):
        seen.update(payload)
        return {"id": "evt-2"}

    monkeypatch.setattr(calendar, "_http_post_json", fake_post)
    calendar.create_event("Closing day", "2026-06-20", "2026-06-21")
    assert seen["start"] == {"date": "2026-06-20"}
    assert seen["end"] == {"date": "2026-06-21"}


def test_api_error_is_an_honest_recorded_failure(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _creds(tmp_path, monkeypatch, {"access_token": "AT"})

    def boom(url, payload, headers=None, **kw):
        raise RuntimeError("403: insufficient scope")

    monkeypatch.setattr(calendar, "_http_post_json", boom)
    r = calendar.create_event("X", "2026-06-13T10:00:00Z", "2026-06-13T11:00:00Z")
    assert r["created"] is False and r["gated"] is False
    assert "403" in r["error"]
    assert any("create_failed" in row[2] for row in store.rows)   # rows = (seq, source, kind, detail)


def test_refresh_without_access_token_in_reply_is_an_error(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _creds(tmp_path, monkeypatch,
           {"client_id": "i", "client_secret": "s", "refresh_token": "r"})
    monkeypatch.setattr(calendar, "_http_post_json",
                        lambda url, payload, headers=None, **kw: {"error": "invalid_grant"})
    r = calendar.create_event("X", "2026-06-13T10:00:00Z", "2026-06-13T11:00:00Z")
    assert r["created"] is False and "invalid_grant" in r["error"]


# --- validation + gate ----------------------------------------------------------
def test_missing_fields_never_reach_the_client(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    called = []
    r = calendar.create_event("", "2026-06-13T10:00:00Z", "2026-06-13T11:00:00Z",
                              client_fn=lambda *a: called.append(a) or "e")
    assert r["created"] is False and r["gated"] is False and "required" in r["error"]
    assert called == []


def test_gated_without_usable_auth(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(calendar, "GOOGLE_CREDS", tmp_path / "absent.json")
    r = calendar.create_event("X", "2026-06-13T10:00:00Z", "2026-06-13T11:00:00Z")
    assert r["created"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)  # rows = (seq, source, kind, detail)
