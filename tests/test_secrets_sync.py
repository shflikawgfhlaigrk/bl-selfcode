"""Secrets hydration — the contract: never overwrite a real value with a placeholder,
migrate the legacy ~/.ace config exactly ONCE, fix the owner-email typo in any casing,
write secrets 0600, and NEVER raise out of sync_all (an unwritable disk degrades to an
honest report). All paths are monkeypatched to tmp; nothing touches ~/.utah or ~/.ace."""
from __future__ import annotations

import json
import stat

import pytest

from utah import config, secrets_sync


@pytest.fixture()
def paths(monkeypatch, tmp_path):
    """Point every module path constant at tmp so the tests are hermetic."""
    secrets = tmp_path / "secrets"
    legacy = tmp_path / "ace"
    legacy.mkdir()
    monkeypatch.setattr(secrets_sync, "SECRETS", secrets)
    monkeypatch.setattr(secrets_sync, "GMAIL", secrets / "gmail.json")
    monkeypatch.setattr(secrets_sync, "BUSINESS", secrets / "business.json")
    monkeypatch.setattr(secrets_sync, "GOOGLE", secrets / "google.json")
    monkeypatch.setattr(secrets_sync, "OUTREACH_CFG", secrets / "outreach-config.yaml")
    monkeypatch.setattr(secrets_sync, "OAUTH_CFG", secrets / "google-oauth.yaml")
    monkeypatch.setattr(secrets_sync, "_OUTREACH_CFG_LEGACY", legacy / "outreach-config.yaml")
    monkeypatch.setattr(secrets_sync, "_OAUTH_CFG_LEGACY", legacy / "config.yaml")
    # memory is out of scope here — its own test covers the wrap
    monkeypatch.setattr(secrets_sync, "sync_business_from_memory", lambda: None)
    return secrets, legacy


# ── building blocks ──────────────────────────────────────────────────────────
def test_is_real_rejects_placeholders_and_blank():
    for junk in ("", "  ", "REPLACE_ME", "your email here", "x@example.com",
                 "[CAN-SPAM address]", config.CANSPAM_PLACEHOLDER, None):
        assert secrets_sync._is_real(junk) is False, junk
    assert secrets_sync._is_real("28 Dogwood Rd") is True


def test_parse_yaml_kv_reads_simple_pairs_and_skips_noise(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('# comment\n\nfrom_name: "Michael"\nphysical_address: 28 Dogwood Rd\nweird line\n')
    kv = secrets_sync._parse_yaml_kv(p)
    assert kv["from_name"] == "Michael"
    assert kv["physical_address"] == "28 Dogwood Rd"


def test_parse_yaml_kv_missing_file_is_empty(tmp_path):
    assert secrets_sync._parse_yaml_kv(tmp_path / "nope.yaml") == {}


# ── one-time legacy migration ────────────────────────────────────────────────
def test_migrate_legacy_copies_once_then_never_reads_ace_again(paths):
    secrets, legacy = paths
    (legacy / "outreach-config.yaml").write_text("from_name: Michael\n")
    secrets_sync._migrate_legacy_once()
    target = secrets / "outreach-config.yaml"
    assert target.read_text() == "from_name: Michael\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # second pass with CHANGED legacy must NOT re-copy (the ~/.utah copy is authoritative)
    (legacy / "outreach-config.yaml").write_text("from_name: SOMEONE ELSE\n")
    secrets_sync._migrate_legacy_once()
    assert target.read_text() == "from_name: Michael\n"


# ── owner-email typo fix ─────────────────────────────────────────────────────
def test_fix_typo_email_any_casing(paths):
    secrets, _ = paths
    cfg = secrets / "outreach-config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("from_address: MTHBurnsbarber@Gmail.com\n")   # MIXED case
    assert secrets_sync._fix_ace_outreach_email() is True
    assert config.OWNER_EMAIL in cfg.read_text()
    assert "mthburnsbarber" not in cfg.read_text().lower()


# ── sync_business ────────────────────────────────────────────────────────────
def test_sync_business_fills_missing_from_ace_and_writes_0600(paths):
    secrets, _ = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "outreach-config.yaml").write_text(
        "from_name: Michael Barber\nphysical_address: 28 Dogwood Rd\n")
    r = secrets_sync.sync_business(write=True)
    assert "from_name←ace" in r["updated"] and "physical_address←ace" in r["updated"]
    assert r["configured"] is True
    biz = json.loads((secrets / "business.json").read_text())
    assert biz["from_name"] == "Michael Barber"
    assert stat.S_IMODE((secrets / "business.json").stat().st_mode) == 0o600


def test_sync_business_never_overwrites_real_with_placeholder(paths):
    secrets, _ = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "business.json").write_text(json.dumps(
        {"from_name": "Real Name", "physical_address": "28 Dogwood Rd"}))
    (secrets / "outreach-config.yaml").write_text(
        "from_name: REPLACE_ME\nphysical_address: [CAN-SPAM address placeholder]\n")
    r = secrets_sync.sync_business(write=True)
    biz = json.loads((secrets / "business.json").read_text())
    assert biz["from_name"] == "Real Name"                 # untouched
    assert biz["physical_address"] == "28 Dogwood Rd"      # untouched
    assert not [u for u in r["updated"] if "from_name" in u or "physical" in u]


def test_sync_business_reports_missing_address_honestly(paths):
    r = secrets_sync.sync_business(write=False)
    assert "physical_address" in r["missing"] and r["configured"] is False


# ── sync_google ──────────────────────────────────────────────────────────────
def test_sync_google_seeds_client_from_ace_and_flags_refresh_token(paths):
    secrets, _ = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "google-oauth.yaml").write_text(
        'google_oauth:\n  client_id: "abc.apps.googleusercontent.com"\n  client_secret: "shh"\n')
    r = secrets_sync.sync_google(write=True)
    assert "client_id←ace" in r["updated"] and "client_secret←ace" in r["updated"]
    assert any("refresh_token" in m for m in r["missing"])
    g = json.loads((secrets / "google.json").read_text())
    assert g["client_id"] == "abc.apps.googleusercontent.com"
    assert stat.S_IMODE((secrets / "google.json").stat().st_mode) == 0o600


def test_sync_google_skips_when_refresh_token_present(paths):
    secrets, _ = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "google.json").write_text(json.dumps({"refresh_token": "tok"}))
    r = secrets_sync.sync_google(write=True)
    assert r["updated"] == [] and "refresh_token" in r.get("skipped", "")


# ── never-raises boundary ────────────────────────────────────────────────────
def test_sync_all_never_raises_when_disk_is_unwritable(paths, monkeypatch):
    secrets, _ = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "outreach-config.yaml").write_text("from_name: Michael Barber\n")

    def deny(path, data):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(secrets_sync, "_save_json", deny)
    report = secrets_sync.sync_all(write=True)               # must NOT raise
    assert report["business"].get("write_failed") or report["business"]["updated"] == []


def test_sync_all_never_raises_when_a_section_explodes(paths, monkeypatch):
    monkeypatch.setattr(secrets_sync, "sync_google",
                        lambda *, write: (_ for _ in ()).throw(RuntimeError("boom")))
    report = secrets_sync.sync_all(write=False)              # must NOT raise
    assert "error" in report["google"]
    assert isinstance(report["still_missing"], list)


def test_sync_business_from_memory_survives_dead_backend(monkeypatch):
    """A broken memory backend must degrade to None (logged), never raise into the
    foundation probe loop."""
    from utah import memory

    monkeypatch.setattr(memory, "init", lambda: None)
    monkeypatch.setattr(memory, "recall",
                        lambda q, k=8: (_ for _ in ()).throw(RuntimeError("pg dead")))
    assert secrets_sync.sync_business_from_memory() is None
