"""Morning brief capability — "what happened / where things stand," from LIVE state.

Ace's brief transitions HERE as a capability behind the brain, not an agent. It composes a
brief from real Postgres state — the revenue ledger, memory size, recent leads, and the
open items on the failure log — never fabricated. Delivery is split by what's gated:
spoken aloud via Piper (ungated, works now) and/or emailed (GATED on Gmail creds, which is
Michael's business input; an attempted email with no creds documents the gate). compose is
pure; gather/speak/email are injectable.
"""
from __future__ import annotations

import datetime
import logging
import os

from utah import failures

log = logging.getLogger("utah.product.brief")


def _brief_recipient() -> str:
    """Who the brief is emailed to. Env override wins; otherwise Michael's own Gmail
    ``from`` address (he emails himself the brief). Empty string -> no email recipient."""
    env = os.environ.get("UTAH_BRIEF_EMAIL", "").strip()
    if env:
        return env
    try:
        import json

        from utah import mail
        return str(json.loads(mail.GMAIL_CREDS.read_text()).get("from", "")).strip()
    except Exception:  # noqa: BLE001 — no creds / unreadable -> no recipient (gated)
        return ""


def compose_brief(*, ledger_counts: dict, memory_live: int,
                  failures_recent: list, leads_recent: list,
                  probate_top: list | None = None) -> str:
    """A grounded brief from live state. All numbers are real; nothing invented."""
    lc = ledger_counts or {}
    lines = ["UTAH MORNING BRIEF", ""]
    lines.append(
        f"Revenue ledger: {lc.get('leads', 0)} leads, {lc.get('probate', 0)} probate, "
        f"{lc.get('outreach_ledger', 0)} outreach queued, {lc.get('fires', 0)} engine fires."
    )
    lines.append(f"Memory: {memory_live} live facts grounding the brain.")
    if probate_top:
        # The property intel Michael never received (2026-06-10): top resolved estates.
        lines.append("Real estate — top resolved probate properties (county ARV):")
        for p in probate_top[:5]:
            # 3-mile average assessed value, when the county layer resolved one
            area = f" · 3mi avg ${int(p['area_avg']):,}" if p.get("area_avg") else ""
            lines.append(f"  • {p.get('case_name')} ({(p.get('county') or '?').title()}) — "
                         f"${int(p.get('arv') or 0):,} — {p.get('address')}{area}")
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
        "probate_top": _probate_top(),
    }


def _probate_top(limit: int = 5) -> list[dict]:
    """Top RESOLVED probate properties by county ARV — best-effort, never blocks the brief."""
    try:
        import psycopg

        from utah import config
        with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT case_name, county, arv, heir_contact->>'address', "
                "       (heir_contact#>>'{area_avg_3mi,avg_value}')::numeric FROM probate "
                "WHERE arv IS NOT NULL AND heir_contact ? 'address' "
                "ORDER BY arv DESC LIMIT %s", (limit,)).fetchall()
        return [{"case_name": c, "county": co, "arv": int(a), "address": ad,
                 "area_avg": int(avg) if avg is not None else None}
                for c, co, a, ad, avg in rows]
    except Exception:  # noqa: BLE001 — store down: brief still ships without the section
        return []


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


def _email_brief(text: str, *, send_fn=None, ledger=None, to=None) -> bool:
    """Email the brief to Michael and record the send in ``mail_ledger`` (caller-side, the
    same pattern as outreach). The subject is DATE-STAMPED so the daily brief is never
    suppressed by the never-twice ``UNIQUE(recipient, subject)`` constraint. Never raises."""
    from utah import mail

    send = send_fn or mail.send
    to = to or _brief_recipient()
    if not to:
        return False
    subject = f"Utah morning brief — {datetime.date.today().isoformat()}"
    res = send(to, subject, text)
    if res.get("sent"):
        try:
            lg = ledger
            if lg is None:
                from utah.product.ledger import get_ledger
                lg = get_ledger()
            lg.record_mail(to, subject, status="sent", channel="email")
        except Exception as exc:  # noqa: BLE001 — ledger is observability, never the send
            log.debug("brief mail_ledger record skipped: %s", exc)
    return bool(res.get("sent"))


def deliver(*, push: bool = True, email: bool = True) -> dict:
    """The cron entrypoint: compose the brief from live state and deliver it both ways that
    work now — PUSH to the phone (Pushover) AND EMAIL to Michael (Gmail SMTP, recorded in
    mail_ledger). Email auto-gates if no creds are present; push is best-effort. Never raises."""
    from utah import alerts, mail

    push_fn = alerts.brief if push else None
    can_email = bool(email and mail.creds_available() and _brief_recipient())
    return run(push_fn=push_fn, can_email=can_email,
               email_fn=_email_brief if can_email else None)


__all__ = ["compose_brief", "gather", "run", "deliver"]
