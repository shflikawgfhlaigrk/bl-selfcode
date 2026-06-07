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

import logging
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
            discard_fn=None, merge_fn=None, auto_merge=None, repo: str | None = None) -> dict:
    """Propose a change for *task* on an isolated branch, gated by the suite. On a green
    suite, auto-merges to main + pushes when the auto-merge flag is set (``auto_merge``
    overrides for tests); otherwise the change stays a branch proposal. Returns
    ``{task, applied, tests_passed, merged, branch, reason, ...}``. Never raises."""
    if not enabled():
        failures.record("selfcode", "disabled", f"kill switch present; skipped: {task[:80]}")
        return {"task": task, "applied": False, "disabled": True,
                "tests_passed": False, "branch": None, "reason": "kill switch"}

    run_claude = run_claude or (lambda t: _real_claude(t, cwd=repo or "."))
    run_tests = run_tests or (lambda: _real_tests(cwd=repo or "."))
    branch_fn = branch_fn or _real_branch
    discard_fn = discard_fn or _real_discard

    branch = branch_fn(_slug(task))   # isolated; _real_branch refuses main
    try:
        run_claude(task)
    except Exception as exc:  # noqa: BLE001 — coding run failed (timeout/nonzero/etc.)
        failures.record("selfcode", "run_failed", f"{task[:80]}: {exc}")
        _safe(discard_fn)
        return {"task": task, "applied": False, "tests_passed": False,
                "branch": branch, "reason": str(exc)}

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

    do_merge = automerge_enabled() if auto_merge is None else auto_merge
    if do_merge:
        merge = merge_fn or (lambda b, t: _real_merge(b, t, repo=repo or "."))
        try:
            sha, pushed = merge(branch, task)
        except Exception as exc:  # noqa: BLE001 — merge/push failed; the branch survives
            failures.record("selfcode", "merge_failed", f"{task[:80]}: {exc}")
            return {"task": task, "applied": True, "tests_passed": True, "merged": False,
                    "branch": branch, "reason": f"suite green; merge failed: {exc}"}
        log.info("selfcode: %s — suite GREEN, merged to main %s (pushed=%s)", task[:60], sha, pushed)
        return {"task": task, "applied": True, "tests_passed": True, "merged": True,
                "commit": sha, "pushed": pushed, "branch": branch,
                "reason": "suite green, merged to main" + ("" if pushed else " (push rejected — local only)")}

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


__all__ = ["enabled", "automerge_enabled", "propose", "KILL_SWITCH", "AUTOMERGE_FLAG"]
