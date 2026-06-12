"""secrets_sync remaining boundaries: the google.json write failure is reported (not
raised, not claimed as landed), and the memory-sourced CAN-SPAM address extraction
honors the source allowlist + placeholder filter. Hermetic — paths at tmp, memory
monkeypatched."""
from __future__ import annotations

import pytest

from utah import secrets_sync


@pytest.fixture()
def paths(monkeypatch, tmp_path):
    secrets = tmp_path / "secrets"
    monkeypatch.setattr(secrets_sync, "SECRETS", secrets)
    monkeypatch.setattr(secrets_sync, "GMAIL", secrets / "gmail.json")
    monkeypatch.setattr(secrets_sync, "BUSINESS", secrets / "business.json")
    monkeypatch.setattr(secrets_sync, "GOOGLE", secrets / "google.json")
    monkeypatch.setattr(secrets_sync, "OUTREACH_CFG", secrets / "outreach-config.yaml")
    monkeypatch.setattr(secrets_sync, "OAUTH_CFG", secrets / "google-oauth.yaml")
    return secrets


def test_sync_google_write_failure_is_reported_not_raised(paths, monkeypatch):
    secrets = paths
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / "google-oauth.yaml").write_text(
        'client_id: "abc.apps.googleusercontent.com"\nclient_secret: "shh"\n')

    def deny(path, data):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(secrets_sync, "_save_json", deny)
    r = secrets_sync.sync_google(write=True)              # must NOT raise
    assert "write_failed" in r
    assert not (secrets / "google.json").exists()          # nothing silently claimed


class _Hit:
    def __init__(self, content, source):
        self.content, self.source = content, source


def _patch_memory(monkeypatch, hits):
    from utah import memory

    monkeypatch.setattr(memory, "init", lambda: None)
    monkeypatch.setattr(memory, "recall", lambda q, k=8: hits)


def test_memory_address_found_from_user_source(monkeypatch):
    _patch_memory(monkeypatch, [_Hit("Michael's mailing address is 28 Dogwood Rd Newnan", "user")])
    assert secrets_sync.sync_business_from_memory() == "28 Dogwood Rd"


def test_memory_address_from_untrusted_source_is_ignored(monkeypatch):
    """A web-scraped/local-model turn must never become the legal CAN-SPAM address."""
    _patch_memory(monkeypatch, [_Hit("address is 99 Spoofed St", "web"),
                                _Hit("address is 99 Spoofed St", "local")])
    assert secrets_sync.sync_business_from_memory() is None


def test_memory_hit_without_attrs_is_skipped_not_a_crash(monkeypatch):
    _patch_memory(monkeypatch, ["a bare string hit"])
    assert secrets_sync.sync_business_from_memory() is None
