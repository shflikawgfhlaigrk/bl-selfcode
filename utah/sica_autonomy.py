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

#: git subprocesses MUST start in a readable, non-TCC dir. Under launchd the process
#: cwd was the TCC-protected Desktop, so git's startup getcwd() returned EPERM and EVERY
#: git call aborted with "Unable to read current working directory" before propagate even
#: evaluated the merge. ``git -C <repo>`` sets the operating dir; cwd just needs to be
#: getcwd-readable. ~/.utah is neither TCC-protected nor missing.
_SAFE_CWD = str(Path.home() / ".utah")

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
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                              text=True, cwd=_SAFE_CWD)

    if not (live / ".git").exists() or not (clone / ".git").exists():
        return {"propagated": False, "reason": "live or clone repo missing"}
    g(live, "fetch", str(clone), "main")
    before = g(live, "rev-parse", "HEAD").stdout.strip()
    target = g(live, "rev-parse", "FETCH_HEAD").stdout.strip()
    if before == target:
        return {"propagated": False, "reason": "already up to date"}
    if g(live, "merge-base", "--is-ancestor", before, "FETCH_HEAD").returncode != 0:
        return {"propagated": False, "reason": "not a fast-forward: live has commits the clone lacks"}

    # DURABLE dirty-tree guard: a dirty live tree must not PERMANENTLY strand
    # self-improvement (the live tree is ~always dirty, which is why nothing ever
    # propagated). If the uncommitted dev work touches NONE of the files the
    # autonomous commits change, stash-guard it (stash -u -> ff -> pop) so the
    # autonomous work lands AND dev work is preserved. Only a real OVERLAP skips.
    dirty = (set(g(live, "diff", "--name-only", "HEAD").stdout.split())
             | set(g(live, "ls-files", "--others", "--exclude-standard").stdout.split()))
    stashed = False
    if dirty:
        auto_files = set(g(live, "diff", "--name-only", before, "FETCH_HEAD").stdout.split())
        overlap = dirty & auto_files
        if overlap:
            return {"propagated": False,
                    "reason": f"live tree dirty — overlaps autonomous files "
                              f"({', '.join(sorted(overlap))[:80]}); skipped (safe)"}
        st = g(live, "stash", "push", "-u", "-m", "utah-propagate-guard")
        stashed = st.returncode == 0 and "No local changes" not in st.stdout
        if not stashed:
            return {"propagated": False, "reason": "live tree dirty — stash failed; skipped (safe)"}

    res = g(live, "merge", "--ff-only", "FETCH_HEAD")
    after = g(live, "rev-parse", "HEAD").stdout.strip()
    pop_conflict = False
    if stashed:
        pop = g(live, "stash", "pop")
        pop_conflict = pop.returncode != 0   # no overlap => should be clean; surface if not
    if res.returncode != 0:
        return {"propagated": False, "reason": f"not a fast-forward: {res.stderr.strip()[:120]}"}
    if before == after:
        return {"propagated": False, "reason": "already up to date"}
    log.info("propagated autonomous work to live main: %s -> %s", before[:8], after[:8])
    out = {"propagated": True, "from": before[:8], "to": after[:8]}
    if pop_conflict:
        out["dev_work"] = "preserved in stash@{0} — pop conflicted, resolve manually"
    return out


def sync_repo(repo: Path) -> bool:
    """Ready the dedicated repo for a cycle WITHOUT losing autonomous work. Force
    back to a clean main (dropping any in-progress attempt), then fast-forward to
    origin/main ONLY when the clone is strictly behind — never reset away local
    autonomous commits (that's what lets self-improvement COMPOUND). Returns False
    if it isn't a git repo (provision it once with a clone)."""
    if not (repo / ".git").exists():
        return False

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                              text=True, cwd=_SAFE_CWD)

    git("fetch", "origin", "main")
    git("checkout", "-f", "main")          # force back to main, drop in-progress attempt
    git("clean", "-fd")                     # remove untracked leftovers
    if git("rev-parse", "--verify", "origin/main").returncode == 0:
        ahead = (git("rev-list", "--count", "origin/main..main").stdout.strip() or "0")
        behind = (git("rev-list", "--count", "main..origin/main").stdout.strip() or "0")
        if ahead == "0" and behind != "0":
            git("reset", "--hard", "origin/main")   # purely behind → safe to pull dev
        elif ahead != "0" and behind != "0":
            # DIVERGED (clone has autonomous commits AND live advanced with dev work):
            # rebase the autonomous commits onto current live main so the clone never
            # permanently forks — ff propagate to live then works and self-improvement
            # keeps compounding. A conflict aborts cleanly and retries next cycle.
            if git("rebase", "origin/main").returncode != 0:
                git("rebase", "--abort")
        # ahead-only → already a strict descendant of live; ff propagate applies as-is.
    # prune stale selfcode/* branches so they don't accumulate
    for b in git("branch", "--list", "selfcode/*").stdout.split():
        if b and b != "*":
            git("branch", "-D", b)
    return True


#: Defer a self-code cycle when the machine is already loaded. A `claude` coding run is by
#: far the heaviest thing Ace spawns; launching it into a load storm (often one a previous
#: selfcode caused) is exactly what produced the live 900s claude timeouts and the correlated
#: Postgres flaps. Skip and retry next interval rather than burn a 15-minute run that can't
#: make progress — so self-coding stays RELIABLE, the precondition for "Ace fixes himself".
SELFCODE_MAX_LOAD_PER_CORE = float(os.environ.get("UTAH_SELFCODE_MAX_LOAD", "2.0"))


def _load_per_core() -> float:
    return os.getloadavg()[0] / (os.cpu_count() or 1)


def run_cycle(*, repo=None, brain_fn=None, propose_fn=None, sync_fn=None, task_fn=None,
              propagate_fn=None, verify_fn=None, foundation_gate=None, discover_fn=None,
              load_fn=None) -> dict:
    """One autonomous improvement cycle. Boundaries injected for unit-proof.

    The task is chosen by the domain-rotating goal source (sica_goals): each cycle
    targets baseline / leads / autonomy in turn, grounded in live signals — so the
    loop optimizes the real product, not archive look-alikes. ``task_fn`` overrides
    the generator (tests / a fixed goal)."""
    if not selfcode.enabled():
        return {"ran": False, "reason": "kill switch"}
    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("selfcode")
    if skip:
        return skip
    lpc = (load_fn or _load_per_core)()
    if lpc > SELFCODE_MAX_LOAD_PER_CORE:
        return {"ran": False, "reason": f"load {lpc:.2f}/core > {SELFCODE_MAX_LOAD_PER_CORE:.1f} "
                                        f"— deferring self-code (avoid claude timeout/PG flap)"}
    repo = Path(repo) if repo else REPO_DIR
    if not (sync_fn or sync_repo)(repo):
        return {"ran": False, "reason": f"dedicated repo not ready: {repo} "
                                        f"(provision with: git clone <origin> {repo})"}
    arch = sica.Archive()
    brain = brain_fn or _brain_answer
    from utah import sica_discover

    discover_out = (discover_fn or (lambda: sica_discover.run_discover(brain_fn=brain)))()
    pending_rec: dict | None = None
    if task_fn is not None:
        domain, task = "injected", task_fn()
    else:
        pending = sica_discover.next_pending_task()
        if pending:
            domain, task, pending_rec = pending
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
    if pending_rec is not None:
        sica_discover.mark_used(pending_rec)
    out = {"ran": True, "discover": discover_out, "domain": domain, "task": task,
           "steps": res.steps, "attempts": res.attempts, "best_after": res.best_after}
    if pending_rec:
        out["from_finding"] = pending_rec.get("brief_path")
    if any(a.get("merged") for a in res.attempts):
        out["propagation"] = (propagate_fn or propagate)(clone=repo)
        # Closed loop: after a FRONTEND change lands, Ace re-renders its OWN live deck through
        # headless Chrome and records what actually came back — verifying the change in the real
        # UI instead of guessing it worked. Other domains don't touch the browser.
        if domain == "frontend":
            from utah import sica_goals
            out["frontend_verify"] = (verify_fn or sica_goals.observe_deck)()
    _log_cycle(out)
    log.info("sica cycle: domain=%s task=%r steps=%d propagation=%s",
             domain, task[:60], res.steps, out.get("propagation"))
    return out


def _log_cycle(d: dict) -> None:
    """Cycle telemetry → Postgres (selfcode_log), best-effort. Was a JSONL file; now
    on PG like everything else (no-op in tests via the in-memory archive backend)."""
    sica.record_cycle({"ts": time.time(), **d})


def main() -> int:
    import sys

    logging.basicConfig(level=logging.INFO)
    if "--loop" in sys.argv:
        interval = max(60, int(os.environ.get("UTAH_SELFCODE_INTERVAL", "900")))
        while True:
            print(json.dumps(run_cycle()), flush=True)
            time.sleep(interval)
    print(json.dumps(run_cycle()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
