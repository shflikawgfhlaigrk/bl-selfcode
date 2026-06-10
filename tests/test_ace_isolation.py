"""Binding rule M: the RUNTIME never reads ~/.ace. The Ace config/profiles are
forward-migrated into ~/.utah exactly ONCE; every recurring read is from ~/.utah."""
from __future__ import annotations

from pathlib import Path

from utah import secrets_sync


def test_secrets_sync_reads_utah_not_ace_after_migration(tmp_path, monkeypatch):
    """Once the ~/.utah copy exists, the recurring sync reads it — never ~/.ace."""
    utah_cfg = tmp_path / "secrets" / "outreach-config.yaml"
    utah_cfg.parent.mkdir(parents=True)
    utah_cfg.write_text("from_name: Michael Barber\nphysical_address: 28 Dogwood Rd, Newnan GA 30263\n")
    legacy = tmp_path / "ace" / "outreach-config.yaml"   # a DIFFERENT (poison) legacy value
    legacy.parent.mkdir(parents=True)
    legacy.write_text("from_name: SHOULD-NOT-BE-READ\n")

    monkeypatch.setattr(secrets_sync, "OUTREACH_CFG", utah_cfg)
    monkeypatch.setattr(secrets_sync, "_OUTREACH_CFG_LEGACY", legacy)
    # target exists → migration is a no-op; the read comes from the ~/.utah copy
    secrets_sync._migrate_legacy_once()
    assert utah_cfg.read_text().startswith("from_name: Michael Barber")  # untouched
    parsed = secrets_sync._parse_yaml_kv(secrets_sync.OUTREACH_CFG)
    assert parsed["from_name"] == "Michael Barber"          # ~/.utah, not the legacy poison


def test_migrate_copies_legacy_only_when_utah_copy_absent(tmp_path, monkeypatch):
    target = tmp_path / "secrets" / "outreach-config.yaml"
    legacy = tmp_path / "ace" / "outreach-config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("from_name: From Legacy\n")
    monkeypatch.setattr(secrets_sync, "OUTREACH_CFG", target)
    monkeypatch.setattr(secrets_sync, "_OUTREACH_CFG_LEGACY", legacy)
    monkeypatch.setattr(secrets_sync, "_OAUTH_CFG_LEGACY", tmp_path / "ace" / "missing.yaml")
    monkeypatch.setattr(secrets_sync, "OAUTH_CFG", tmp_path / "secrets" / "google-oauth.yaml")
    assert not target.exists()
    secrets_sync._migrate_legacy_once()                     # one-time copy
    assert target.read_text() == "from_name: From Legacy\n"
    # mutate legacy; a second migrate must NOT re-read/overwrite (target now exists)
    legacy.write_text("from_name: Changed Later\n")
    secrets_sync._migrate_legacy_once()
    assert target.read_text() == "from_name: From Legacy\n"  # never read ~/.ace again


def test_no_module_defaults_point_at_ace_runtime_paths():
    """The live default paths the runtime reads must be under ~/.utah, not ~/.ace."""
    from utah.integrations import wc_feed

    assert "/.ace/" not in wc_feed.WC_PROFILE          # default profile is ~/.utah
    assert str(secrets_sync.OUTREACH_CFG).find("/.ace/") == -1
    assert str(secrets_sync.OAUTH_CFG).find("/.ace/") == -1
    # the legacy constants are allowed to point at ~/.ace (one-time migration source only)
    assert "/.ace/" in str(secrets_sync._OUTREACH_CFG_LEGACY)
