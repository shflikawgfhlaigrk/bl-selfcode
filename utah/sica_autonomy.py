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
#: Cap every git call so a hung NETWORK op (fetch/push) can't freeze the autonomous loop
#: forever — the same failure class as the unbounded afplay (a blocking call with no timeout
#: in a critical loop). On timeout the wrapper returns a FAILED CompletedProcess so callers
#: degrade (skip this cycle) instead of crashing. 120s is generous for this small repo.
GIT_TIMEOUT_S = float(os.environ.get("UTAH_GIT_TIMEOUT", "120"))


def _git_timed(args: list[str], **kw):
    """subprocess.run for git, bounded: returns a failed CompletedProcess on timeout."""
    try:
        return subprocess.run(args, timeout=GIT_TIMEOUT_S, **kw)
    except subprocess.TimeoutExpired:
        log.warning("git timed out after %ss: %s", GIT_TIMEOUT_S, " ".join(args[:4]))
        return subprocess.CompletedProcess(args, 124, "", f"git timed out after {GIT_TIMEOUT_S}s")

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


def _scrub_conflict_refs(repo) -> int:
    """Delete iCloud/Finder conflict-copy git refs (e.g. ``refs/heads/main 2``).

    The Desktop repo's ``.git`` is iCloud-synced, which spawns ``<ref> 2`` copies.
    Such a ref points at a missing object, so ``git fetch`` negotiation aborts with
    'bad object refs/heads/main 2 ... did not send all necessary objects' — which
    stalled selfcode propagation for hours (live 2026-06-13). The repo ``.gitignore``
    ``* 2`` rule cannot cover files INSIDE ``.git``. Returns the count removed; never
    raises (best-effort — a cleanup failure must not crash the cycle)."""
    removed = 0
    try:
        refs = Path(repo) / ".git" / "refs"
        if refs.is_dir():
            for p in refs.rglob("*"):
                if p.is_file() and " " in p.name:
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001 — cleanup must never crash the autonomy cycle
        pass
    return removed


def _default_live_verify(repo) -> bool:
    """Post-merge live-suite gate for transactional propagation — True iff pytest is
    green in *repo*. Used so an autonomous change that breaks the live suite is rolled
    back instead of landing."""
    from utah import selfcode
    ok, _ = selfcode._real_tests(cwd=str(repo))
    return ok


def propagate(live=None, clone=None, verify_fn=None) -> dict:
    """Flow autonomous improvements OUT of the sandbox clone INTO the real Utah:
    fast-forward the live repo's main to include the clone's autonomous commits.

    SAFE BY CONSTRUCTION: (1) refuses if the live working tree is DIRTY — never
    clobbers uncommitted dev work; (2) ff-ONLY — never a force/merge-commit, so it
    only applies when live's main is a strict ancestor of the clone's (no
    divergence); (3) NO-REGRESSION — when *verify_fn* is supplied (production passes
    the live suite), the merged tree is verified and AUTO-ROLLED-BACK on red, so an
    autonomous change can never leave the live suite broken. On a skip/rollback the
    autonomous commits stay in the clone (preserved by sync_repo) and re-try on a
    later clean cycle. Never raises."""
    live = Path(live) if live else LIVE_REPO
    clone = Path(clone) if clone else REPO_DIR

    def g(repo, *a):
        return _git_timed(["git", "-C", str(repo), *a], capture_output=True,
                          text=True, cwd=_SAFE_CWD)

    if not (live / ".git").exists() or not (clone / ".git").exists():
        return {"propagated": False, "reason": "live or clone repo missing"}
    # Scrub iCloud conflict-copy refs on BOTH sides before the fetch — a stray
    # 'main 2' on either repo aborts fetch negotiation and stalls propagation.
    _scrub_conflict_refs(live)
    _scrub_conflict_refs(clone)
    fetched = g(live, "fetch", str(clone), "main")
    if fetched.returncode != 0:
        # Honest diagnosis: a failed/timed-out fetch used to fall through and read as
        # "not a fast-forward" — a misleading reason that hid the real (network/empty
        # clone) failure from the cycle log.
        return {"propagated": False,
                "reason": f"fetch from clone failed: {(fetched.stderr or '').strip()[:120]}"}
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
    if res.returncode != 0:
        if stashed:
            g(live, "stash", "pop")          # merge didn't land — restore dev work as-is
        return {"propagated": False, "reason": f"not a fast-forward: {res.stderr.strip()[:120]}"}
    after = g(live, "rev-parse", "HEAD").stdout.strip()
    if before == after:
        if stashed:
            g(live, "stash", "pop")
        return {"propagated": False, "reason": "already up to date"}

    # NO-REGRESSION GUARANTEE: with a verifier supplied (production passes the live
    # suite), run it on the merged tree and AUTO-ROLLBACK to the prior state on red —
    # an autonomous change can NEVER leave the live suite broken. Verify BEFORE popping
    # the stash so the rollback (reset --hard) can't discard dev work. A verify crash is
    # fail-CLOSED (treated as red → rolled back).
    if verify_fn is not None:
        try:
            green = bool(verify_fn(str(live)))
        except Exception as exc:  # noqa: BLE001 — never trust a crashing verifier; roll back
            green = False
            log.warning("propagate verify crashed (treating as red): %s", exc)
        if not green:
            g(live, "reset", "--hard", before)   # roll back to the prior green state
            if stashed:
                g(live, "stash", "pop")           # restore dev work onto the rolled-back tree
            try:
                from utah import failures
                failures.record("selfcode", "propagate_rolled_back",
                                f"{before[:8]}->{after[:8]} live suite RED after merge — auto-reverted")
            except Exception:  # noqa: BLE001
                pass
            log.warning("propagate ROLLED BACK %s->%s: live suite red after merge",
                        before[:8], after[:8])
            return {"propagated": False, "rolled_back": True,
                    "reason": f"verify red after merge — rolled back to {before[:8]}"}

    pop_conflict = False
    if stashed:
        pop = g(live, "stash", "pop")
        if pop.returncode != 0:
            # B13: a conflicted stash pop used to leave the live tree littered with conflict
            # markers + a "resolve manually" note nobody reads — and once stranded real
            # uncommitted work. A conflicted pop does NOT drop the stash, so the dev work is
            # safe in stash@{0}. Recover the tree to a CLEAN state (the propagated commit) and
            # PAGE Michael, instead of leaving a half-merged tree the daemon then runs from.
            pop_conflict = True
            g(live, "reset", "--hard", "HEAD")   # clear conflict markers; stash@{0} preserved
            _alert_propagate_conflict()
    log.info("propagated autonomous work to live main: %s -> %s", before[:8], after[:8])
    out = {"propagated": True, "from": before[:8], "to": after[:8]}
    if pop_conflict:
        out["dev_work"] = ("preserved in stash@{0} (pop conflicted) — tree left CLEAN, no "
                           "conflict markers; recover with `git stash apply`. Michael paged.")
    return out


def _alert_propagate_conflict() -> None:
    """Page Michael when an autonomous propagate had to stash-recover dev work (B13).
    Best-effort — alerting must never crash the autonomy cycle."""
    try:
        from utah import alerts, failures

        msg = ("autonomous propagate stash-pop conflicted — your uncommitted dev work is "
               "safe in `git stash@{0}` (live tree left clean). Recover: `git stash apply`.")
        failures.record("selfcode", "propagate_conflict", msg)
        alerts.critical_async("selfcode", msg, key="selfcode/propagate_conflict")
    except Exception:  # noqa: BLE001
        log.warning("propagate-conflict alert failed", exc_info=True)


def sync_repo(repo: Path) -> bool:
    """Ready the dedicated repo for a cycle WITHOUT losing autonomous work. Force
    back to a clean main (dropping any in-progress attempt), then fast-forward to
    origin/main ONLY when the clone is strictly behind — never reset away local
    autonomous commits (that's what lets self-improvement COMPOUND). Returns False
    if it isn't a git repo (provision it once with a clone)."""
    if not (repo / ".git").exists():
        return False

    def git(*a):
        return _git_timed(["git", "-C", str(repo), *a], capture_output=True,
                          text=True, cwd=_SAFE_CWD)

    # origin is the live tree (file remote); a stray iCloud 'main 2' on either side
    # aborts the fetch — scrub both before pulling.
    _scrub_conflict_refs(repo)
    _scrub_conflict_refs(LIVE_REPO)
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
              load_fn=None, recent_fn=None) -> dict:
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
    from utah import sica_goals
    if task_fn is not None:
        domain, task = "injected", task_fn()
    else:
        pending = sica_discover.next_pending_task()
        # PRIORITY: a FILED repair (revenue_heal etc. — anything not the routine browser
        # polish) jumps the queue. But a frontend/research finding must NOT pre-empt the
        # revenue-weighted rotation, or the loop spends every cycle on live.html cosmetics
        # (audit 2026-06-08: 4/4 auto-commits were frontend, 0 revenue). It is consumed only
        # when the rotation itself lands on that domain.
        if pending and pending[0] not in ("frontend", "research"):
            domain, task, pending_rec = pending
        else:
            # Failure-rate weighted: steer the rotation away from categories whose last N
            # self-coding attempts all failed (sica_goals.select_domain), to pull the ~39%
            # archive failure rate down. Degrades to plain round-robin when no signal.
            domain = sica_goals.select_domain(sica_goals.next_cycle_index())
            if pending is not None and pending[0] == domain:
                domain, task, pending_rec = pending     # browser finding on its own turn
            elif domain == "autonomy":
                # CLOSE THE META-LOOP (audit #25): on the autonomy slot, evolve from the
                # best archived attempt (argmax utility) instead of a generic task — this is
                # what makes self-improvement COMPOUND rather than only rotate. Falls back to
                # the goal generator if the archive is empty or the meta-agent is silent.
                task = MetaLoop(archive=arch).next_task_from_archive(brain) \
                    or sica_goals.next_task(domain, brain_fn=brain)
            else:
                task = sica_goals.next_task(domain, brain_fn=brain)
    task = (task or "").strip() or DEFAULT_TASK   # empty/whitespace → safe default
    regenerated = False
    if task_fn is None:
        # DEGENERACY GATE (audit 2026-06-12: 9 merged cycles were the same trivial
        # `_probe_marker` test — a repeat scores ~max utility, so without this veto the
        # loop's argmax IS the spam). One regeneration with explicit rejection feedback;
        # if the brain is incorrigible, skip the cycle HONESTLY — a recorded no-op beats
        # a merged nothing. task_fn (operator/test override) is never vetoed.
        recent = (recent_fn or sica_goals.recent_tasks)()
        reason = sica_goals.is_degenerate(task, recent)
        if reason:
            task = sica_goals.next_task(domain, brain_fn=brain,
                                        rejected=task, reject_reason=reason)
            regenerated = True
            still = sica_goals.is_degenerate(task, recent) if task \
                else "brain silent on regeneration"
            if still:
                out = {"ran": False, "reason": f"degenerate task rejected: {still}",
                       "domain": domain, "task": task, "discover": discover_out}
                _log_cycle(out)
                log.info("sica cycle: degenerate task rejected (domain=%s): %s",
                         domain, still)
                return out
    default_propose = (lambda t: selfcode.propose_governed(
        t, repo=str(repo), auto_merge=True,
        run_claude=lambda task: sica_overseer.run_claude_supervised(task, cwd=str(repo))))
    loop = MetaLoop(archive=arch, max_steps=1, propose_fn=propose_fn or default_propose)
    res = loop.run([task])
    if pending_rec is not None:
        sica_discover.mark_used(pending_rec)
    out = {"ran": True, "discover": discover_out, "domain": domain, "task": task,
           "steps": res.steps, "attempts": res.attempts, "best_after": res.best_after}
    if regenerated:
        out["regenerated"] = True   # first proposal was vetoed as degenerate
    if pending_rec:
        out["from_finding"] = pending_rec.get("brief_path")
    if any(a.get("merged") for a in res.attempts):
        # Transactional: the live suite is re-verified after the merge and auto-rolled
        # back on red, so an autonomous change can never regress the live tree.
        out["propagation"] = (propagate_fn or propagate)(clone=repo,
                                                         verify_fn=_default_live_verify)
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
