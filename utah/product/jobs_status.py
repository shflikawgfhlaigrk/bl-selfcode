"""Jobs / daily-responsibility capability — Ace's REAL standing duties.

Answers "what do you run / uphold every day", "list your jobs", "what are your
responsibilities" from the LIVE launchd roster + each job's actual run state. So Ace
can name every entity it must uphold from verified state — and still say "I don't know"
honestly if the roster can't be read. Never invents a job or a purpose it doesn't have.
"""
from __future__ import annotations

import subprocess

from utah.product import pipeline

#: Factual one-line purpose per standing job. A job NOT in this map still appears with
#: its live state — we never paint a purpose we don't actually know.
_PURPOSE = {
    "supervisor": "keep the daemon + deck alive",
    "postgres": "keep Postgres (:5433) up",
    "leads": "harvest fresh SMB leads",
    "probate": "scrape new probate filings",
    "enrich": "find emails/phones for leads",
    "outreach": "send gated, deliverable outreach",
    "marketer": "produce marketing posts",
    "mailcheck": "poll inbound mail",
    "replies": "ingest inbound replies",
    "brief": "send the morning brief",
    "proof": "re-verify the proof ledger",
    "canary": "health-sweep every job",
    "operator": "self-heal failures",
    "verify": "run the verify gate",
    "tailserve": "serve the deck to your phone",
    "engine-audit": "recompute trading edge nightly",
    "selfcode": "self-improve the codebase",
    "selfaudit": "audit itself for weaknesses",
    "consolidate": "consolidate memory",
    "codeindex": "index the codebase",
    "foundation": "prove permissions/secrets are in place",
    "wcfeed": "keep the market feed live",
}


def _launchctl_state() -> dict:
    """name -> 'ok' | 'exit N' | 'not loaded', read from live launchctl. Empty on failure
    (caller degrades honestly)."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    state = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.utah."):
            name = parts[2][len("com.utah."):]
            state[name] = "ok" if parts[1] in ("0", "-") else f"exit {parts[1]}"
    return state


def answer(text: str = "") -> str:
    """A grounded, asterisk-free readout of Ace's standing daily responsibilities."""
    try:
        roster = pipeline.scope().get("crons", [])
    except Exception:  # noqa: BLE001
        roster = []
    if not roster:
        return ("I don't know — I couldn't read my launchd roster just now, "
                "and I won't list duties I haven't verified.")
    state = _launchctl_state()
    running = sum(1 for j in roster if state.get(j) == "ok")
    lines = []
    for job in roster:
        st = state.get(job, "not loaded")
        purpose = _PURPOSE.get(job, "")
        lines.append(f"- {job}: {st}" + (f" — {purpose}" if purpose else ""))
    head = (f"I uphold {len(roster)} standing jobs every day; {running} are loaded and "
            f"healthy right now. This is my real launchd roster, checked live:")
    return head + "\n" + "\n".join(lines)
