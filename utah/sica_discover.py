"""Browser discovery pass — browse → write finding pages → queue fix ideas.

Mirrors Ace's ``browse_optimize_loop`` research + dashboard stages inside Utah's
self-opt lane ONLY (never revenue/GTM). Every ``sica_autonomy`` cycle runs this
FIRST:

  1. **frontend** — headless Chrome renders the live command deck; brain writes a
     grounded brief (``~/.utah/findings/<ts>-frontend.md``).
  2. **research** — search + Chrome render of the best result for the dominant
     recurring failure; brain writes a brief (``…-research.md``).

Each brief ends with ``TASK: …`` — the self-coder consumes the oldest unused task
before falling back to domain rotation. Findings are real files (not ephemeral prompts).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from utah import selfcode
from utah.daemon import runtime

log = logging.getLogger("utah.sica_discover")

FINDINGS_DIR = Path(os.environ.get("UTAH_FINDINGS_DIR", str(runtime.UTAH_HOME / "findings")))
DISCOVERIES_LOG = runtime.RUN_DIR / "discoveries.jsonl"
USED_PATH = runtime.RUN_DIR / "discoveries-used.json"
#: Edge-trigger sentinel: a filed repair touches this so the EDGE-TRIGGERED selfcode job
#: (launchd WatchPaths) fires one cycle when there's real work — no 24/7 KeepAlive loop
#: (B12b). Only ``file_task`` touches it (the cycle's own discovery does not), so the job
#: never self-retriggers.
TRIGGER_PATH = runtime.RUN_DIR / "selfcode.trigger"


BROWSER_DOMAINS = ("frontend", "research")


def _touch_trigger() -> None:
    """Bump the edge-trigger sentinel so a filed repair wakes the self-coder. Best-effort."""
    try:
        TRIGGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER_PATH.write_text(str(time.time()))
    except OSError:  # the queue record already landed; the trigger is a nicety
        pass

_BRIEF_ASK = (
    "You are Utah's browser discovery pass (self-optimization ONLY — not marketing, "
    "not trading, not leads outreach).\n"
    "Given LIVE signals from headless Chrome below, write a short markdown brief:\n\n"
    "## Observed\n"
    "(what the browser actually showed — deck panels/markers, or researched technique text)\n\n"
    "## Suggested fix\n"
    "(one concrete, safe code change; touch as few files as possible; never edit the "
    "safety core: selfcode.py, config.py, brain.py, peercred.py, lifecycle.py, governor.py)\n\n"
    "End with exactly one line:\n"
    "TASK: <single sentence task for the self-coder>\n\n"
    "Signals:\n{signals}"
)
_TASK_RE = re.compile(r"^TASK:\s*(.+)\s*$", re.I | re.M)


def _parse_task(brief: str) -> str:
    m = _TASK_RE.search(brief or "")
    return (m.group(1).strip() if m else "")


def _record_key(rec: dict) -> str:
    return f"{rec.get('ts')}:{rec.get('domain')}"


def _load_used(used_path: Path | None = None) -> set[str]:
    path = used_path or USED_PATH
    try:
        data = json.loads(path.read_text())
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError, TypeError):  # absent/corrupt → nothing marked used
        return set()


def _save_used(used: set[str], used_path: Path | None = None) -> bool:
    """Persist the used-set. Best-effort: a write failure is logged, never raised —
    the worst case is a finding retried next cycle, not a crashed caller."""
    path = used_path or USED_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(used)))
        return True
    except OSError as exc:
        log.warning("used-set write failed (%s): %s", path, exc)
        return False


def _append_record(rec: dict, log_path: Path | None = None) -> None:
    path = log_path or DISCOVERIES_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec) + "\n")


def write_finding(domain: str, signals: str, *, brain_fn, findings_dir=None,
                  log_path=None) -> dict | None:
    """Signals → brain brief → markdown page + discoveries log row. Returns the
    record, or ``None`` when there is nothing real to record (empty/non-string
    brief, unwritable findings dir) — an honest no-finding, never a raise."""
    brief = brain_fn(_BRIEF_ASK.format(signals=signals))
    brief = brief.strip() if isinstance(brief, str) else ""
    if not brief:
        return None
    task = _parse_task(brief)
    ts = time.time()
    fdir = Path(findings_dir) if findings_dir else FINDINGS_DIR
    page = fdir / f"{int(ts)}-{domain}.md"
    rec = {
        "ts": ts,
        "domain": domain,
        "brief_path": str(page),
        "suggested_task": task,
        "chars": len(brief),
    }
    try:
        fdir.mkdir(parents=True, exist_ok=True)
        page.write_text(brief, encoding="utf-8")
        _append_record(rec, log_path=log_path)
    except OSError as exc:
        # The queue record is the source of truth — if it can't land, there IS
        # no finding to act on; report none rather than a half-written one.
        log.warning("discover: could not persist %s finding: %s", domain, exc)
        return None
    log.info("discover: wrote %s (%d chars, task=%r)", page.name, len(brief), task[:60])
    return rec


def harvest_failure_findings(*, recent_fn=None, log_path: Path | None = None,
                             used_path: Path | None = None, min_count: int = 3,
                             window: int = 60) -> dict:
    """Self-healing bridge: RECURRING live failures become pending self-code findings —
    the system that complains about its own line now files the fix task (Michael
    2026-06-10: "the voice and the coding agent don't use each other to fix itself").

    Reads the failure feed, groups by source/kind, and appends a ``selfheal`` finding
    (with a concrete suggested task) for every failure recurring ≥ *min_count* times —
    deduped against findings already pending or used, so the queue never spams. The
    console's /findings refreshes this on every view; /do n (or the autonomous loop)
    runs the fix. Never raises."""
    import time as _time

    from utah import failures as _failures

    out = {"scanned": 0, "harvested": 0, "skipped": 0}
    try:
        rows = (recent_fn or _failures.recent)(window)
    except Exception as exc:  # noqa: BLE001 — feed down: nothing to harvest
        out["error"] = str(exc)
        return out
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        out["scanned"] += 1
        key = (str(getattr(r, "source", "?")), str(getattr(r, "kind", "?")))
        counts[key] = counts.get(key, 0) + 1
    already = {rec.get("failure") for rec in list_findings(log_path=log_path)
               if rec.get("domain") == "selfheal"}
    for (source, kind), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n < min_count:
            continue
        tag = f"{source}/{kind}"
        if tag in already:
            out["skipped"] += 1
            continue
        rec = {
            "ts": _time.time(),
            "domain": "selfheal",
            "brief_path": "",
            "suggested_task": (
                f"Self-heal: the live failure {tag} recurred {n}x in the last {window} "
                f"failure rows. Find the root cause in the {source} lane (read the "
                f"failure details via utah.failures.recent and the {source} module), "
                f"fix it properly with a test that pins the regression, and keep the "
                f"fix honest — never silence the failure without fixing the cause."),
            "chars": 0,
            "failure": tag,
            "count": n,
        }
        try:
            _append_record(rec, log_path=log_path)
        except OSError as exc:
            # The queue itself is broken — report it once and stop; later rows
            # would hit the same wall ("never raises" is this function's contract).
            out["error"] = f"findings queue unwritable: {exc}"
            log.warning("selfheal harvest aborted: %s", out["error"])
            return out
        out["harvested"] += 1
        log.info("selfheal finding: %s x%d -> pending task", tag, n)
    return out


def run_discover(*, brain_fn, domains=BROWSER_DOMAINS, gather_fn=None,
                 findings_dir=None, log_path=None) -> dict:
    """Run browser discovery for each domain. Never raises."""
    if not selfcode.enabled():
        return {"ran": False, "reason": "kill switch"}
    from utah import sica_goals

    gather = gather_fn or sica_goals.gather_signals
    brain = brain_fn
    written: list[dict] = []
    for domain in domains:
        try:
            signals = gather(domain)
            rec = write_finding(domain, signals, brain_fn=brain,
                                findings_dir=findings_dir, log_path=log_path)
            if rec:
                written.append(rec)
        except Exception as exc:  # noqa: BLE001 — one domain must not abort the other
            log.warning("discover %s failed: %s", domain, exc)
    return {"ran": True, "findings": written, "count": len(written)}


def list_findings(*, log_path: Path | None = None) -> list[dict]:
    """Every recorded finding, oldest first. Corrupt lines are skipped and an
    unreadable log degrades to ``[]`` — the console/queue readers never crash
    on a damaged journal."""
    path = log_path or DISCOVERIES_LOG
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.warning("discoveries log unreadable (%s): %s", path, exc)
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def pending_findings(*, log_path: Path | None = None, used_path: Path | None = None
                     ) -> list[tuple[str, str, dict]]:
    """ALL unused findings carrying a suggested task → [(domain, task, record), …], oldest
    first. The SELF-CODE console lists these so Michael can action a browser-agent idea."""
    used = _load_used(used_path)
    out: list[tuple[str, str, dict]] = []
    for rec in list_findings(log_path=log_path):
        if rec.get("suggested_task") and _record_key(rec) not in used:
            out.append((rec["domain"], rec["suggested_task"], rec))
    return out


def next_pending_task(*, log_path: Path | None = None, used_path: Path | None = None
                      ) -> tuple[str, str, dict] | None:
    """Oldest unused finding with a non-empty suggested task → (domain, task, record)."""
    used = _load_used(used_path)
    for rec in list_findings(log_path=log_path):
        if not rec.get("suggested_task"):
            continue
        if _record_key(rec) in used:
            continue
        return rec["domain"], rec["suggested_task"], rec
    return None


def mark_used(rec: dict, *, used_path: Path | None = None) -> bool:
    """Mark a finding consumed so the queue never re-serves it. Never raises
    (the autonomous cycle calls this AFTER the work landed — a bookkeeping
    failure must not crash the cycle); returns whether the mark persisted."""
    used = _load_used(used_path)
    used.add(_record_key(rec))
    return _save_used(used, used_path)


def file_task(domain: str, task: str, *, log_path: Path | None = None,
              used_path: Path | None = None) -> dict:
    """File a PRE-MADE self-code task as a pending finding (no brain/browser). The SICA
    loop's :func:`next_pending_task` picks it up, codes it under the suite gate, and lands
    it via durable propagation — so any subsystem can queue a concrete fix for Ace to make
    himself. Deduped: an identical task already pending (filed, not yet used) is NOT
    re-filed, so a subsystem that stays broken queues ONE repair, not one per sweep.
    Returns ``{filed: bool, rec|reason}``."""
    task = (task or "").strip()
    if not task:
        return {"filed": False, "reason": "empty task"}
    used = _load_used(used_path)
    for rec in list_findings(log_path=log_path):
        if rec.get("suggested_task") == task and _record_key(rec) not in used:
            return {"filed": False, "reason": "already pending", "rec": rec}
    rec = {"ts": time.time(), "domain": domain, "brief_path": "",
           "suggested_task": task, "source": "filed"}
    try:
        _append_record(rec, log_path=log_path)
    except OSError as exc:
        # Never-raises boundary: revenue_heal/selfaudit call this mid-sweep — a
        # broken queue dir must come back as an honest refusal, not crash the cron.
        log.warning("file_task: queue write failed: %s", exc)
        return {"filed": False, "reason": f"queue write failed: {exc}"}
    if log_path is None:        # real (not a test fixture) → edge-trigger a selfcode cycle
        _touch_trigger()
    log.info("file_task: queued self-code task (%s): %r", domain, task[:70])
    return {"filed": True, "rec": rec}


__all__ = [
    "BROWSER_DOMAINS",
    "DISCOVERIES_LOG",
    "FINDINGS_DIR",
    "harvest_failure_findings",
    "USED_PATH",
    "file_task",
    "list_findings",
    "mark_used",
    "next_pending_task",
    "pending_findings",
    "run_discover",
    "write_finding",
]
