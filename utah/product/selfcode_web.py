"""Self-code page data — the four sections of the deck's SELF-CODE tab.

Surfaces what the autonomous self-coder is actually doing, all from REAL sources
(never fabricated), each section pure + I/O-isolated behind an injectable boundary
so the logic is fully unit-proven without a live DB or git tree:

1. **finding on google** — web-grounded facts (``research`` panel; memory rows).
2. **correcting in himself** — recent ``selfcode_log`` cycles (Postgres): task,
   domain, utility, passed, merged.
3. **ask-for-edit** — a real governed :func:`selfcode.propose_governed` run on the
   isolated clone, ``auto_merge=False`` (PROPOSE-ONLY — a web-triggered edit never
   auto-merges to main). Slow (~minutes), so the web layer runs it off the loop and
   the page polls; here we expose the single blocking call + result shaper.
4. **percentage goals** — progress bars derived from REAL counts: autonomous merges
   (git ``selfcode(auto)`` commits), the gate pass-rate of recent cycles, and the
   self-code knowledge corpus — every % traces to a real count.

The DB read and git counts are injected (``query_fn``/``git_count_fn``) so the unit
tests run against fakes; the production defaults hit the real Postgres + the real
isolated clone.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("utah.product.selfcode_web")

#: The isolated clone the autonomous loop (and chat-triggered edits) operate on —
#: NEVER the live dev tree (matches utah.sica_autonomy.REPO_DIR).
REPO_DIR = Path(os.environ.get("UTAH_SELFCODE_REPO", str(Path.home() / ".utah" / "selfcode-repo")))
#: The real Utah repo autonomous work propagates into (matches sica_autonomy.LIVE_REPO).
LIVE_REPO = Path(os.environ.get("UTAH_LIVE_REPO", str(Path(__file__).resolve().parents[2])))
#: git must start in a getcwd-readable, non-TCC dir (same fix as sica_autonomy._SAFE_CWD).
_SAFE_CWD = str(Path.home() / ".utah")

#: The prefix the self-coder stamps on every autonomous merge commit (greppable
#: provenance — see selfcode._real_merge). The merge-count goal is keyed off it.
AUTO_COMMIT_GREP = "selfcode(auto)"

# --- section 2: "correcting in himself" — recent self-code cycles ------------

def _default_query(sql: str, params: tuple = ()) -> list[tuple]:
    """Default DB boundary: one read of the real Postgres (autocommit, short timeout)."""
    import psycopg

    from utah import config

    with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=8) as c:
        return c.execute(sql, params).fetchall()


def _cycle_summary(data: dict) -> dict:
    """One self-code cycle → the flat row the page renders: task, domain, the best
    attempt's utility, and whether it passed the gate / merged. Real fields only."""
    attempts = data.get("attempts") or []
    # The representative attempt = the highest-utility one (the cycle's best result).
    best = max(attempts, key=lambda a: a.get("utility", 0.0)) if attempts else {}
    passed = any(bool(a.get("passed")) for a in attempts)
    merged = any(bool(a.get("merged")) for a in attempts)
    prop = data.get("propagation") or {}
    return {
        "task": (data.get("task") or "").strip(),
        "domain": data.get("domain") or "",
        "utility": best.get("utility"),
        "best_after": data.get("best_after"),
        "passed": passed,
        "merged": merged,
        "attempts": len(attempts),
        "propagated": bool(prop.get("propagated")) if prop else None,
        "ts": data.get("ts"),
    }


def recent_cycles(limit: int = 25, *, query_fn=None) -> list[dict]:
    """Recent ``selfcode_log`` cycles, newest first, flattened for the page.
    Real-or-empty: an unreachable DB / empty table yields ``[]`` (never fabricated)."""
    limit = max(1, min(int(limit), 200))
    q = query_fn or _default_query
    try:
        rows = q("SELECT id, ts, data FROM selfcode_log ORDER BY id DESC LIMIT %s", (limit,))
    except Exception as exc:  # noqa: BLE001 — honest empty on any read failure
        log.warning("selfcode_log read failed: %s", exc)
        return []
    out: list[dict] = []
    for rid, ts, data in rows:
        d = data if isinstance(data, dict) else json.loads(data)
        s = _cycle_summary(d)
        s["id"] = rid
        s["logged_at"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        out.append(s)
    return out


def cycle_stats(*, query_fn=None) -> dict:
    """Aggregate counts over ALL cycles/attempts (the goal denominators). Real-or-zero."""
    q = query_fn or _default_query
    try:
        (cycles,) = q("SELECT count(*) FROM selfcode_log")[0]
        passed, merged, total = q(
            "SELECT count(*) FILTER (WHERE (a->>'passed')::bool), "
            "count(*) FILTER (WHERE (a->>'merged')::bool), count(*) "
            "FROM selfcode_log, jsonb_array_elements(data->'attempts') a")[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("selfcode cycle stats failed: %s", exc)
        return {"cycles": 0, "attempts": 0, "passed": 0, "merged": 0}
    return {"cycles": int(cycles or 0), "attempts": int(total or 0),
            "passed": int(passed or 0), "merged": int(merged or 0)}


# --- section 4: percentage goals from real PRs/commits + cycle outcomes -------

def _git_count(repo: Path, grep: str) -> int:
    """Count commits in *repo* whose subject matches *grep* (the autonomous-merge
    provenance). git -C + a safe cwd so a TCC-protected process cwd can't abort it."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline", f"--grep={grep}"],
            capture_output=True, text=True, cwd=_SAFE_CWD, timeout=15)
        if out.returncode != 0:
            return 0
        return sum(1 for ln in out.stdout.splitlines() if ln.strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("git count failed (%s): %s", repo, exc)
        return 0


def _pct(num: float, den: float) -> int:
    if not den:
        return 0
    return max(0, min(100, round(100.0 * num / den)))


#: Real targets (denominators) for each goal — kept modest so the bar reflects genuine
#: progress against a concrete milestone, not a moving denominator.
GOAL_TARGETS = {
    "autonomous_merges": int(os.environ.get("UTAH_GOAL_MERGES", "25")),
    "knowledge_corpus": int(os.environ.get("UTAH_GOAL_KNOWLEDGE", "200")),
}


def goals(*, query_fn=None, git_count_fn=None) -> list[dict]:
    """Percentage goals wired to REAL counts. Every ``pct`` traces to a git commit
    count or a ``selfcode_log`` row count — nothing is invented.

    * **autonomous merges** — ``selfcode(auto)`` commits across the live repo + the
      isolated clone, vs a target. Proves what he's actually merged.
    * **gate pass-rate** — attempts that passed the full suite ÷ all attempts.
    * **autonomous merge-rate** — attempts merged ÷ all attempts (how often a green
      attempt cleared the tier policy).
    * **self-code knowledge** — cycles logged vs a target corpus (breadth of self-work).
    """
    gc = git_count_fn or (lambda repo: _git_count(repo, AUTO_COMMIT_GREP))
    stats = cycle_stats(query_fn=query_fn)
    live_merges = gc(LIVE_REPO)
    clone_merges = gc(REPO_DIR)
    total_merges = live_merges + clone_merges
    tgt_m = GOAL_TARGETS["autonomous_merges"]
    tgt_k = GOAL_TARGETS["knowledge_corpus"]
    return [
        {"name": "Autonomous merges",
         "pct": _pct(total_merges, tgt_m),
         "num": total_merges, "den": tgt_m,
         "detail": f"{total_merges} selfcode(auto) commits "
                   f"({live_merges} live + {clone_merges} clone) of {tgt_m} target"},
        {"name": "Gate pass-rate",
         "pct": _pct(stats["passed"], stats["attempts"]),
         "num": stats["passed"], "den": stats["attempts"],
         "detail": f"{stats['passed']} of {stats['attempts']} attempts passed the full suite"},
        {"name": "Autonomous merge-rate",
         "pct": _pct(stats["merged"], stats["attempts"]),
         "num": stats["merged"], "den": stats["attempts"],
         "detail": f"{stats['merged']} of {stats['attempts']} attempts merged "
                   f"(green + cleared tier policy)"},
        {"name": "Self-code knowledge",
         "pct": _pct(stats["cycles"], tgt_k),
         "num": stats["cycles"], "den": tgt_k,
         "detail": f"{stats['cycles']} self-code cycles logged of {tgt_k} target"},
    ]


# --- section 3: chat-box "ask for an edit, he does it" -----------------------

def shape_result(res: dict) -> dict:
    """Trim a ``propose_governed`` dict to the page-facing fields (proposed/ran, task,
    utility, passed, branch, diff/reason). Never exposes raw test output verbatim."""
    return {
        "ran": True,
        "task": res.get("task", ""),
        "applied": bool(res.get("applied")),
        "passed": bool(res.get("tests_passed")),
        "merged": bool(res.get("merged")),   # always False on the web lane (auto_merge=False)
        "branch": res.get("branch"),
        "tier": res.get("tier"),
        "utility": res.get("utility"),
        "elapsed_s": res.get("elapsed_s"),
        "reason": (res.get("reason") or "")[:400],
        "diff": (res.get("diff") or "")[:6000],
    }


def _branch_diff(repo: Path, branch: str | None) -> str:
    """Best-effort: the diff the proposal produced on its branch vs main (so the page
    can SHOW what he changed). Empty on any failure — diff is a bonus, not the gate."""
    if not branch:
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", f"main...{branch}"],
            capture_output=True, text=True, cwd=_SAFE_CWD, timeout=15)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def run_edit(task: str, *, repo=None, propose_fn=None, diff_fn=None,
             run_claude=None) -> dict:
    """Run ONE real governed self-code attempt for a web-typed edit request.

    PROPOSE-ONLY: ``auto_merge=False`` — a web-triggered edit is NEVER auto-merged to
    main; it lands as a reviewable branch on the isolated clone. Returns the shaped
    result (+ branch diff where available). The blocking call is real (a full claude
    coding cycle + the gate), so the WEB layer must invoke this OFF the event loop and
    have the page poll — this function itself is the synchronous unit of work."""
    task = (task or "").strip()
    if not task:
        return {"ran": False, "error": "empty edit request"}
    repo = Path(repo) if repo else REPO_DIR
    from utah import selfcode

    if not selfcode.enabled():
        return {"ran": False, "error": "kill switch — self-coding disabled"}

    def _default_propose(t: str) -> dict:
        kw: dict = {}
        if run_claude is not None:
            kw["run_claude"] = run_claude
        else:
            # Real coding runner under the overseer (bounded/cancellable), exactly like
            # the autonomous lane (sica_autonomy.run_cycle) but PROPOSE-ONLY.
            from utah import sica_overseer
            kw["run_claude"] = lambda task: sica_overseer.run_claude_supervised(task, cwd=str(repo))
        return selfcode.propose_governed(t, repo=str(repo), auto_merge=False, **kw)

    pf = propose_fn or _default_propose
    try:
        res = pf(task)
    except Exception as exc:  # noqa: BLE001 — never let a coding failure escape as a 500
        log.warning("web self-code edit failed: %s", exc)
        return {"ran": False, "error": str(exc)[:300], "task": task}
    shaped = shape_result(res)
    if not shaped["diff"]:
        shaped["diff"] = (diff_fn or (lambda b: _branch_diff(repo, b)))(shaped["branch"])[:6000]
    return shaped


__all__ = ["recent_cycles", "cycle_stats", "goals", "run_edit", "shape_result",
           "REPO_DIR", "LIVE_REPO", "GOAL_TARGETS", "AUTO_COMMIT_GREP"]
