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
#: The real Utah repo autonomous improvements propagate INTO (the ProjectUtah root).
LIVE_REPO = Path(os.environ.get("UTAH_LIVE_REPO", str(Path(__file__).resolve().parents[1])))
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


def propagate(live=None, clone=None) -> dict:
    """Flow autonomous improvements OUT of the sandbox clone INTO the real Utah:
    fast-forward the live repo's main to include the clone's autonomous commits.

    SAFE BY CONSTRUCTION: (1) refuses if the live working tree is DIRTY — never
    clobbers uncommitted dev work; (2) ff-ONLY — never a force/merge-commit, so it
    only applies when live's main is a strict ancestor of the clone's (no
    divergence). On a skip the autonomous commits stay in the clone (preserved by
    sync_repo) and propagate on a later clean cycle. Never raises."""
    live = Path(live) if live else LIVE_REPO
    clone = Path(clone) if clone else REPO_DIR

    def g(repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    if not (live / ".git").exists() or not (clone / ".git").exists():
        return {"propagated": False, "reason": "live or clone repo missing"}
    if g(live, "status", "--porcelain").stdout.strip():
        return {"propagated": False, "reason": "live tree dirty — skipped (safe; will retry when clean)"}
    g(live, "fetch", str(clone), "main")
    before = g(live, "rev-parse", "HEAD").stdout.strip()
    res = g(live, "merge", "--ff-only", "FETCH_HEAD")
    after = g(live, "rev-parse", "HEAD").stdout.strip()
    if res.returncode != 0:
        return {"propagated": False, "reason": f"not a fast-forward: {res.stderr.strip()[:120]}"}
    if before == after:
        return {"propagated": False, "reason": "already up to date"}
    log.info("propagated autonomous work to live main: %s -> %s", before[:8], after[:8])
    return {"propagated": True, "from": before[:8], "to": after[:8]}


def sync_repo(repo: Path) -> bool:
    """Ready the dedicated repo for a cycle WITHOUT losing autonomous work. Force
    back to a clean main (dropping any in-progress attempt), then fast-forward to
    origin/main ONLY when the clone is strictly behind — never reset away local
    autonomous commits (that's what lets self-improvement COMPOUND). Returns False
    if it isn't a git repo (provision it once with a clone)."""
    if not (repo / ".git").exists():
        return False

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    git("fetch", "origin", "main")
    git("checkout", "-f", "main")          # force back to main, drop in-progress attempt
    git("clean", "-fd")                     # remove untracked leftovers
    if git("rev-parse", "--verify", "origin/main").returncode == 0:
        ahead = (git("rev-list", "--count", "origin/main..main").stdout.strip() or "0")
        behind = (git("rev-list", "--count", "main..origin/main").stdout.strip() or "0")
        if ahead == "0" and behind != "0":
            git("reset", "--hard", "origin/main")   # purely behind → safe to pull dev
        # ahead/diverged → keep the autonomous main (compounding); dev sync via push/pull later
    # prune stale selfcode/* branches so they don't accumulate
    for b in git("branch", "--list", "selfcode/*").stdout.split():
        if b and b != "*":
            git("branch", "-D", b)
    return True


def run_cycle(*, repo=None, brain_fn=None, propose_fn=None, sync_fn=None, task_fn=None,
              propagate_fn=None) -> dict:
    """One autonomous improvement cycle. Boundaries injected for unit-proof.

    The task is chosen by the domain-rotating goal source (sica_goals): each cycle
    targets baseline / leads / autonomy in turn, grounded in live signals — so the
    loop optimizes the real product, not archive look-alikes. ``task_fn`` overrides
    the generator (tests / a fixed goal)."""
    if not selfcode.enabled():
        return {"ran": False, "reason": "kill switch"}
    repo = Path(repo) if repo else REPO_DIR
    if not (sync_fn or sync_repo)(repo):
        return {"ran": False, "reason": f"dedicated repo not ready: {repo} "
                                        f"(provision with: git clone <origin> {repo})"}
    arch = sica.Archive()
    brain = brain_fn or _brain_answer
    if task_fn is not None:
        domain, task = "injected", task_fn()
    else:
        from utah import sica_goals
        domain = sica_goals.pick_domain(sica_goals.next_cycle_index())
        task = sica_goals.next_task(domain, brain_fn=brain)
    task = (task or "").strip() or DEFAULT_TASK   # empty/whitespace → safe default
    default_propose = (lambda t: selfcode.propose_governed(
        t, repo=str(repo), auto_merge=True,
        run_claude=lambda task: sica_overseer.run_claude_supervised(task, cwd=str(repo))))
    loop = MetaLoop(archive=arch, max_steps=1, propose_fn=propose_fn or default_propose)
    res = loop.run([task])
    out = {"ran": True, "domain": domain, "task": task, "steps": res.steps,
           "attempts": res.attempts, "best_after": res.best_after}
    if any(a.get("merged") for a in res.attempts):
        out["propagation"] = (propagate_fn or propagate)(clone=repo)
    _log_cycle(out)
    log.info("sica cycle: domain=%s task=%r steps=%d propagation=%s",
             domain, task[:60], res.steps, out.get("propagation"))
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
