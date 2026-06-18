"""The unifying self-watch loop: diagnose -> auto-heal deterministic cases -> page Michael for
what needs a human -> log proof ONLY when something was wrong. NO autonomous code edits.

WHY (Michael, 2026-06-18): "get Ace to loop and work issues anytime something's wrong." The
detect->heal->page pieces existed (canary/heal/operator); watch() ties them into one cadence
that also runs the comprehensive diagnose() and shows up in the /activity proof panel.
"""
from __future__ import annotations

import pytest

from utah import control


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    d = tmp_path / "activity"
    monkeypatch.setattr(control, "ACTIVITY_DIR", d)
    monkeypatch.setattr(control, "LEDGER", d / "actions.jsonl")
    return d


def _diag(problems):
    return lambda: {"ok": not problems, "summary": "", "evidence": {"problems": list(problems)}}


def _heal(actions):
    return lambda: {"ok": True, "summary": "", "evidence": {"actions": list(actions)}}


def test_all_clear_writes_no_receipt_and_says_so():
    res = control.watch(diagnose_fn=_diag([]), heal_fn=_heal([]), notify_fn=lambda *a, **k: None)
    assert res["ok"] is True
    assert "clear" in res["summary"].lower()
    # no all-clear spam in the proof panel
    assert control.activity()["recent"] == []
    assert control.activity()["active"] == []


def test_heal_fixes_everything_no_page():
    pages = []
    # first diagnose finds 1; after heal, the (re)diagnose finds none.
    diags = iter([
        {"ok": False, "evidence": {"problems": [{"area": "job", "detail": "x dead"}]}},
        {"ok": True, "evidence": {"problems": []}},
    ])
    res = control.watch(diagnose_fn=lambda: next(diags),
                        heal_fn=_heal([{"target": "x", "fix": "kickstarted", "verified": True}]),
                        notify_fn=lambda *a, **k: pages.append(k.get("key")))
    assert res["ok"] is True
    assert res["evidence"]["remaining"] == []
    assert pages == []                       # nothing left → nobody paged
    rec = control.activity()["recent"]
    assert len(rec) == 1 and rec[0]["action"] == "watch"   # one consolidated receipt


def test_unhealable_problem_pages_michael():
    pages = []
    # heal can't fix it → the second diagnose still shows it.
    prob = {"area": "service", "detail": "deck not serving (:down)"}
    diags = iter([
        {"ok": False, "evidence": {"problems": [prob]}},
        {"ok": False, "evidence": {"problems": [prob]}},
    ])
    res = control.watch(diagnose_fn=lambda: next(diags), heal_fn=_heal([]),
                        notify_fn=lambda msg, *, key: pages.append((msg, key)))
    assert res["ok"] is False
    assert len(res["evidence"]["remaining"]) == 1
    assert len(pages) == 1
    assert "deck" in pages[0][0]                 # message names the problem
    assert pages[0][1].startswith("watch:")      # deduped by a stable key


def test_code_drift_is_logged_but_not_paged():
    # Uncommitted code is normal during dev — it shows in /activity but must NOT page (alert
    # fatigue). Only operational failures (jobs/services/brain) page.
    pages = []
    prob = {"area": "code", "detail": "3 uncommitted file(s) on branch X"}
    diags = iter([
        {"ok": False, "evidence": {"problems": [prob]}},
        {"ok": False, "evidence": {"problems": [prob]}},
    ])
    res = control.watch(diagnose_fn=lambda: next(diags), heal_fn=_heal([]),
                        notify_fn=lambda msg, *, key: pages.append(key))
    assert pages == []                                  # code drift never pages
    assert len(res["evidence"]["remaining"]) == 1       # but it IS surfaced/logged
    rec = control.activity()["recent"]
    assert rec and rec[0]["action"] == "watch"


def test_watch_receipt_records_detected_healed_remaining():
    diags = iter([
        {"ok": False, "evidence": {"problems": [{"area": "job", "detail": "a"},
                                                {"area": "drift", "detail": "b"}]}},
        {"ok": False, "evidence": {"problems": [{"area": "drift", "detail": "b"}]}},
    ])
    res = control.watch(diagnose_fn=lambda: next(diags),
                        heal_fn=_heal([{"target": "a", "fix": "fixed", "verified": True}]),
                        notify_fn=lambda *a, **k: None)
    ev = res["evidence"]
    assert len(ev["detected"]) == 2 and len(ev["healed"]) == 1 and len(ev["remaining"]) == 1


def test_diagnose_and_heal_support_quiet_mode():
    # watch relies on calling these without spamming their own receipts.
    import inspect
    assert "log" in inspect.signature(control.diagnose).parameters
    assert "log" in inspect.signature(control.heal).parameters
