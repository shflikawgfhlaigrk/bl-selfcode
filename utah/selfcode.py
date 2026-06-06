"""Self-coding capability — propose a code change, gated by the test suite.

Ace's agent_007 mechanism (Claude CLI in a sandbox branch → regression check → commit on
pass / ``git reset --hard`` on fail) transitions HERE as a capability behind the brain,
not an autonomous agent. Hard rules, all enforced:

* **Never main.** Work happens on an isolated ``selfcode/<slug>`` branch.
* **Kill switch.** Touch ``~/.utah/run/selfcode.disabled`` to disable instantly.
* **The test suite is the gate.** A green suite keeps the change (a proposal — never
  auto-merged to main); a red suite is rolled back and the failing output documented to
  the failure log. A Claude/test crash is likewise documented and rolled back.

The heavy boundaries (Claude CLI with tools, pytest, git) are injected so the gate logic
is unit-proven; the defaults are the real subprocesses.
"""
from __future__ import annotations

import logging
import re
import subprocess

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.selfcode")

KILL_SWITCH = runtime.RUN_DIR / "selfcode.disabled"
#: Bound a single coding run. Kept tight: the live proof showed an unbounded Claude+Bash
#: run can hang (it ran `uv` and timed out at 600s), so the gate must cut it off.
CODE_TIMEOUT_S = 300


def enabled() -> bool:
    """False when the kill-switch flag is present."""
    return not KILL_SWITCH.exists()


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


def propose(task: str, *, run_claude=None, run_tests=None,
            branch_fn=None, discard_fn=None, repo: str | None = None) -> dict:
    """Propose a change for *task* on an isolated branch, gated by the suite. Returns
    ``{task, applied, tests_passed, branch, reason, disabled?}``. Never raises."""
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

    log.info("selfcode: %s — suite GREEN, kept on %s (proposal, not merged)", task[:60], branch)
    return {"task": task, "applied": True, "tests_passed": True,
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


__all__ = ["enabled", "propose", "KILL_SWITCH"]
