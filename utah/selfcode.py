"""Self-coding capability — propose a code change, gated by the test suite.

Ace's agent_007 mechanism (Claude CLI in a sandbox branch → regression check → commit on
pass / ``git reset --hard`` on fail) transitions HERE as a capability behind the brain,
not an autonomous agent. Hard rules, all enforced:

* **Isolated branch.** Work happens on an isolated ``selfcode/<slug>`` branch, never
  edited directly on main.
* **Kill switch.** Touch ``~/.utah/run/selfcode.disabled`` to disable instantly.
* **The test suite is the gate.** Only a FULL green suite keeps a change; a red suite is
  rolled back (``git reset --hard`` + ``clean``) and the failing output documented. A
  Claude/test crash is likewise documented and rolled back.
* **Auto-merge (opt-in, reversible).** With ``~/.utah/run/selfcode.automerge`` present, a
  green proposal is committed, merged ``--no-ff`` to main, and pushed to origin — autonomy
  level "auto-merge on green" (Michael, 2026-06-07). Without the flag it stays a branch
  proposal for human review. Remove the flag to revert to propose-only. The kill switch
  overrides both. There is no unattended task-picking loop: each run is an explicit task.

The heavy boundaries (Claude CLI with tools, pytest, git) are injected so the gate logic
is unit-proven; the defaults are the real subprocesses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
import subprocess
import sys

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.selfcode")

KILL_SWITCH = runtime.RUN_DIR / "selfcode.disabled"
#: Opt-in auto-merge: when present, a green proposal merges to main + pushes origin.
#: Absent → propose-only (human merges). Reversible: just remove the file.
AUTOMERGE_FLAG = runtime.RUN_DIR / "selfcode.automerge"
#: Bound a single coding run. Kept tight: the live proof showed an unbounded Claude+Bash
#: run can hang (it ran `uv` and timed out at 600s), so the gate must cut it off.
CODE_TIMEOUT_S = 300

#: Count of supervised green proposals (human-reviewed) — gates Tier-A autonomy.
SUPERVISED_STATE = runtime.RUN_DIR / "selfcode.supervised"

# --- Doc-13 tiered self-coding (policy-as-data) -----------------------------
#: Off-limits files (Tier D): the self-coder itself, auth, the singleton/hard-exit,
#: the admission governor, doctrine config, and the no-fabrication brain. A coding
#: run that changes ANY of these bytes is rolled back — the self-coder may never
#: edit its own safety. (Byte-checked around every run; see :func:`safety_intact`.)
SAFETY_PATHS: tuple[str, ...] = (
    "utah/selfcode.py",
    "utah/config.py",
    "utah/brain.py",
    "utah/daemon/peercred.py",
    "utah/daemon/lifecycle.py",
    "utah/daemon/governor.py",
)

#: The tier of a change = the STRICTEST tier among the files it touches; the tier
#: sets the autonomy ceiling. Pure data so the policy is the spec, not buried logic.
POLICY: dict[str, dict] = {
    "D": {"label": "off-limits", "automerge": False, "paths": SAFETY_PATHS},
    "C": {"label": "5-rung review", "automerge": False,
          "paths": ("utah/daemon/", "migrations/")},          # the spine + schema
    "B": {"label": "batch-review", "automerge": False,
          "paths": ("utah/product/", "utah/memory.py", "utah/store/")},  # money + memory
    "A": {"label": "autonomous", "automerge": True, "min_supervised": 3,
          "paths": ("",)},                                    # leaf caps / docs / helpers
}
#: Strictest → loosest. ``classify`` returns the first (strictest) tier that matches.
TIER_ORDER: tuple[str, ...] = ("D", "C", "B", "A")


def _tier_of(path: str) -> str:
    p = path.strip().lstrip("./")
    for tier in TIER_ORDER:                       # D, C, B, A — strictest first
        for pref in POLICY[tier]["paths"]:
            if pref and (p == pref or p.startswith(pref)):
                return tier
    return "A"                                    # nothing matched → leaf (A fallback)


def classify(paths) -> str:
    """The strictest tier among *paths* (doc-13 policy-as-data). Empty → ``'A'``."""
    worst = "A"
    for path in paths:
        t = _tier_of(path)
        if TIER_ORDER.index(t) < TIER_ORDER.index(worst):
            worst = t
    return worst


def tier_allows_automerge(tier: str, supervised: int) -> bool:
    """True iff *tier* may auto-merge on green. Only Tier A, and only after its
    ``min_supervised`` count of human-reviewed green proposals."""
    pol = POLICY.get(tier, {})
    if not pol.get("automerge"):
        return False
    return supervised >= pol.get("min_supervised", 0)


def safety_snapshot(repo: str = ".") -> dict[str, str]:
    """sha256 of each existing safety file — the before-image for the byte-check."""
    out: dict[str, str] = {}
    for rel in SAFETY_PATHS:
        p = pathlib.Path(repo) / rel
        if p.exists():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def safety_intact(repo: str, before: dict[str, str]) -> bool:
    """True iff every safety file is byte-identical to *before* (none added/removed).
    A mismatch means the coding run touched off-limits code → refuse + roll back."""
    return safety_snapshot(repo) == before


def _read_supervised() -> int:
    try:
        return int(json.loads(SUPERVISED_STATE.read_text()).get("count", 0))
    except Exception:  # noqa: BLE001 — absent/corrupt → start at zero
        return 0


def _bump_supervised() -> None:
    try:
        SUPERVISED_STATE.write_text(json.dumps({"count": _read_supervised() + 1}))
    except Exception as exc:  # noqa: BLE001
        log.warning("selfcode supervised bump failed: %s", exc)


def _real_changed_files(repo: str = ".") -> list[str]:
    """Files the coding run produced (tracked diff vs HEAD + new untracked). Reliable
    on the auto-merge path because a clean tree is enforced before the run."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout
    tracked = git("diff", "--name-only", "HEAD").split()
    untracked = git("ls-files", "--others", "--exclude-standard").split()
    return [*tracked, *untracked]


def enabled() -> bool:
    """False when the kill-switch flag is present."""
    return not KILL_SWITCH.exists()


def automerge_enabled() -> bool:
    """True when the opt-in auto-merge flag is present (kill switch still overrides)."""
    return AUTOMERGE_FLAG.exists()


def _slug(task: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (task or "change").lower()).strip("-")[:40] or "change"


# --- real boundaries (defaults) ---------------------------------------------

def _real_claude(task: str, *, cwd: str, timeout: int = CODE_TIMEOUT_S) -> None:
    """Run Claude CLI WITH coding tools in *cwd* to perform *task*. (Tools are enabled
    here — unlike the grounded brain — because this IS the coding harness.)"""
    # Prompt via STDIN, never as a positional arg: --allowedTools is variadic and would
    # otherwise swallow a trailing prompt, leaving claude -p to hang on empty stdin until
    # the timeout (the live proof's 600s/300s hangs). With the prompt on stdin there is no
    # positional to swallow, so flag order can't break it. (Proven: pipe-in returns in ~2s.)
    proc = subprocess.run(
        [config.BRAIN_CMD, "-p", "--allowedTools", "Edit", "Write", "Read", "Bash"],
        input=task, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude coding run exited {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '')[-300:]}")


def _real_tests(*, cwd: str, timeout: int = CODE_TIMEOUT_S) -> tuple[bool, str]:
    # Use THIS interpreter (the ~/.utah venv) — bare "python" isn't on PATH (the live
    # proof failed with FileNotFoundError: 'python').
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def propose(task: str, *, run_claude=None, run_tests=None, branch_fn=None,
            discard_fn=None, merge_fn=None, auto_merge=None, tree_clean_fn=None,
            repo: str | None = None, safety_snapshot_fn=None, safety_intact_fn=None,
            changed_files_fn=None, supervised_fn=None, bump_supervised_fn=None) -> dict:
    """Propose a change for *task* on an isolated branch, gated by the suite AND the
    doc-13 tier policy. The change's tier (the strictest among the files it touches)
    sets the autonomy ceiling: Tier-D (safety) is rolled back via a byte-check; only a
    Tier-A change auto-merges, and only after ``min_supervised`` green proposals. B/C
    (and not-yet-autonomous A) stay branch proposals for review. Returns
    ``{task, applied, tests_passed, merged, branch, tier, reason, ...}``. Never raises."""
    if not enabled():
        failures.record("selfcode", "disabled", f"kill switch present; skipped: {task[:80]}")
        return {"task": task, "applied": False, "disabled": True,
                "tests_passed": False, "branch": None, "reason": "kill switch"}

    do_merge = automerge_enabled() if auto_merge is None else auto_merge
    # On the auto-merge path, REFUSE a dirty tree: branching + `git add -A` would sweep
    # unrelated/concurrent uncommitted work into the autonomous commit and push it to main
    # (this happened live 2026-06-07 — a concurrent propose() swept an editor's change).
    if do_merge:
        is_clean = tree_clean_fn if tree_clean_fn is not None else (lambda: _tree_clean(repo or "."))
        if not is_clean():
            failures.record("selfcode", "tree_dirty",
                            f"auto-merge refused — dirty tree, would sweep uncommitted work: {task[:60]}")
            return {"task": task, "applied": False, "tests_passed": False, "merged": False,
                    "branch": None,
                    "reason": "working tree dirty — auto-merge refused (would sweep uncommitted work)"}

    run_claude = run_claude or (lambda t: _real_claude(t, cwd=repo or "."))
    run_tests = run_tests or (lambda: _real_tests(cwd=repo or "."))
    branch_fn = branch_fn or _real_branch
    discard_fn = discard_fn or _real_discard

    # Byte-check before-image of the off-limits (Tier-D) files. Captured around the run
    # so it is robust to an already-dirty tree (unlike a diff-vs-HEAD).
    before = None
    if safety_intact_fn is None:
        before = (safety_snapshot_fn or (lambda: safety_snapshot(repo or ".")))()

    branch = branch_fn(_slug(task))   # isolated; _real_branch refuses main
    try:
        run_claude(task)
    except Exception as exc:  # noqa: BLE001 — coding run failed (timeout/nonzero/etc.)
        failures.record("selfcode", "run_failed", f"{task[:80]}: {exc}")
        _safe(discard_fn)
        return {"task": task, "applied": False, "tests_passed": False,
                "branch": branch, "reason": str(exc)}

    # Tier-D off-limits enforcement: a run that edited a safety file is rolled back,
    # no matter how green the suite. The self-coder can never edit its own safety.
    intact = (safety_intact_fn(before) if safety_intact_fn is not None
              else safety_intact(repo or ".", before))
    if not intact:
        failures.record("selfcode", "off_limits",
                        f"{task[:80]}: edited a Tier-D safety file — rolled back")
        _safe(discard_fn)
        log.info("selfcode: %s — touched off-limits safety code, rolled back", task[:60])
        return {"task": task, "applied": False, "tests_passed": False, "merged": False,
                "branch": branch, "tier": "D",
                "reason": "off-limits — change touched a Tier-D safety file, rolled back"}

    try:
        passed, output = run_tests()
    except Exception as exc:  # noqa: BLE001
        failures.record("selfcode", "test_run_failed", f"{task[:80]}: {exc}")
        _safe(discard_fn)
        return {"task": task, "applied": False, "tests_passed": False,
                "branch": branch, "reason": str(exc)}

    if not passed:
        tail = output.strip()[-400:]
        failures.record("selfcode", "tests_failed", f"{task[:80]}: {tail}")
        _safe(discard_fn)
        log.info("selfcode: %s — suite RED, rolled back", task[:60])
        return {"task": task, "applied": False, "tests_passed": False,
                "branch": branch, "reason": tail}

    if do_merge:
        # Tree was clean pre-run, so changed-files == the run's output → tier is reliable.
        changed = (changed_files_fn or (lambda: _real_changed_files(repo or ".")))()
        tier = classify(changed)
        if tier == "D":   # belt-and-suspenders with the byte-check above
            failures.record("selfcode", "off_limits",
                            f"{task[:80]}: change set includes a Tier-D safety file — rolled back")
            _safe(discard_fn)
            return {"task": task, "applied": False, "tests_passed": False, "merged": False,
                    "branch": branch, "tier": "D",
                    "reason": "off-limits — change set includes a Tier-D safety file, rolled back"}
        supervised = (supervised_fn or _read_supervised)()
        if tier_allows_automerge(tier, supervised):
            merge = merge_fn or (lambda b, t: _real_merge(b, t, repo=repo or "."))
            try:
                sha, pushed = merge(branch, task)
            except Exception as exc:  # noqa: BLE001 — merge/push failed; the branch survives
                failures.record("selfcode", "merge_failed", f"{task[:80]}: {exc}")
                return {"task": task, "applied": True, "tests_passed": True, "merged": False,
                        "branch": branch, "tier": tier, "reason": f"suite green; merge failed: {exc}"}
            log.info("selfcode: %s — Tier-%s GREEN, merged to main %s (pushed=%s)",
                     task[:60], tier, sha, pushed)
            return {"task": task, "applied": True, "tests_passed": True, "merged": True,
                    "commit": sha, "pushed": pushed, "branch": branch, "tier": tier,
                    "reason": f"suite green, Tier-{tier} merged to main"
                              + ("" if pushed else " (push rejected — local only)")}
        # Tier B/C, or Tier-A still under supervision → stays a proposal for review.
        (bump_supervised_fn or _bump_supervised)()
        reason = (f"suite green; Tier-{tier} ({POLICY[tier]['label']}) — proposal kept for "
                  f"review, not auto-merged")
        log.info("selfcode: %s — Tier-%s GREEN, kept on %s (review)", task[:60], tier, branch)
        return {"task": task, "applied": True, "tests_passed": True, "merged": False,
                "branch": branch, "tier": tier, "reason": reason}

    log.info("selfcode: %s — suite GREEN, kept on %s (proposal, not merged)", task[:60], branch)
    return {"task": task, "applied": True, "tests_passed": True, "merged": False,
            "branch": branch, "reason": "suite green"}


def _safe(fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("selfcode discard failed: %s", exc)


def _real_branch(slug: str, *, repo: str = ".") -> str:
    name = f"selfcode/{slug}"
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    if cur in ("main", "master"):  # never code on the trunk
        subprocess.run(["git", "checkout", "-B", name], cwd=repo,
                       capture_output=True, text=True)
    return name


def _real_discard(*, repo: str = ".") -> None:
    # Roll back tracked edits AND remove untracked files. The live proof showed a timed-out
    # run leaves untracked artifacts (uv.lock) that `git reset --hard` alone won't clear.
    subprocess.run(["git", "reset", "--hard"], cwd=repo, capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo, capture_output=True, text=True)


def _tree_clean(repo: str = ".") -> bool:
    """True when the working tree has no uncommitted changes — the precondition for a safe
    auto-merge (so the autonomous commit captures ONLY what the coding run produced)."""
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    return out.returncode == 0 and not out.stdout.strip()


def _real_merge(branch: str, task: str, *, repo: str = ".") -> tuple[str, bool]:
    """Commit the green proposal, merge ``--no-ff`` into main, push origin. Returns
    ``(short_sha, pushed)``. ONLY called after the full suite is green. Provenance: the
    message is prefixed ``selfcode(auto):`` + a Claude co-author trailer, so autonomous
    commits are greppable and distinguishable from human ones. A rejected push (e.g. a
    concurrent commit advanced origin) is reported as ``pushed=False`` — the merge is
    local on main and recoverable, never silently lost."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
    msg = (f"selfcode(auto): {task[:68]}\n\n"
           "Autonomous self-code change — full test suite green before merge.\n\n"
           "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
    git("add", "-A")
    git("commit", "-m", msg)                  # no-op if Claude changed nothing
    git("checkout", "main")
    git("merge", "--no-ff", branch, "-m", f"selfcode(auto) merge: {task[:60]}")
    pushed = git("push", "origin", "main").returncode == 0
    git("branch", "-D", branch)
    return git("rev-parse", "--short", "HEAD").stdout.strip(), pushed


def kill_switch_smoke() -> dict:
    """Nightly safety proof (doc 13): the self-coder MUST refuse to edit its own safety.
    Drives a fully-green, clean-tree, auto-merge run whose coding step *did* modify a
    Tier-D file and asserts the result is rolled back and never merged. Returns
    ``{refused, tier, reason}`` — ``refused`` is the live invariant the verifier checks."""
    seen = {"discarded": False, "merged": False}
    r = propose(
        "SMOKE: attempt to edit a safety file (must be refused)",
        run_claude=lambda t: None,
        run_tests=lambda: (True, ""),
        branch_fn=lambda slug: f"selfcode/{slug}",
        discard_fn=lambda: seen.update(discarded=True),
        safety_intact_fn=lambda before: False,            # pretend a safety file changed
        auto_merge=True, tree_clean_fn=lambda: True,
        merge_fn=lambda b, t: seen.update(merged=True) or ("SHOULD-NOT-HAPPEN", True),
    )
    refused = (r.get("applied") is False and r.get("merged") in (False, None)
               and r.get("tier") == "D" and seen["discarded"] and not seen["merged"])
    return {"refused": refused, "tier": r.get("tier"), "reason": r.get("reason")}


__all__ = ["enabled", "automerge_enabled", "propose", "classify", "tier_allows_automerge",
           "safety_snapshot", "safety_intact", "kill_switch_smoke", "POLICY", "TIER_ORDER",
           "SAFETY_PATHS", "KILL_SWITCH", "AUTOMERGE_FLAG", "SUPERVISED_STATE"]
