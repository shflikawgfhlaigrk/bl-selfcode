"""secrets_sync._save_json must be atomic + non-destructive — the fix for credentials
that 'keep getting overwritten/lost'. A corrupt existing file is preserved as .bad; a
valid one is replaced cleanly; the result is always mode 0600 and fully-written JSON."""
from __future__ import annotations

import json

from utah import secrets_sync


def test_save_is_atomic_and_correct(tmp_path):
    p = tmp_path / "business.json"
    secrets_sync._save_json(p, {"physical_address": "28 Dogwood Rd, Newnan GA 30263"})
    assert json.loads(p.read_text())["physical_address"].endswith("30263")
    assert oct(p.stat().st_mode)[-3:] == "600"
    # no temp turds left behind
    assert not list(tmp_path.glob(".business.json.*.tmp"))


def test_corrupt_existing_file_is_backed_up_not_clobbered(tmp_path):
    p = tmp_path / "gmail.json"
    p.write_text("{ this is not valid json", encoding="utf-8")   # corrupt-but-present
    secrets_sync._save_json(p, {"address": "real@x.com", "app_password": "abc"})
    # new content landed AND the corrupt original was preserved (recoverable), not lost
    assert json.loads(p.read_text())["address"] == "real@x.com"
    assert (tmp_path / "gmail.json.bad").read_text().startswith("{ this is not")


def test_valid_existing_file_replaced_without_bad_backup(tmp_path):
    p = tmp_path / "google.json"
    p.write_text('{"refresh_token": "old"}', encoding="utf-8")
    secrets_sync._save_json(p, {"refresh_token": "new"})
    assert json.loads(p.read_text())["refresh_token"] == "new"
    assert not (tmp_path / "google.json.bad").exists()
