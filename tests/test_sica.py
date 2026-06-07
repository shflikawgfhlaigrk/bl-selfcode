"""Tests for SICA governance: the utility formula, graded pytest scoring, the
append-only archive, and selfcode.propose_governed (scores + archives every attempt)."""
from __future__ import annotations

from utah import selfcode, sica


# ── utility (exact paper formula) ────────────────────────────────────────────
def test_utility_perfect_fast_free():
    assert sica.utility(1.0, 0.0, 0.0, False) == 1.0


def test_utility_timeout_halves():
    assert sica.utility(1.0, 0.0, 0.0, True) == 0.5


def test_utility_zero_score_keeps_efficiency_terms():
    assert sica.utility(0.0, 0.0, 0.0, False) == 0.5   # 0 + .25 + .25


def test_utility_max_cost_and_time_zero_efficiency():
    assert sica.utility(1.0, 10.0, 300.0, False) == 0.5  # .5 + 0 + 0


def test_utility_half_cost_half_time():
    assert sica.utility(1.0, 5.0, 150.0, False) == 0.75  # .5 + .125 + .125


def test_utility_clamps_overflow():
    assert sica.utility(2.0, 99.0, 9999.0, False) == 0.5  # score clamped to 1, terms to 0


# ── graded pytest score ──────────────────────────────────────────────────────
def test_score_all_green():
    assert sica.score_from_pytest("181 passed in 3.1s") == 1.0


def test_score_some_failed_is_pass_rate():
    assert sica.score_from_pytest("2 failed, 179 passed in 3.1s") == round(179 / 181, 6)


def test_score_errors_count_against():
    assert sica.score_from_pytest("1 error, 180 passed") == round(180 / 181, 6)


def test_score_no_summary_is_zero():
    assert sica.score_from_pytest("") == 0.0
    assert sica.score_from_pytest("collected 0 items") == 0.0


# ── make_attempt ─────────────────────────────────────────────────────────────
def test_make_attempt_passed_scores_one():
    a = sica.make_attempt(task="t", branch="b", tier="A", passed=True,
                          output="5 passed", cost_usd=0.0, elapsed_s=1.0,
                          timed_out=False, merged=True, sha="abc", reason="green")
    assert a.score == 1.0 and a.utility > 0.9 and a.merged is True


def test_make_attempt_failed_is_graded():
    a = sica.make_attempt(task="t", branch="b", tier="A", passed=False,
                          output="1 failed, 9 passed", cost_usd=0.0, elapsed_s=0.0,
                          timed_out=False, merged=False, sha=None, reason="red")
    assert a.score == 0.9


# ── archive ──────────────────────────────────────────────────────────────────
def test_archive_record_entries_best_count(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    assert arch.count() == 0 and arch.best() is None
    arch.record(sica.make_attempt(task="lo", branch="b", tier="A", passed=False,
                                  output="1 failed, 1 passed", cost_usd=0, elapsed_s=10,
                                  timed_out=False, merged=False, sha=None, reason="x"))
    arch.record(sica.make_attempt(task="hi", branch="b", tier="A", passed=True,
                                  output="2 passed", cost_usd=0, elapsed_s=1,
                                  timed_out=False, merged=True, sha="s", reason="y"))
    assert arch.count() == 2
    best = arch.best()
    assert best["task"] == "hi" and best["passed"] is True   # argmax utility


def test_archive_survives_corrupt_line(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"utility": 0.9, "task": "ok"}\nNOT JSON\n', encoding="utf-8")
    arch = sica.Archive(p)
    assert arch.count() == 1 and arch.best()["task"] == "ok"


# ── propose_governed (scores + archives a real propose run) ──────────────────
def test_propose_governed_green_scores_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")  # enabled
    arch = sica.Archive(tmp_path / "arch.jsonl")
    r = selfcode.propose_governed(
        "add a docstring", archive=arch,
        run_claude=lambda t: None,
        run_tests=lambda: (True, "5 passed in 0.1s"),
        branch_fn=lambda slug: f"selfcode/{slug}",
        discard_fn=lambda: None,
        auto_merge=False,
        safety_intact_fn=lambda before: True,
    )
    assert r["tests_passed"] is True
    assert r["score"] == 1.0 and r["utility"] > 0.9
    assert r["archived"] is True
    assert arch.count() == 1 and arch.best()["passed"] is True


def test_propose_governed_red_is_graded_and_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    arch = sica.Archive(tmp_path / "arch.jsonl")
    seen = {"discarded": False}
    r = selfcode.propose_governed(
        "break something", archive=arch,
        run_claude=lambda t: None,
        run_tests=lambda: (False, "2 failed, 8 passed in 0.1s"),
        branch_fn=lambda slug: f"selfcode/{slug}",
        discard_fn=lambda: seen.update(discarded=True),
        auto_merge=False,
        safety_intact_fn=lambda before: True,
    )
    assert r["tests_passed"] is False and seen["discarded"] is True
    assert r["score"] == 0.8 and r["archived"] is True
    assert arch.count() == 1 and arch.best()["passed"] is False


def test_propose_governed_disabled_does_not_archive(tmp_path, monkeypatch):
    kill = tmp_path / "selfcode.disabled"
    kill.write_text("")                       # kill switch ON
    monkeypatch.setattr(selfcode, "KILL_SWITCH", kill)
    arch = sica.Archive(tmp_path / "arch.jsonl")
    r = selfcode.propose_governed("anything", archive=arch,
                                  run_claude=lambda t: None,
                                  run_tests=lambda: (True, "1 passed"))
    assert r.get("disabled") is True
    assert arch.count() == 0               # a kill-switched no-op is not an attempt
