"""Doc-13 tiered self-coding (policy-as-data A/B/C/D) + byte-checked off-limits +
kill-switch smoke.

The tier of a change is the *strictest* tier among the files it touches, and the
tier sets the autonomy ceiling:

* **D — off-limits** (the self-coder, auth, flock/hard-exit, governor, config, the
  no-fabrication brain). A run that edits one is rolled back + documented, even on
  a GREEN suite. Enforced by a byte-check snapshot around the coding run.
* **C — 5-rung** (the daemon spine, schema migrations) — never auto-merges.
* **B — batch-review** (revenue capabilities, memory, storage) — never auto-merges.
* **A — autonomous** (leaf capabilities, docs, helpers) — auto-merges on green only
  after N supervised green proposals.

Boundaries are injected so the policy is proven without spawning git/claude/pytest.
"""
from __future__ import annotations

import pytest

from utah import failures, selfcode
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _isolate_automerge_flag(monkeypatch, tmp_path):
    """Hermetic gate: ignore the ambient ~/.utah/run/selfcode.automerge flag
    (armed for live autonomy). Tests force the merge path via auto_merge=True."""
    monkeypatch.setattr(selfcode, "AUTOMERGE_FLAG", tmp_path / "ambient-automerge-off")


def _vcs():
    calls = {"branch": None, "discarded": False}

    def branch_fn(slug):
        calls["branch"] = f"selfcode/{slug}"
        return calls["branch"]

    def discard_fn():
        calls["discarded"] = True

    return calls, branch_fn, discard_fn


# --- classification (pure policy-as-data) -----------------------------------

def test_classify_safety_file_is_tier_D():
    assert selfcode.classify(["utah/config.py"]) == "D"
    assert selfcode.classify(["utah/brain.py"]) == "D"
    assert selfcode.classify(["utah/selfcode.py"]) == "D"
    assert selfcode.classify(["utah/daemon/peercred.py"]) == "D"


def test_classify_spine_is_tier_C():
    assert selfcode.classify(["utah/daemon/server.py"]) == "C"
    assert selfcode.classify(["migrations/003_thing.sql"]) == "C"


def test_classify_product_and_memory_is_tier_B():
    assert selfcode.classify(["utah/product/leads.py"]) == "B"
    assert selfcode.classify(["utah/memory.py"]) == "B"


def test_classify_leaf_is_tier_A():
    assert selfcode.classify(["utah/knowledge/douglas.py"]) == "A"
    assert selfcode.classify([]) == "A"


def test_classify_returns_strictest_across_files():
    assert selfcode.classify(["utah/knowledge/douglas.py", "utah/config.py"]) == "D"
    assert selfcode.classify(["utah/product/leads.py", "utah/daemon/server.py"]) == "C"


def test_tier_A_autonomy_needs_N_supervised():
    n = selfcode.POLICY["A"]["min_supervised"]
    assert selfcode.tier_allows_automerge("A", n) is True
    assert selfcode.tier_allows_automerge("A", n - 1) is False


def test_tiers_B_C_D_never_automerge():
    assert selfcode.tier_allows_automerge("B", 999) is False
    assert selfcode.tier_allows_automerge("C", 999) is False
    assert selfcode.tier_allows_automerge("D", 999) is False


# --- off-limits byte-check (Tier-D enforcement) -----------------------------

def test_offlimits_safety_edit_is_refused_and_documented(monkeypatch, tmp_path):
    # A run that modifies a safety file is rolled back + documented, even GREEN.
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    r = selfcode.propose("sneak an edit into config", run_claude=lambda t: None,
                         run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df,
                         safety_intact_fn=lambda before: False)  # a safety file changed
    assert r["applied"] is False and r.get("merged") in (False, None)
    assert r["tier"] == "D"
    assert calls["discarded"] is True
    assert any("off_limits" in row[2] for row in store.rows)


def test_kill_switch_smoke_refuses_self_edit_to_safety(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    failures.set_store(FakeFailureStore())
    result = selfcode.kill_switch_smoke()
    assert result["refused"] is True and result["tier"] == "D"


def test_kill_switch_smoke_does_not_pollute_failure_feed(monkeypatch, tmp_path):
    """B14: the nightly smoke deliberately triggers a refusal — its synthetic
    off_limits outcome must go to SMOKE_LOG, never the production failures feed
    that drives the AUDIT panel (it once buried ~824 real signals)."""
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    monkeypatch.setattr(selfcode, "SMOKE_LOG", tmp_path / "smoke.jsonl")
    store = FakeFailureStore(); failures.set_store(store)
    result = selfcode.kill_switch_smoke()
    assert result["refused"] is True
    assert store.rows == []                      # nothing in the production feed
    assert (tmp_path / "smoke.jsonl").exists()   # but the smoke trail is durable
    assert "off_limits" in (tmp_path / "smoke.jsonl").read_text()


def test_real_offlimits_still_records_to_failure_feed(monkeypatch, tmp_path):
    """The B14 fix must NOT silence a REAL autonomous attempt to edit safety —
    only the synthetic smoke is routed away. A genuine off_limits run still
    records to the production feed (and pages)."""
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    store = FakeFailureStore(); failures.set_store(store)
    calls, bf, df = _vcs()
    selfcode.propose("a genuine attempt to edit a safety file", run_claude=lambda t: None,
                     run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df,
                     safety_intact_fn=lambda before: False)   # real run, smoke defaults False
    assert any(row[2] == "off_limits" for row in store.rows)


# --- tier-gated merge -------------------------------------------------------

def test_tier_A_with_enough_supervised_auto_merges(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    r = selfcode.propose("tweak a leaf helper", run_claude=lambda t: None,
                         run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df,
                         auto_merge=True, tree_clean_fn=lambda: True,
                         changed_files_fn=lambda: ["utah/knowledge/douglas.py"],
                         supervised_fn=lambda: selfcode.POLICY["A"]["min_supervised"],
                         merge_fn=lambda b, t: ("sha999", True))
    assert r["tier"] == "A" and r["merged"] is True and r["commit"] == "sha999"


def test_tier_B_change_stays_proposal_even_with_automerge(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    merge_called = []
    r = selfcode.propose("touch a revenue capability", run_claude=lambda t: None,
                         run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df,
                         auto_merge=True, tree_clean_fn=lambda: True,
                         changed_files_fn=lambda: ["utah/product/leads.py"],
                         supervised_fn=lambda: 999, bump_supervised_fn=lambda: None,
                         merge_fn=lambda b, t: merge_called.append(1) or ("x", True))
    assert r["tier"] == "B" and r["applied"] is True and r["merged"] is False
    assert merge_called == []   # batch-review never auto-merges


def test_tier_A_insufficient_supervised_stays_proposal_and_counts(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    bumped = []
    r = selfcode.propose("tiny leaf tweak", run_claude=lambda t: None,
                         run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df,
                         auto_merge=True, tree_clean_fn=lambda: True,
                         changed_files_fn=lambda: ["utah/knowledge/douglas.py"],
                         supervised_fn=lambda: 0,
                         bump_supervised_fn=lambda: bumped.append(1),
                         merge_fn=lambda b, t: ("x", True))
    assert r["tier"] == "A" and r["merged"] is False and r["applied"] is True
    assert bumped == [1]   # a supervised green proposal counts toward Tier-A autonomy


def test_propose_threads_repo_into_branch_and_discard(monkeypatch, tmp_path):
    """Repo isolation: propose(repo=X) must branch/discard in X, not the process
    cwd (regression for the live-tree checkout bug caught by the SICA proof)."""
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    seen: dict[str, str] = {}

    def fake_branch(slug, *, repo="."):
        seen["branch_repo"] = repo
        return f"selfcode/{slug}"

    def fake_discard(*, repo="."):
        seen["discard_repo"] = repo

    monkeypatch.setattr(selfcode, "_real_branch", fake_branch)
    monkeypatch.setattr(selfcode, "_real_discard", fake_discard)

    # green run → branch uses repo, no discard
    selfcode.propose("x", run_claude=lambda t: None, run_tests=lambda: (True, "1 passed"),
                     repo="/tmp/wtA", safety_intact_fn=lambda b: True, auto_merge=False)
    assert seen["branch_repo"] == "/tmp/wtA"

    # red run → discard uses repo (rollback in the isolated repo, not cwd)
    selfcode.propose("y", run_claude=lambda t: None, run_tests=lambda: (False, "1 failed"),
                     repo="/tmp/wtB", safety_intact_fn=lambda b: True, auto_merge=False)
    assert seen["discard_repo"] == "/tmp/wtB"
