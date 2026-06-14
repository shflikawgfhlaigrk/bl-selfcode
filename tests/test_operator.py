"""Tests for Ace operator self-heal sweep."""
from __future__ import annotations

import json
from pathlib import Path

from utah import operator


def _patch_green(monkeypatch, *, mail=None, substrate=None, app=None, permissions=None):
    """Patch every sweep dependency green so each test flips exactly one signal."""
    monkeypatch.setattr(operator, "repair_substrate", lambda **k: list(substrate or []))
    monkeypatch.setattr(operator, "repair_tailserve", lambda **k: {"ok": True})
    monkeypatch.setattr(
        operator,
        "repair_integrations",
        lambda **k: {"secrets": {}, "mail": dict(mail or {"ok": True, "gated": False}), "human": []},
    )
    monkeypatch.setattr(operator, "sweep_failures", lambda **k: [])
    monkeypatch.setattr(
        operator, "ensure_app", lambda **k: dict(app or {"ok": True, "path": "/tmp/Ace.app"})
    )
    monkeypatch.setattr(
        operator,
        "permissions_status",
        lambda **k: dict(permissions or {"present": False, "ok": False}),
    )
    monkeypatch.setattr(operator.config, "canspam_configured", lambda: True)
    monkeypatch.setattr(operator, "_remember_owner_facts", lambda: None)
    monkeypatch.setattr("utah.revenue_heal.scan", lambda *a, **k: [])  # don't touch live DB
    monkeypatch.setattr(
        "utah.revenue_heal.outcome_gate",
        lambda *a, **k: {"ok": False, "assessable": False, "reason": "test"},
    )


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
    _patch_green(monkeypatch)

    result = operator.run(write_status=True)
    assert result["canspam_ready"] is True
    assert result["revenue"] == []                # revenue self-heal sweep wired into the operator
    assert result["ok"] is True                   # everything checked genuinely works
    assert result["mail_gated"] is False
    assert (run_dir / "operator.json").is_file()
    on_disk = json.loads((run_dir / "operator.json").read_text(encoding="utf-8"))
    assert on_disk["ok"] is True and on_disk["mail_gated"] is False


def test_gated_mail_is_not_ok(monkeypatch):
    """A gated (never-configured) mail integration must NOT report the sweep ok."""
    _patch_green(monkeypatch, mail={"ok": False, "gated": True})
    result = operator.run(write_status=False)
    assert result["ok"] is False
    assert result["mail_gated"] is True           # surfaced separately for the deck


def test_mail_auth_failure_is_not_ok(monkeypatch):
    _patch_green(monkeypatch, mail={"ok": False, "gated": False, "error": "auth"})
    result = operator.run(write_status=False)
    assert result["ok"] is False
    assert result["mail_gated"] is False


def test_failed_substrate_repair_is_not_ok(monkeypatch):
    _patch_green(
        monkeypatch,
        substrate=[{"target": "postgres", "action": "kickstart com.utah.postgres", "ok": False}],
    )
    result = operator.run(write_status=False)
    assert result["ok"] is False


def test_sweep_ensures_ace_app(monkeypatch):
    """A2: the 5-minute operator sweep keeps Ace.app built via operator_app.ensure."""
    _patch_green(monkeypatch)
    result = operator.run(write_status=False, app_fn=lambda: {"ok": True, "path": "/x/Ace.app"})
    assert result["app"] == {"ok": True, "path": "/x/Ace.app"}


def test_app_build_failure_is_not_ok(monkeypatch):
    _patch_green(monkeypatch, app={"ok": False, "detail": "stub missing"})
    result = operator.run(write_status=False)
    assert result["ok"] is False
    assert result["app"]["ok"] is False


def test_ensure_app_wraps_operator_app(monkeypatch):
    monkeypatch.setattr("utah.operator_app.ensure", lambda **k: Path("/tmp/Ace.app"))
    out = operator.ensure_app()
    assert out["ok"] is True and out["path"].endswith("Ace.app")


def test_ensure_app_honest_on_failure():
    out = operator.ensure_app(ensure_fn=lambda **k: None)
    assert out["ok"] is False and "detail" in out


def test_ensure_app_never_raises():
    def boom(**k):
        raise RuntimeError("codesign exploded")

    out = operator.ensure_app(ensure_fn=boom)
    assert out["ok"] is False
    assert "codesign exploded" in out["detail"]


def test_permissions_status_absent():
    def missing():
        raise FileNotFoundError("no permissions.json")

    out = operator.permissions_status(read_fn=missing)
    assert out == {"present": False, "ok": False}


def test_permissions_status_present():
    blob = json.dumps({
        "ts": 123.0,
        "ok": True,
        "microphone": {"ok": True},
        "automation": {"ok": True},
        "deck": {"ok": True},
    })
    out = operator.permissions_status(read_fn=lambda: blob)
    assert out["present"] is True and out["ok"] is True
    assert out["microphone"] is True and out["automation"] is True and out["deck"] is True
    assert out["ts"] == 123.0


def test_permissions_status_never_raises_on_garbage():
    out = operator.permissions_status(read_fn=lambda: "not json {{{")
    assert out["present"] is False and out["ok"] is False


def test_run_includes_permissions(monkeypatch):
    """A3: every sweep surfaces the last permissions bootstrap honestly."""
    _patch_green(monkeypatch)
    result = operator.run(
        write_status=False,
        permissions_fn=lambda: {"present": True, "ok": True},
    )
    assert result["permissions"] == {"present": True, "ok": True}


def test_absent_permissions_does_not_flip_ok(monkeypatch):
    """Bootstrap is a one-time Michael action; never run = honest absent, not red."""
    _patch_green(monkeypatch, permissions={"present": False, "ok": False})
    result = operator.run(write_status=False)
    assert result["permissions"]["present"] is False
    assert result["ok"] is True


def test_assessable_outcome_failure_flips_ok(monkeypatch):
    """269 sends / 0 sales: substrate green must not mask revenue_ok false (J-033)."""
    _patch_green(monkeypatch)
    monkeypatch.setattr(
        "utah.revenue_heal.outcome_gate",
        lambda *a, **k: {
            "ok": False,
            "assessable": True,
            "sends": 269,
            "sales": 0,
            "window_h": 26,
            "reason": "NO outcome in 26h — 269 sends, 0 sales",
        },
    )
    result = operator.run(write_status=False)
    assert result["revenue_ok"] is False
    assert result["outcome"]["sends"] == 269
    assert result["ok"] is False


def test_unassessable_outcome_does_not_flip_ok(monkeypatch):
    """DB unreachable: honest unassessable — substrate ok stays ok until we can read ledgers."""
    _patch_green(monkeypatch)
    monkeypatch.setattr(
        "utah.revenue_heal.outcome_gate",
        lambda *a, **k: {
            "ok": False,
            "assessable": False,
            "sends": None,
            "sales": None,
            "reason": "cannot read revenue ledgers",
        },
    )
    result = operator.run(write_status=False)
    assert result["revenue_ok"] is False
    assert result["ok"] is True


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


# --- _notify_human dedup: one page per issue class per hour, across processes ----

def _human(tmp_path, *, now, pushes, issue="gmail_2fa"):
    operator._notify_human(
        issue, "mail is down",
        push_send=lambda *a, **k: pushes.append(a) or {"sent": True},
        now_fn=lambda: now,
        seen_path=tmp_path / "human_seen.json")


def test_notify_human_pages_once_per_window(tmp_path):
    from tests.fakes import FakeFailureStore
    from utah import failures
    failures.set_store(FakeFailureStore())
    pushes = []
    _human(tmp_path, now=1000.0, pushes=pushes)
    _human(tmp_path, now=1300.0, pushes=pushes)   # 5 min later: deduped
    assert len(pushes) == 1


def test_notify_human_repages_after_window(tmp_path):
    from tests.fakes import FakeFailureStore
    from utah import failures
    failures.set_store(FakeFailureStore())
    pushes = []
    _human(tmp_path, now=1000.0, pushes=pushes)
    _human(tmp_path, now=1000.0 + operator._HUMAN_DEDUP_S + 1, pushes=pushes)
    assert len(pushes) == 2


def test_notify_human_distinct_issues_each_page(tmp_path):
    from tests.fakes import FakeFailureStore
    from utah import failures
    failures.set_store(FakeFailureStore())
    pushes = []
    _human(tmp_path, now=1000.0, pushes=pushes, issue="gmail_2fa")
    _human(tmp_path, now=1000.0, pushes=pushes, issue="oauth_dead")
    assert len(pushes) == 2


def test_notify_human_survives_garbage_seen_file(tmp_path):
    from tests.fakes import FakeFailureStore
    from utah import failures
    failures.set_store(FakeFailureStore())
    (tmp_path / "human_seen.json").write_text("{not json")
    pushes = []
    _human(tmp_path, now=1000.0, pushes=pushes)
    assert len(pushes) == 1


def test_notify_human_always_records_the_failure(tmp_path):
    """The page is deduped; the failure log is not — every sweep documents the open item."""
    from tests.fakes import FakeFailureStore
    from utah import failures
    store = FakeFailureStore(); failures.set_store(store)
    pushes = []
    _human(tmp_path, now=1000.0, pushes=pushes)
    _human(tmp_path, now=1300.0, pushes=pushes)
    assert len([r for r in store.rows if r[2] == "needs_human"]) == 2


def test_open_url_launches_in_background_never_steals_focus(monkeypatch):
    """A repair/2FA URL prompt must open WITHOUT stealing focus — `open -g` — so a
    mail-broken operator sweep never yanks Michael's cursor to a browser tab."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = list(argv)
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
        return R()

    monkeypatch.setattr(operator.subprocess, "run", fake_run)
    ok = operator._open_url("https://myaccount.google.com/apppasswords")
    assert ok is True
    assert seen["argv"][:2] == ["open", "-g"], f"must background-open, got {seen['argv'][:3]}"
