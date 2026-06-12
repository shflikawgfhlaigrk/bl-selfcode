"""Tests for the Ace permission bootstrap + its live read-path through the operator."""
from __future__ import annotations

import json

from utah import operator, permissions


def _patch_probes(monkeypatch, *, mic_ok=True, auto_ok=True, deck_ok=True):
    monkeypatch.setattr(permissions.runtime, "ensure_runtime", lambda *a, **k: None)
    monkeypatch.setattr(permissions, "ensure_macos_flag", lambda: {"ok": True, "created": False})
    monkeypatch.setattr(permissions, "probe_microphone", lambda **k: {"ok": mic_ok})
    monkeypatch.setattr(
        permissions, "probe_automation", lambda **k: {"ok": auto_ok, "passed": 5, "total": 6}
    )
    monkeypatch.setattr(permissions, "probe_deck", lambda **k: {"ok": deck_ok})


def test_probe_automation_all_granted():
    out = permissions.probe_automation(notify_fn=lambda script: (True, "Finder"))
    assert out["ok"] is True
    assert out["passed"] >= 4


def test_probe_automation_denied_is_honest():
    out = permissions.probe_automation(notify_fn=lambda script: (False, "not authorized"))
    assert out["ok"] is False


def test_probe_deck_unreachable_is_honest():
    out = permissions.probe_deck(url="http://127.0.0.1:9/", timeout=0.3)
    assert out["ok"] is False and "error" in out


def test_bootstrap_writes_status(tmp_path, monkeypatch):
    status = tmp_path / "permissions.json"
    monkeypatch.setattr(permissions, "STATUS_PATH", status)
    _patch_probes(monkeypatch)

    payload = permissions.bootstrap(
        open_browser=False, restart_if_down=False, show_dialog=False, write_status=True
    )
    assert payload["ok"] is True
    assert status.is_file()
    assert json.loads(status.read_text(encoding="utf-8"))["ok"] is True


def test_bootstrap_honest_when_mic_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "STATUS_PATH", tmp_path / "permissions.json")
    _patch_probes(monkeypatch, mic_ok=False)
    payload = permissions.bootstrap(
        open_browser=False, restart_if_down=False, show_dialog=False, write_status=True
    )
    assert payload["ok"] is False


def test_operator_consumes_bootstrap_status(tmp_path, monkeypatch):
    """A3 round-trip: what bootstrap writes is what the 5-minute sweep reads."""
    status = tmp_path / "permissions.json"
    monkeypatch.setattr(permissions, "STATUS_PATH", status)
    _patch_probes(monkeypatch)
    permissions.bootstrap(
        open_browser=False, restart_if_down=False, show_dialog=False, write_status=True
    )

    out = operator.permissions_status(read_fn=lambda: status.read_text(encoding="utf-8"))
    assert out["present"] is True and out["ok"] is True
    assert out["microphone"] is True and out["deck"] is True
