"""Morning brief capability — "what happened / where things stand," from LIVE state.

Ace's brief transitions HERE as a capability behind the brain, not an agent. It composes a
brief from real Postgres state — the revenue ledger, memory size, recent leads, and the
open items on the failure log — never fabricated. Delivery is split by what's gated:
spoken aloud via Piper (ungated, works now) and/or emailed (GATED on Gmail creds, which is
Michael's business input; an attempted email with no creds documents the gate). compose is
pure; gather/speak/email are injectable.
"""
from __future__ import annotations

import logging

from utah import failures

log = logging.getLogger("utah.product.brief")


def compose_brief(*, ledger_counts: dict, memory_live: int,
                  failures_recent: list, leads_recent: list) -> str:
    """A grounded brief from live state. All numbers are real; nothing invented."""
    lc = ledger_counts or {}
    lines = ["UTAH MORNING BRIEF", ""]
    lines.append(
        f"Revenue ledger: {lc.get('leads', 0)} leads, {lc.get('probate', 0)} probate, "
        f"{lc.get('outreach_ledger', 0)} outreach queued, {lc.get('fires', 0)} engine fires."
    )
    lines.append(f"Memory: {memory_live} live facts grounding the brain.")
    if leads_recent:
        names = ", ".join((l.get("name") or "?") for l in leads_recent[:3])
        lines.append(f"Newest leads: {names}.")
    if failures_recent:
        items = "; ".join(f"{r.source}/{r.kind}" for r in failures_recent[:5])
        lines.append(f"Open items ({len(failures_recent)}): {items}.")
    else:
        lines.append("Open items: none.")
    return "\n".join(lines)


def gather() -> dict:
    """Pull live state for the brief from Postgres (real, off the live system)."""
    from utah import memory
    from utah.product.ledger import get_ledger

    lg = get_ledger()
    return {
        "ledger_counts": lg.counts(),
        "memory_live": memory.get_backend().live_counts().get("live", 0),
        "failures_recent": failures.recent(8),
        "leads_recent": lg.recent("leads", 3),
    }


def run(*, gather=gather, speak_fn=None, email_fn=None, can_email=False,
        push_fn=None) -> dict:
    """Compose the brief from live state, speak it (Piper, ungated), optionally email it
    (gated), and optionally PUSH it to the phone (``push_fn``, e.g. ``alerts.brief`` — the
    working delivery path while the Gmail app-password is broken). Returns
    ``{brief, spoke, emailed, email_gated, pushed}``. Never raises."""
    try:
        state = gather()
    except Exception as exc:  # noqa: BLE001
        failures.record("brief", "gather_failed", str(exc))
        return {"brief": "", "spoke": False, "emailed": False, "email_gated": False,
                "pushed": False}

    text = compose_brief(**state)

    spoke = False
    if speak_fn is not None:
        try:
            speak_fn(text.replace("\n", ". "))   # the spoken form
            spoke = True
        except Exception as exc:  # noqa: BLE001
            failures.record("brief", "speak_failed", str(exc))

    emailed = email_gated = False
    if can_email and email_fn is not None:
        try:
            emailed = bool(email_fn(text))
        except Exception as exc:  # noqa: BLE001
            failures.record("brief", "email_failed", str(exc))
    else:
        email_gated = True
        failures.record("brief", "email_gated",
                        "morning brief email gated: needs Gmail/sending creds (Michael's input)")

    pushed = False
    if push_fn is not None:
        try:
            pushed = bool((push_fn(text) or {}).get("sent"))
        except Exception as exc:  # noqa: BLE001
            failures.record("brief", "push_failed", str(exc))

    log.info("brief: spoke=%s emailed=%s gated=%s pushed=%s", spoke, emailed, email_gated, pushed)
    return {"brief": text, "spoke": spoke, "emailed": emailed,
            "email_gated": email_gated, "pushed": pushed}


__all__ = ["compose_brief", "gather", "run"]
