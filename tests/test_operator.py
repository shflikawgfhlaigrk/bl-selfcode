"""Tests for Ace operator self-heal sweep."""
from __future__ import annotations

from utah import operator


def test_repair_substrate_kickstarts_when_down():
    calls = []

    def kick(label):
        calls.append(label)
        return {"action": label, "ok": True, "detail": ""}

    out = operator.repair_substrate(
        pg_fn=lambda: False,
        sup_fn=lambda: True,
        kickstart_fn=kick,
    )
    assert any(x.get("target") == "postgres" for x in out)
    assert "com.utah.postgres" in calls


def test_run_writes_status(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(operator.runtime, "RUN_DIR", run_dir)
    monkeypatch.setattr(operator, "repair_substrate", lambda **k: [])
    monkeypatch.setattr(operator, "repair_tailserve", lambda **k: {"ok": True})
    monkeypatch.setattr(
        operator,
        "repair_integrations",
        lambda **k: {"secrets": {}, "mail": {"ok": True, "gated": False}, "human": []},
    )
    monkeypatch.setattr(operator, "sweep_failures", lambda **k: [])
    monkeypatch.setattr(operator.config, "canspam_configured", lambda: True)

    result = operator.run(write_status=True)
    assert result["canspam_ready"] is True
    assert (run_dir / "operator.json").is_file()


def test_mail_auth_failure_escalates_human():
    notified = []

    def notify(issue, msg, *, url=None):
        notified.append((issue, url, msg))

    operator.repair_integrations(
        mail_verify=lambda: {"ok": False, "gated": False, "error": "auth"},
        notify_fn=notify,
    )
    # Ace runs auth_repair first; human only if repair didn't open browser
    assert notified
    issue = notified[0][0]
    assert issue in ("gmail_2fa", "gmail_smtp")
