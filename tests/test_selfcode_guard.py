"""The selfcode edit guard: every safety-core file is OFF LIMITS; a sandbox path is not.

This is the doc-13 guard stated as the single invariant the task names — proven against
the REAL guard (not a stub), per safety-core file:

    For each of the six Tier-D safety files, a coding run that edits it is REFUSED
    as "selfcode off limits" (rolled back, never merged, and documented as a
    source=``selfcode`` / kind=``off_limits`` failure), while the SAME guard ALLOWS a
    change confined to a sandbox (non-safety) path.

Each case populates a temp repo with every real Tier-D path, captures the byte-image with
the production :func:`selfcode.safety_snapshot`, has the coding step mutate the target's
bytes on disk, and lets the production :func:`selfcode.safety_intact` detect the tamper so
:func:`selfcode.propose` rolls it back. The allow-case edits only a sandbox file and is
kept — proving the guard discriminates rather than refusing unconditionally.

(:mod:`tests.test_selfcode_offlimits` proves adjacent properties — one-canonical-reason,
no-retry-loop, not-requeued. This file pins the headline guard contract on its own.)
"""
from __future__ import annotations

import pytest

from utah import failures, selfcode
from tests.fakes import FakeFailureStore

#: A repo-relative path under no safety prefix — the "sandbox" the guard must ALLOW.
SANDBOX_PATH = "utah/knowledge/douglas.py"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """Hermetic gate: never see the kill switch, and ignore the ambient
    ~/.utah/run/selfcode.automerge flag (it may be armed for live autonomy)."""
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "no-kill-switch")
    monkeypatch.setattr(selfcode, "AUTOMERGE_FLAG", tmp_path / "no-automerge")


def _vcs():
    """A fake branch/discard pair that records whether a rollback happened."""
    calls = {"branch": None, "discarded": False, "discards": 0}

    def branch_fn(slug):
        calls["branch"] = f"selfcode/{slug}"
        return calls["branch"]

    def discard_fn():
        calls["discarded"] = True
        calls["discards"] += 1

    return calls, branch_fn, discard_fn


def _seeded_repo(root):
    """A temp repo holding every real Tier-D path plus the sandbox path, each with
    sentinel content so any edit is a detectable byte change."""
    repo = root / "repo"
    for rel in (*selfcode.SAFETY_PATHS, SANDBOX_PATH):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\nSENTINEL = 1\n")
    return repo


def _propose_editing(repo, rel, *, store):
    """Run the REAL guard end-to-end: a fully-GREEN coding run that rewrites *rel*'s
    bytes in *repo*. Returns ``(result, calls)``."""
    failures.set_store(store)
    calls, bf, df = _vcs()
    r = selfcode.propose(
        f"edit {rel}",
        run_claude=lambda _t: (repo / rel).write_text("# TAMPERED\nSENTINEL = 666\n"),
        run_tests=lambda: (True, "all green"),   # even a green suite must not save a safety edit
        branch_fn=bf, discard_fn=df,
        repo=str(repo), auto_merge=False,
    )
    return r, calls


def test_safety_core_set_is_the_documented_paths():
    """Guard the guard: the parametrized cases below cover exactly SAFETY_PATHS, so a
    case exists for every off-limits file (and only those) if the set ever drifts."""
    assert set(selfcode.SAFETY_PATHS) == {
        "utah/selfcode.py",
        "utah/config.py",
        "utah/brain.py",
        "utah/daemon/peercred.py",
        "utah/daemon/lifecycle.py",
        "utah/daemon/governor.py",
        "tests/conftest.py",   # the shared autouse pollution guards (the gate's harness)
        # the deck surface — off-limits to selfcode (a bad UI edit silently breaks the deck)
        "utah/interface/web.py",
        "utah/interface/static/live.html",
        "utah/interface/static/terminal.html",
        "utah/interface/static/truth.html",
        "utah/interface/static/route.html",
    }


@pytest.mark.parametrize("rel", list(selfcode.SAFETY_PATHS))
def test_every_safety_core_file_is_refused_off_limits(tmp_path, rel):
    """Each safety-core file is OFF LIMITS: editing it is refused (not applied, Tier-D,
    never merged), rolled back, and documented as a selfcode / off_limits failure."""
    store = FakeFailureStore()
    r, calls = _propose_editing(_seeded_repo(tmp_path), rel, store=store)

    # Refused: not applied, classified strictest tier, never merged.
    assert r["applied"] is False
    assert r["tier"] == "D"
    assert r.get("merged") in (False, None)

    # The reason is a single clear line naming the off-limits guard.
    reason = r["reason"]
    assert isinstance(reason, str) and reason.strip() and "\n" not in reason
    assert "off-limits" in reason.lower()

    # Rolled back exactly once...
    assert calls["discarded"] is True and calls["discards"] == 1
    # ...and documented as "selfcode off limits" (source=selfcode, kind=off_limits).
    assert any(src == "selfcode" and kind == "off_limits"
               for _, src, kind, _ in store.rows)


def test_sandbox_path_is_allowed(tmp_path):
    """Control / the other half of the invariant: the SAME real byte-check ALLOWS a
    change confined to a sandbox (non-safety) path — kept, not Tier-D, not rolled back."""
    store = FakeFailureStore()
    r, calls = _propose_editing(_seeded_repo(tmp_path), SANDBOX_PATH, store=store)

    assert r["applied"] is True              # the safety byte-check did not fire
    assert r.get("tier") != "D"
    assert calls["discarded"] is False       # nothing rolled back
    # The allow-path records no off-limits failure.
    assert not any(kind == "off_limits" for _, _, kind, _ in store.rows)
