"""Mail-status capability — answer "how many emails/texts did we send" from the LIVE
``mail_ledger``, so the chat/voice brain stops saying "I can't reach the ledger" for a
send count (2026-06-13: it gave that runaround a dozen turns in a row).

Mirrors :mod:`utah.product.leads_status`: every number is a real ``COUNT(*)`` from
``mail_ledger`` (the table ``record_mail`` writes), or an honest "I can't reach it" — it
NEVER guesses. The brain has no ledger access; routing a send-count question here is what
makes Ace actually *know* the number instead of describing the code that writes it.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.product.mail_status")


def counts() -> dict:
    """Live send counts from ``mail_ledger``: today's rows by channel+status, the
    first/last send today, the all-time total, and the last few active days. Real
    ``COUNT(*)`` only — no estimates."""
    import psycopg

    from utah import config

    with psycopg.connect(config.DB_DSN, autocommit=True) as cx:
        today = cx.execute(
            "SELECT channel, status, count(*) FROM mail_ledger "
            "WHERE date(ts) = current_date GROUP BY channel, status ORDER BY 3 DESC").fetchall()
        total = cx.execute("SELECT count(*) FROM mail_ledger").fetchone()[0]
        span = cx.execute(
            "SELECT min(ts)::time, max(ts)::time FROM mail_ledger "
            "WHERE date(ts) = current_date AND status = 'sent'").fetchone()
        recent = cx.execute(
            "SELECT to_char(date(ts), 'Mon DD'), count(*) FROM mail_ledger "
            "WHERE status = 'sent' GROUP BY date(ts) ORDER BY date(ts) DESC LIMIT 5").fetchall()
    return {"today": [(c, s, n) for c, s, n in today], "total": total,
            "first": span[0] if span and span[0] else None,
            "last": span[1] if span and span[1] else None,
            "recent": [(d, n) for d, n in recent]}


def answer(text: str, *, counts_fn=None) -> str:
    """A grounded, speakable reply to a send-count question. Pulls real counts; on any
    ledger error it says so plainly rather than inventing a number (the whole point)."""
    try:
        c = (counts_fn or counts)()
    except Exception as exc:  # noqa: BLE001 — a dead ledger must NEVER become a fabricated count
        log.warning("mail_status: ledger unreachable: %s", exc)
        return ("I can't reach the mail ledger right now, so I won't guess at the number — "
                "ask again once Postgres is back.")

    today = c.get("today", [])
    sent = sum(n for _ch, st, n in today if st == "sent")
    bounced = sum(n for _ch, st, n in today if st == "bounced")
    parts = [f"Sent today: {sent:,} email" + ("" if sent == 1 else "s") + "."]
    if c.get("first") and c.get("last"):
        parts.append(f"First at {c['first']}, last at {c['last']}.")
    if bounced:
        parts.append(f"{bounced:,} bounced.")
    if not today:
        parts.append("Nothing sent yet today.")
    recent = "; ".join(f"{d}: {n:,}" for d, n in c.get("recent", [])[:3])
    if recent:
        parts.append(f"Recent daily sends — {recent}.")
    return " ".join(parts)


__all__ = ["counts", "answer"]
