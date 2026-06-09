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
BROWSER_DOMAINS = ("frontend", "research")

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
    except Exception:  # noqa: BLE001
        return set()


def _save_used(used: set[str], used_path: Path | None = None) -> None:
    path = used_path or USED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(used)))


def _append_record(rec: dict, log_path: Path | None = None) -> None:
    path = log_path or DISCOVERIES_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec) + "\n")


def write_finding(domain: str, signals: str, *, brain_fn, findings_dir=None,
                  log_path=None) -> dict | None:
    """Signals → brain brief → markdown page + discoveries log row. Returns the record."""
    brief = (brain_fn(_BRIEF_ASK.format(signals=signals)) or "").strip()
    if not brief:
        return None
    task = _parse_task(brief)
    ts = time.time()
    fdir = Path(findings_dir) if findings_dir else FINDINGS_DIR
    fdir.mkdir(parents=True, exist_ok=True)
    page = fdir / f"{int(ts)}-{domain}.md"
    page.write_text(brief, encoding="utf-8")
    rec = {
        "ts": ts,
        "domain": domain,
        "brief_path": str(page),
        "suggested_task": task,
        "chars": len(brief),
    }
    _append_record(rec, log_path=log_path)
    log.info("discover: wrote %s (%d chars, task=%r)", page.name, len(brief), task[:60])
    return rec


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
    path = log_path or DISCOVERIES_LOG
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
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


def mark_used(rec: dict, *, used_path: Path | None = None) -> None:
    used = _load_used(used_path)
    used.add(_record_key(rec))
    _save_used(used, used_path)


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
    _append_record(rec, log_path=log_path)
    log.info("file_task: queued self-code task (%s): %r", domain, task[:70])
    return {"filed": True, "rec": rec}


__all__ = [
    "BROWSER_DOMAINS",
    "DISCOVERIES_LOG",
    "FINDINGS_DIR",
    "USED_PATH",
    "file_task",
    "list_findings",
    "mark_used",
    "next_pending_task",
    "pending_findings",
    "run_discover",
    "write_finding",
]
