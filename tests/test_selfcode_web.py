"""The SELF-CODE page data layer: the four deck sections, all from REAL sources via
injectable boundaries (fake DB + fake git + fake propose), so the logic is proven
without a live Postgres / git tree. Real-or-empty everywhere; nothing fabricated;
the web edit lane is PROPOSE-ONLY (auto_merge=False, never merges main)."""
from __future__ import annotations

from utah.product import selfcode_web


# ---- section 2: recent_cycles / cycle_stats (selfcode_log) -------------------

class _FakeTS:
    def isoformat(self):
        return "2026-06-07T09:48:01"


def _fake_log_row(rid, *, task, domain, attempts, best_after=0.9, propagated=False):
    data = {"ts": 1.0, "ran": True, "task": task, "domain": domain, "steps": 1,
            "attempts": attempts, "best_after": best_after,
            "propagation": {"propagated": propagated}}
    return (rid, _FakeTS(), data)


def test_recent_cycles_flattens_real_log_rows():
    rows = [
        _fake_log_row(2, task="add docstring", domain="leads",
                      attempts=[{"task": "add docstring", "passed": True, "merged": True, "utility": 0.96}]),
        _fake_log_row(1, task="do x", domain="injected",
                      attempts=[{"task": "do x", "passed": False, "merged": False, "utility": 0.2}]),
    ]
    out = selfcode_web.recent_cycles(query_fn=lambda *a: rows)
    assert len(out) == 2
    first = out[0]
    assert first["id"] == 2 and first["task"] == "add docstring" and first["domain"] == "leads"
    assert first["passed"] is True and first["merged"] is True
    assert first["utility"] == 0.96 and first["attempts"] == 1
    assert out[1]["passed"] is False and out[1]["merged"] is False


def test_recent_cycles_picks_best_attempt_utility():
    rows = [_fake_log_row(1, task="t", domain="d", attempts=[
        {"passed": False, "merged": False, "utility": 0.3},
        {"passed": True, "merged": False, "utility": 0.91},
    ])]
    out = selfcode_web.recent_cycles(query_fn=lambda *a: rows)
    assert out[0]["utility"] == 0.91 and out[0]["passed"] is True


def test_recent_cycles_empty_on_db_error():
    def boom(*a):
        raise RuntimeError("no db")
    assert selfcode_web.recent_cycles(query_fn=boom) == []


def test_cycle_stats_real_counts():
    def q(sql, params=()):
        if "count(*) FROM selfcode_log" in sql and "jsonb" not in sql:
            return [(136,)]
        return [(69, 17, 100)]   # passed, merged, total attempts
    s = selfcode_web.cycle_stats(query_fn=q)
    assert s == {"cycles": 136, "attempts": 100, "passed": 69, "merged": 17}


def test_cycle_stats_zero_on_error():
    def boom(*a, **k):
        raise RuntimeError("x")
    assert selfcode_web.cycle_stats(query_fn=boom) == {
        "cycles": 0, "attempts": 0, "passed": 0, "merged": 0}


# ---- section 4: goals — every % traces to a real count ----------------------

def _stats_q(cycles, passed, merged, attempts):
    def q(sql, params=()):
        if "count(*) FROM selfcode_log" in sql and "jsonb" not in sql:
            return [(cycles,)]
        return [(passed, merged, attempts)]
    return q


def test_goals_pct_trace_to_real_counts():
    q = _stats_q(cycles=100, passed=70, merged=20, attempts=100)
    gls = selfcode_web.goals(query_fn=q, git_count_fn=lambda repo: 5)
    by = {g["name"]: g for g in gls}
    # 5 live + 5 clone = 10 merges of 25 target = 40%
    assert by["Autonomous merges"]["num"] == 10 and by["Autonomous merges"]["pct"] == 40
    # 70/100 passed
    assert by["Gate pass-rate"]["pct"] == 70
    # 20/100 merged
    assert by["Autonomous merge-rate"]["pct"] == 20
    # 100 cycles / 200 target = 50%
    assert by["Self-code knowledge"]["pct"] == 50


def test_goals_never_exceed_100_and_zero_safe():
    q = _stats_q(cycles=0, passed=0, merged=0, attempts=0)
    gls = selfcode_web.goals(query_fn=q, git_count_fn=lambda repo: 0)
    for g in gls:
        assert 0 <= g["pct"] <= 100


def test_goals_merges_count_both_repos():
    q = _stats_q(cycles=10, passed=5, merged=2, attempts=10)
    seen = []
    gls = selfcode_web.goals(query_fn=q, git_count_fn=lambda repo: (seen.append(repo) or 3))
    assert len(seen) == 2   # live repo + clone both counted
    merges = next(g for g in gls if g["name"] == "Autonomous merges")
    assert merges["num"] == 6


# ---- section 3: run_edit — REAL governed, propose-only ----------------------

def test_run_edit_is_propose_only(monkeypatch):
    """The web edit lane MUST call propose_governed with auto_merge=False — a
    web-triggered edit can never auto-merge to main."""
    captured = {}

    def fake_propose(task):
        return {"task": task, "applied": True, "tests_passed": True, "merged": False,
                "branch": "selfcode/x", "tier": "A", "utility": 0.95, "elapsed_s": 12.0,
                "reason": "suite green"}

    out = selfcode_web.run_edit("tidy a docstring", propose_fn=fake_propose,
                                diff_fn=lambda b: "diff --git a/x b/x\n+ok")
    assert out["ran"] is True and out["passed"] is True
    assert out["merged"] is False              # propose-only invariant
    assert out["branch"] == "selfcode/x" and out["tier"] == "A"
    assert out["diff"].startswith("diff --git")


def test_run_edit_default_propose_uses_auto_merge_false(monkeypatch):
    """Without an injected propose_fn, the default path calls selfcode.propose_governed
    with auto_merge=False and the isolated clone repo."""
    from utah import selfcode
    seen = {}

    def fake_pg(task, *, repo=None, auto_merge=None, **kw):
        seen.update(task=task, repo=repo, auto_merge=auto_merge)
        return {"task": task, "tests_passed": True, "merged": False, "branch": "b"}

    monkeypatch.setattr(selfcode, "enabled", lambda: True)
    monkeypatch.setattr(selfcode, "propose_governed", fake_pg)
    out = selfcode_web.run_edit("do a thing", repo="/tmp/clone",
                                run_claude=lambda t: None, diff_fn=lambda b: "")
    assert seen["auto_merge"] is False and seen["repo"] == "/tmp/clone"
    assert out["merged"] is False


def test_run_edit_empty_request():
    assert selfcode_web.run_edit("   ")["ran"] is False


def test_run_edit_respects_kill_switch(monkeypatch):
    from utah import selfcode
    monkeypatch.setattr(selfcode, "enabled", lambda: False)
    out = selfcode_web.run_edit("anything")
    assert out["ran"] is False and "kill switch" in out["error"]


def test_run_edit_never_raises_on_failure(monkeypatch):
    def boom(task):
        raise RuntimeError("claude crashed")
    out = selfcode_web.run_edit("x", propose_fn=boom)
    assert out["ran"] is False and "claude crashed" in out["error"]


def test_shape_result_caps_diff_and_reason():
    res = {"task": "t", "tests_passed": True, "branch": "b",
           "reason": "z" * 1000, "diff": "d" * 9000}
    s = selfcode_web.shape_result(res)
    assert len(s["reason"]) == 400 and len(s["diff"]) == 6000
    assert s["merged"] is False
