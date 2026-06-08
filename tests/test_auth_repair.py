"""Tests for autonomous Gmail auth repair."""
from __future__ import annotations

from utah import auth_repair, config


def test_normalize_gmail_json_fixes_typo(tmp_path, monkeypatch):
    creds = tmp_path / "gmail.json"
    creds.write_text('{"from": "mthburnsbarber@gmail.com", "app_password": "x"}')
    monkeypatch.setattr(auth_repair, "GMAIL_CREDS", creds)
    assert auth_repair.normalize_gmail_json() is True
    import json

    data = json.loads(creds.read_text())
    assert data["from"] == config.OWNER_EMAIL


def test_url_with_login_hint():
    u = auth_repair.url_with_login_hint("https://example.com/path")
    assert "login_hint=mtuburnsbarber" in u
    assert "select_account" in u


def test_repair_gmail_oauth_noop_when_correct(monkeypatch):
    monkeypatch.setattr(
        auth_repair.oauth,
        "ace_gmail_email",
        lambda: config.OWNER_EMAIL,
    )
    monkeypatch.setattr(
        auth_repair.oauth,
        "utah_gmail_email",
        lambda: config.OWNER_EMAIL,
    )
    monkeypatch.setattr(auth_repair.oauth, "copy_ace_to_utah_if_correct", lambda: False)
    monkeypatch.setattr(auth_repair, "_recently_attempted", lambda k: False)
    r = auth_repair.repair_gmail_oauth()
    assert r["action"] == "noop"


def test_repair_gmail_oauth_starts_when_ace_typo(monkeypatch):
    monkeypatch.setattr(
        auth_repair.oauth,
        "ace_gmail_email",
        lambda: "mthburnsbarber@gmail.com",
    )
    monkeypatch.setattr(auth_repair.oauth, "utah_gmail_email", lambda: None)
    monkeypatch.setattr(auth_repair.oauth, "copy_ace_to_utah_if_correct", lambda: False)
    monkeypatch.setattr(auth_repair, "_recently_attempted", lambda k: False)
    monkeypatch.setattr(auth_repair, "_oauth_loopback_consent", lambda **k: {"ok": True, "started": True})
    r = auth_repair.repair_gmail_oauth()
    assert r["action"] == "oauth_reconsent_started"
    assert r.get("started") is True
