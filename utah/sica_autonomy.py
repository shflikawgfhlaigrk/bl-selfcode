"""SICA autonomy (rung 4) — the unattended self-improvement cycle + entrypoint.

Ties the rungs into ONE governed cycle that runs with NO human in the loop:

  1. kill-switch check (``selfcode.enabled()``).
  2. the meta-agent picks the next task from the archive (``sica_loop`` + the brain).
  3. run it through ``selfcode.propose_governed`` in an ISOLATED dedicated repo
     (``UTAH_SELFCODE_REPO``, default ~/.utah/selfcode-repo) with the overseer as
     the coding runner and auto-merge ARMED — a Tier-A green change merges to that
     repo's main and pushes origin (after the supervised ramp); everything else
     stays a reviewed proposal. The dedicated repo keeps autonomous git ops OFF
     the live dev tree.
  4. the attempt is scored + archived (``sica``); the cycle is logged.

Scheduled by launchd ``com.utah.selfcode`` → it runs on its own. Run once:
``python -m utah.sica_autonomy``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from utah import config, selfcode, sica, sica_overseer
from utah.daemon import runtime
from utah.sica_loop import MetaLoop

log = logging.getLogger("utah.sica_autonomy")

#: Isolated clone the autonomous loop edits/merges/pushes — NEVER the live dev tree.
REPO_DIR = Path(os.environ.get("UTAH_SELFCODE_REPO", str(Path.home() / ".utah" / "selfcode-repo")))
CYCLE_LOG = runtime.RUN_DIR / "selfcode-cycle.log"

#: Fallback when the archive is empty / the brain returns nothing — deliberately
#: tiny and low-risk so an unattended cycle can never do harm by default.
DEFAULT_TASK = ("Improve one docstring OR add one small unit test for an existing "
                "leaf capability. Do not change behavior. Touch one file.")


def _brain_answer(prompt: str, timeout: int = 60) -> str:
    """Answer-only brain call (claude -p, NO tools) for meta task generation."""
    try:
        proc = subprocess.run([config.BRAIN_CMD, "-p", *config.BRAIN_NO_AGENT],
                              input=prompt, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("meta brain call failed: %s", exc)
        return ""


def sync_repo(repo: Path) -> bool:
    """Make the dedicated repo clean + on origin/main HEAD before a cycle. Returns
    False if it isn't a git repo (caller should provision it once)."""
    if not (repo / ".git").exists():
        return False

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    git("fetch", "origin", "main")
    git("checkout", "main")
    # Prefer origin/main if the remote is reachable; else just clean the local main.
    if git("rev-parse", "--verify", "origin/main").returncode == 0:
        git("reset", "--hard", "origin/main")
    git("clean", "-fd")
    return True


def run_cycle(*, repo=None, brain_fn=None, propose_fn=None, sync_fn=None) -> dict:
    """One autonomous improvement cycle. Boundaries injected for unit-proof."""
    if not selfcode.enabled():
        return {"ran": False, "reason": "kill switch"}
    repo = Path(repo) if repo else REPO_DIR
    if not (sync_fn or sync_repo)(repo):
        return {"ran": False, "reason": f"dedicated repo not ready: {repo} "
                                        f"(provision with: git clone <origin> {repo})"}
    arch = sica.Archive()
    default_propose = (lambda t: selfcode.propose_governed(
        t, repo=str(repo), auto_merge=True,
        run_claude=lambda task: sica_overseer.run_claude_supervised(task, cwd=str(repo))))
    loop = MetaLoop(archive=arch, max_steps=1, propose_fn=propose_fn or default_propose)
    task = loop.next_task_from_archive(brain_fn or _brain_answer) or DEFAULT_TASK
    res = loop.run([task])
    out = {"ran": True, "task": task, "steps": res.steps,
           "attempts": res.attempts, "best_after": res.best_after}
    _log_cycle(out)
    log.info("sica cycle: task=%r steps=%d attempts=%s", task[:60], res.steps, res.attempts)
    return out


def _log_cycle(d: dict) -> None:
    try:
        CYCLE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CYCLE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **d}) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("cycle log failed: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_cycle()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
