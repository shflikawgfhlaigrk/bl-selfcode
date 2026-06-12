"""auth_repair boundary contracts: never-raises on bad creds files, cooldown
holds, state survives corruption, the browser launch degrades, and the full
repair sweep reports honestly (ok=False stays False; 2FA ping only when a
human tap is actually needed)."""
from __future__ import annotations

import json

import pytest

from utah import auth_repair, config


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Point every state/creds path at tmp so no test touches ~/.utah."""
    monkeypatch.setattr(auth_repair, "REPAIR_STATE", tmp_path / "auth_repair.json")
    monkeypatch.setattr(auth_repair.runtime, "RUN_DIR", tmp_path)
    monkeypatch.setattr(auth_repair, "GMAIL_CREDS", tmp_path / "gmail.json")
    monkeypatch.setattr(auth_repair, "GOOGLE_CREDS", tmp_path / "google.json")
    return tmp_path


# ── _oauth_loopback_consent: a never-raises boundary ─────────────────────────

def test_loopback_consent_missing_google_json_is_honest(isolated_state):
    r = auth_repair._oauth_loopback_consent()
    assert r["ok"] is False and "google.json" in r["error"]


def test_loopback_consent_malformed_google_json_never_raises(isolated_state):
    """Corrupt client creds used to raise JSONDecodeError straight out of the
    repair path — the boundary must return ok=False instead."""
    (isolated_state / "google.json").write_text("{this is not json", encoding="utf-8")
    r = auth_repair._oauth_loopback_consent()
    assert r["ok"] is False
    assert "google.json" in r["error"]


def test_loopback_consent_non_object_google_json_is_honest(isolated_state):
    (isolated_state / "google.json").write_text('["not", "an", "object"]',
                                                encoding="utf-8")
    r = auth_repair._oauth_loopback_consent()
    assert r["ok"] is False


def test_loopback_consent_missing_client_fields_is_honest(isolated_state):
    (isolated_state / "google.json").write_text("{}", encoding="utf-8")
    r = auth_repair._oauth_loopback_consent()
    assert r["ok"] is False and "client_id" in r["error"]


# ── cooldown + state file ─────────────────────────────────────────────────────

def test_repair_oauth_respects_cooldown(isolated_state, monkeypatch):
    monkeypatch.setattr(auth_repair, "diagnose_gmail",
                        lambda: {"ace_oauth_wrong": False, "utah_oauth_wrong": False,
                                 "oauth_missing": True, "utah_oauth_email": None})
    auth_repair._mark_attempt("oauth_repair")          # just attempted
    r = auth_repair.repair_gmail_oauth()
    assert r == {"action": "oauth_repair_skipped", "ok": False, "reason": "cooldown"}


def test_repair_oauth_force_overrides_cooldown(isolated_state, monkeypatch):
    monkeypatch.setattr(auth_repair, "diagnose_gmail",
                        lambda: {"ace_oauth_wrong": True, "utah_oauth_wrong": False,
                                 "oauth_missing": False})
    monkeypatch.setattr(auth_repair.oauth, "copy_ace_to_utah_if_correct", lambda: False)
    monkeypatch.setattr(auth_repair, "_oauth_loopback_consent",
                        lambda **k: {"ok": True, "started": True})
    auth_repair._mark_attempt("oauth_repair")
    r = auth_repair.repair_gmail_oauth(force=True)
    assert r["action"] == "oauth_reconsent_started" and r["started"] is True


def test_state_round_trip_and_corruption_recovery(isolated_state):
    auth_repair._mark_attempt("smtp_repair")
    assert auth_repair._recently_attempted("smtp_repair") is True
    assert auth_repair._recently_attempted("oauth_repair") is False
    auth_repair.REPAIR_STATE.write_text("{corrupt", encoding="utf-8")
    assert auth_repair._load_state() == {}             # corruption → clean slate
    assert auth_repair._recently_attempted("smtp_repair") is False


# ── normalize_gmail_json ──────────────────────────────────────────────────────

def test_normalize_missing_file_is_false(isolated_state):
    assert auth_repair.normalize_gmail_json() is False


def test_normalize_correct_from_is_a_noop(isolated_state):
    creds = isolated_state / "gmail.json"
    payload = {"from": config.OWNER_EMAIL, "app_password": "x"}
    creds.write_text(json.dumps(payload), encoding="utf-8")
    assert auth_repair.normalize_gmail_json() is False
    assert json.loads(creds.read_text()) == payload    # untouched


# ── open_chrome_ace degrade path ─────────────────────────────────────────────

def test_open_chrome_ace_falls_back_to_open_when_no_chrome(tmp_path, monkeypatch):
    monkeypatch.setattr("utah.integrations.browser.chrome_binary", lambda: None)
    monkeypatch.setattr(auth_repair, "_migrate_chrome_once", lambda: None)
    monkeypatch.setattr(auth_repair, "WC_CHROME", tmp_path / "absent-profile")
    calls: list = []
    monkeypatch.setattr(auth_repair.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)))
    assert auth_repair.open_chrome_ace("https://example.com/auth") is True
    (argv, kw), = calls
    # `-g`: background open — a repair prompt must never steal Michael's focus.
    assert argv == ["open", "-g", "https://example.com/auth"]
    assert kw.get("timeout") == 5                       # bounded


def test_open_chrome_ace_uses_profile_when_present(tmp_path, monkeypatch):
    profile = tmp_path / "chrome-ace"
    profile.mkdir()
    monkeypatch.setattr("utah.integrations.browser.chrome_binary", lambda: "/bin/echo")
    monkeypatch.setattr(auth_repair, "_migrate_chrome_once", lambda: None)
    monkeypatch.setattr(auth_repair, "WC_CHROME", profile)
    spawned: list = []

    class _P:
        pass

    monkeypatch.setattr(auth_repair.subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or _P())
    assert auth_repair.open_chrome_ace("https://example.com/x") is True
    assert spawned and f"--user-data-dir={profile}" in spawned[0]


# ── repair_gmail report honesty ───────────────────────────────────────────────

def _wire_sweep(monkeypatch, *, smtp, oauth_result, diag):
    monkeypatch.setattr(auth_repair, "normalize_gmail_json", lambda: False)
    monkeypatch.setattr("utah.secrets_sync.sync_business", lambda write=True: {})
    monkeypatch.setattr(auth_repair, "repair_gmail_smtp", lambda **k: smtp)
    monkeypatch.setattr(auth_repair, "repair_gmail_oauth", lambda **k: oauth_result)
    monkeypatch.setattr(auth_repair, "diagnose_gmail", lambda **k: diag)


def test_repair_gmail_needs_2fa_pings_once_when_human_tap_needed(monkeypatch):
    _wire_sweep(monkeypatch,
                smtp={"ok": False, "action": "smtp_still_failing"},
                oauth_result={"action": "oauth_reconsent_started", "started": True},
                diag={"utah_oauth_email": None})
    pings: list[str] = []
    report = auth_repair.repair_gmail(notify_2fa_fn=pings.append)
    assert report["ok"] is False and report["needs_2fa"] is True
    assert len(pings) == 1 and config.OWNER_EMAIL in pings[0]


def test_repair_gmail_smtp_ok_means_done_and_no_2fa_ping(monkeypatch):
    _wire_sweep(monkeypatch,
                smtp={"ok": True, "action": "smtp_ok"},
                oauth_result={"action": "noop", "ok": True},
                diag={"utah_oauth_email": config.OWNER_EMAIL})
    pings: list[str] = []
    report = auth_repair.repair_gmail(notify_2fa_fn=pings.append)
    assert report["ok"] is True and report["needs_2fa"] is False
    assert pings == []


def test_repair_gmail_none_oauth_email_in_diag_never_crashes(monkeypatch):
    """diag['utah_oauth_email']=None flowed into .lower() territory — the ok
    computation must treat it as 'not the owner', not crash."""
    _wire_sweep(monkeypatch,
                smtp={"ok": False, "action": "smtp_still_failing"},
                oauth_result={"action": "noop", "ok": True},
                diag={"utah_oauth_email": None})
    report = auth_repair.repair_gmail()
    assert report["ok"] is False


# ── url_with_login_hint ───────────────────────────────────────────────────────

def test_login_hint_appends_with_ampersand_when_query_exists():
    u = auth_repair.url_with_login_hint("https://example.com/p?x=1")
    assert "?x=1&" in u and "login_hint=" in u


def test_login_hint_does_not_override_explicit_params():
    u = auth_repair.url_with_login_hint("https://example.com/p",
                                        login_hint="other@example.com")
    assert "other%40example.com" in u
    assert config.OWNER_EMAIL.split("@")[0] not in u
