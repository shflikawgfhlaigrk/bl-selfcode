"""Ace self-knowledge: "what are you working on right now" answered from the LIVE activity
ledger (open receipts), never guessed. Complements control.diagnose() (health) and
introspect.self_model() ("who/what are you")."""
from __future__ import annotations

import pytest

from utah import control


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    d = tmp_path / "activity"
    monkeypatch.setattr(control, "ACTIVITY_DIR", d)
    monkeypatch.setattr(control, "LEDGER", d / "actions.jsonl")
    return d


def test_working_on_reports_running_actions():
    control.begin("improve_apps", "building leads", timeout=1500)
    control.begin("voice_hardening", "heartbeat thread", timeout=60)
    res = control.working_on()
    assert res["ok"] is True
    assert "improve_apps" in res["summary"]
    assert "voice_hardening" in res["summary"]
    # evidence carries the real active receipts
    actions = [a["action"] for a in res["evidence"]["active"]]
    assert "improve_apps" in actions and "voice_hardening" in actions


def test_working_on_is_honest_when_idle():
    res = control.working_on()
    assert res["ok"] is True
    assert "nothing" in res["summary"].lower() or "idle" in res["summary"].lower()
    assert res["evidence"]["active"] == []


def test_working_on_mentions_last_finished_when_idle():
    rid = control.begin("heal", "healing")
    control.end(rid, ok=True, summary="reloaded daemon")
    res = control.working_on()
    assert res["evidence"]["active"] == []
    assert "heal" in res["summary"]  # surfaces the last finished action when idle


@pytest.mark.parametrize("phrase", [
    "what are you working on",
    "what are you doing right now",
    "what are you up to",
    "ace what are you working on?",
])
def test_classify_routes_working_on(phrase):
    assert control.classify(phrase) == "working_on"


@pytest.mark.parametrize("phrase", [
    "are you healthy",        # -> diagnose, not working_on
    "what's wrong",           # -> diagnose
    "worker status",          # -> workers
])
def test_classify_does_not_confuse_health_with_activity(phrase):
    assert control.classify(phrase) != "working_on"


def test_run_working_on_returns_speakable_summary():
    control.begin("research_engine", "scanning QQQ", timeout=300)
    out = control.run("what are you working on")
    assert "research_engine" in out
