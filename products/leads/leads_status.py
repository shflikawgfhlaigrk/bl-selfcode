"""Leads-status capability — answer lead / pipeline / probate-count questions from the
LIVE Postgres ledger, so the chat/voice brain never fabricates counts again.

Regression this exists for: asked for the day's lead split, the brain invented "List A/B/C"
and served "500 + 0 + 0 = 721" — made-up numbers AND wrong arithmetic — because lead-count
questions routed to the free-generating brain, which has no ledger access. This is a
deterministic, grounded capability (the same tier as weather/brief): every number is a real
COUNT(*) from the ``leads``/``probate`` tables, or an honest "I can't reach the ledger" — it
NEVER guesses.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.product.leads_status")


def counts() -> dict:
    """Live counts from the ledger: total, per-source split, with-email, today's intake,
    the last few active days, and probate cases. Real ``COUNT(*)`` only — no estimates."""
    import psycopg

    from utah import config

    with psycopg.connect(config.DB_DSN, autocommit=True) as cx:
        total = cx.execute("SELECT count(*) FROM leads").fetchone()[0]
        by_source = cx.execute(
            "SELECT source, count(*) FROM leads GROUP BY source ORDER BY 2 DESC").fetchall()
        with_email = cx.execute(
            "SELECT count(*) FROM leads "
            "WHERE contact->>'email' IS NOT NULL AND contact->>'email' <> ''").fetchone()[0]
        today = cx.execute(
            "SELECT count(*) FROM leads WHERE date(ts) = current_date").fetchone()[0]
        recent = cx.execute(
            "SELECT to_char(date(ts), 'Mon DD'), count(*) FROM leads "
            "GROUP BY date(ts) ORDER BY date(ts) DESC LIMIT 5").fetchall()
        probate = cx.execute("SELECT count(*) FROM probate").fetchone()[0]
    return {"total": total, "by_source": [(s, n) for s, n in by_source],
            "with_email": with_email, "today": today,
            "recent": [(d, n) for d, n in recent], "probate": probate}


def answer(text: str, *, counts_fn=None) -> str:
    """A grounded, speakable reply to a lead/pipeline question. Pulls real counts; on any
    ledger error it says so plainly rather than inventing a number (the whole point)."""
    try:
        c = (counts_fn or counts)()
    except Exception as exc:  # noqa: BLE001 — a dead ledger must NEVER become a fabricated count
        log.warning("leads_status: ledger unreachable: %s", exc)
        return ("I can't reach the lead ledger right now, so I won't guess at the numbers — "
                "ask again once Postgres is back.")

    by_src = ", ".join(f"{n:,} {s}" for s, n in c.get("by_source", []))
    parts = [f"Leads: {c['total']:,} total" + (f" ({by_src})" if by_src else "") + "."]
    parts.append(f"{c.get('with_email', 0):,} have an email on file.")
    parts.append(f"Today: {c.get('today', 0):,} new.")
    recent = "; ".join(f"{d}: {n:,}" for d, n in c.get("recent", [])[:3])
    if recent:
        parts.append(f"Recent daily intake — {recent}.")
    if c.get("probate") is not None:
        parts.append(f"Probate cases: {c['probate']:,}.")
    return " ".join(parts)


__all__ = ["counts", "answer"]
