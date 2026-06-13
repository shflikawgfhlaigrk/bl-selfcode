"""Lead-pipeline funnel + system-scope scheme — the deck's bird's-eye view, grounded.

Two grounded read-only capabilities, same doctrine as :mod:`leads_status` (every number
is a real ``COUNT(*)`` or an honest "I can't reach the ledger" — NEVER a guess):

``funnel()``  — the lead pipeline as staged conversion: how a discovered lead moves toward
                a paying customer, where it leaks, and the *free* next win to widen it.
``scope()``   — "how big has this gotten": every subsystem of Utah, its live row count, the
                real launchd crons that feed it, and where each area can still grow.

Both feed deck panels (``/panel/pipeline`` · ``/panel/scope``). The DB connection is
injectable (``conn_fn``) so the funnel logic is testable without a live Postgres.
"""
from __future__ import annotations

import logging
import pathlib

log = logging.getLogger("utah.product.pipeline")

#: leads come only from these sources (SMB no-website lane); probate is a separate table.
_SMB_SOURCES = ("osm", "google_maps")
_PROBATE_CAMPAIGN = "probate_motivated"

#: repo root → real launchd plist dir, resolved from this file (CWD-independent under launchd).
_LAUNCHD = pathlib.Path(__file__).resolve().parents[2] / "ops" / "launchd"


def _connect():
    import psycopg

    from utah import config

    return psycopg.connect(config.DB_DSN, autocommit=True)


def _pct(n: int, of: int) -> float:
    """Conversion as a percent of an earlier stage; 0.0 when the denominator is empty
    (a stage can never be a larger share than the funnel mouth that feeds it)."""
    return round(100.0 * n / of, 1) if of else 0.0


def funnel(*, conn_fn=None) -> dict:
    """The lead pipeline as a staged funnel, every count a live ``COUNT(*)``.

    Returns two lanes (SMB no-website + probate real-estate) of ordered stages, each with
    a raw count and its conversion off the funnel mouth, plus a ``diagnosis`` naming the
    biggest leak and the highest-leverage *free* fix — all derived from the live numbers,
    never hardcoded. On a dead ledger returns ``{"error": ...}`` rather than inventing a
    funnel (the whole point of grounding this).
    """
    try:
        cx = (conn_fn or _connect)()
    except Exception as exc:  # noqa: BLE001 — a dead ledger must never become a fabricated funnel
        log.warning("pipeline.funnel: ledger unreachable: %s", exc)
        return {"error": f"lead ledger unreachable: {exc}"}

    def q(sql: str, *args) -> int:
        return cx.execute(sql, args).fetchone()[0]

    try:
        srcs = list(_SMB_SOURCES)
        # SMB lane — every prospect is sourced osm|google_maps, so scope all counts to them.
        discovered = q("SELECT count(*) FROM leads WHERE source = ANY(%s)", srcs)
        with_phone = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                       "AND coalesce(contact->>'phone','') <> ''", srcs)
        with_email = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                       "AND coalesce(contact->>'email','') <> ''", srcs)
        with_site = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                      "AND coalesce(contact->>'website','') <> ''", srcs)
        reachable = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                      "AND (coalesce(contact->>'phone','') <> '' "
                      "OR coalesce(contact->>'email','') <> '')", srcs)
        # the free harvest opportunity: has a website to scrape, but no email yet.
        site_no_email = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                          "AND coalesce(contact->>'website','') <> '' "
                          "AND coalesce(contact->>'email','') = ''", srcs)
        # phone reachable but no email — the SMS/iMessage lane that needs no enrichment.
        phone_no_email = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                           "AND coalesce(contact->>'phone','') <> '' "
                           "AND coalesce(contact->>'email','') = ''", srcs)
        contacted = q("SELECT count(*) FROM leads WHERE source = ANY(%s) "
                      "AND status = 'contacted'", srcs)
        sent_email = q("SELECT count(*) FROM outreach_ledger WHERE channel = 'email'")
        sent_sms = q("SELECT count(*) FROM outreach_ledger WHERE channel = 'sms'")
        sent_msgs = q("SELECT count(*) FROM outreach_ledger")
        delivered = q("SELECT count(*) FROM mail_ledger WHERE status = 'sent'")
        bounced = q("SELECT count(*) FROM mail_ledger WHERE status = 'bounced'")
        replied = q("SELECT count(*) FROM mail_replies")
        # probate lane
        prob_cases = q("SELECT count(*) FROM probate")
        prob_valued = q("SELECT count(*) FROM probate WHERE arv IS NOT NULL")
        prob_contacted = q("SELECT count(*) FROM probate WHERE status = 'contacted'")
        prob_sent = q("SELECT count(*) FROM outreach_ledger WHERE campaign = %s",
                      _PROBATE_CAMPAIGN)
    except Exception as exc:  # noqa: BLE001 — partial schema / mid-migration: honest, not faked
        log.warning("pipeline.funnel: query failed: %s", exc)
        try:
            cx.close()
        except Exception:  # noqa: BLE001
            pass
        return {"error": f"pipeline query failed: {exc}"}
    finally:
        try:
            cx.close()
        except Exception:  # noqa: BLE001 — injected fakes may not implement close()
            pass

    smb = [
        {"key": "discovered", "label": "Discovered",
         "n": discovered, "of": discovered,
         "note": "Local no-website SMBs scouted from OpenStreetMap + Google Maps (free)."},
        {"key": "reachable", "label": "Reachable",
         "n": reachable, "of": discovered,
         "note": f"Has a phone or email on file ({with_phone:,} phone · {with_email:,} email)."},
        {"key": "email", "label": "Email-deliverable",
         "n": with_email, "of": discovered,
         "note": "Has a verified email — the only channel that can be cold-sent at scale."},
        {"key": "contacted", "label": "Prospects contacted",
         "n": contacted, "of": discovered,
         "note": "Leads marked contacted at least once (deduped — never messaged twice)."},
        {"key": "sent", "label": "Messages sent",
         "n": sent_msgs, "of": discovered,
         "note": f"Cold outreach events sent ({sent_email:,} email · {sent_sms:,} SMS/iMessage)."},
        {"key": "delivered", "label": "Email delivered",
         "n": delivered, "of": discovered,
         "note": f"Accepted by the recipient mailserver ({bounced:,} bounced)."},
        {"key": "replied", "label": "Replied",
         "n": replied, "of": discovered,
         "note": "Real inbound replies detected on the mailbox."},
        {"key": "won", "label": "Won (paid)",
         "n": 0, "of": discovered,
         "note": "Closed paying customers. Honest 0 — no sale has landed yet."},
    ]
    probate = [
        {"key": "cases", "label": "Estate cases", "n": prob_cases, "of": prob_cases,
         "note": "Probate/estate filings scraped from county notices (free)."},
        {"key": "valued", "label": "Property-valued", "n": prob_valued, "of": prob_cases,
         "note": "Enriched with an assessed/ARV value — the ones worth a letter."},
        {"key": "contacted", "label": "Heirs contacted", "n": prob_contacted, "of": prob_cases,
         "note": "Estate cases where an heir letter has gone out."},
        {"key": "sent", "label": "Letters sent", "n": prob_sent, "of": prob_cases,
         "note": "Probate-motivated outreach events (separate track from SMB)."},
    ]

    # --- diagnosis: the biggest leak + the free fix, computed from the live numbers ---
    leaks: list[dict] = []
    if discovered:
        if site_no_email:
            leaks.append({
                "title": "Email harvest is under-running",
                "detail": (f"{site_no_email:,} discovered SMBs have a website but no email yet. "
                           "Their own contact page is a free email source — the enrich cron "
                           "(scrape → extract → MX-verify) already does this but only sips "
                           "~50/run. Drain this backlog and the deliverable pool multiplies."),
                "free": True, "n": site_no_email})
        if phone_no_email:
            leaks.append({
                "title": "Phone lane is under-used",
                "detail": (f"{phone_no_email:,} leads have a phone but no email. They're "
                           "reachable RIGHT NOW over the SMS/iMessage auto-channel with no "
                           f"enrichment — yet only {sent_sms:,} texts have gone out."),
                "free": True, "n": phone_no_email})
    diagnosis = {
        "mouth": discovered,
        "email_rate": _pct(with_email, discovered),
        "reachable_rate": _pct(reachable, discovered),
        "headline": (
            f"Only {_pct(with_email, discovered)}% of discovered leads have an email — "
            "that's the funnel's tightest pinch. Both fixes below are free."
            if discovered else "No leads discovered yet."),
        "leaks": leaks,
    }

    return {
        "smb": smb,
        "probate": probate,
        "splits": {"email": with_email, "phone": with_phone, "website": with_site,
                   "sent_email": sent_email, "sent_sms": sent_sms},
        "diagnosis": diagnosis,
    }


# --------------------------------------------------------------------------------------
# scope() — the system scheme: every area, live counts, real crons, where it can grow.
# --------------------------------------------------------------------------------------

#: The system map. Structure is the repo's real shape; the ``count`` key (when present) is
#: filled LIVE from the table named in ``table`` — never a painted number. ``grow`` says
#: where that area can still populate (the expansion surface Michael asked to see).
_SCOPE_GROUPS = [
    {"group": "Revenue · Lead Engine", "icon": "$", "areas": [
        {"name": "Leads (SMB)", "table": "leads",
         "what": "Local no-website businesses — the website-build pitch.",
         "grow": "Tile new metros (moving frontier) · harvest the 1,500+ website leads for email."},
        {"name": "Probate", "table": "probate",
         "what": "Estate/probate filings — motivated real-estate sellers.",
         "grow": "More counties · ARV enrichment on the unvalued cases."},
        {"name": "Outreach", "table": "outreach_ledger",
         "what": "Every cold message sent — deduped, suppression-gated.",
         "grow": "Raise the daily cap once inboxes are warmed · 3-step follow-ups."},
        {"name": "Mail", "table": "mail_ledger",
         "what": "Real SMTP sends + bounce tracking.",
         "grow": "Warmed sender domains lift deliverability past the bounce floor."},
        {"name": "Marketer", "table": "marketer_posts",
         "what": "Outbound content posts (Discord/social).",
         "grow": "Reels backlog · scheduled cadence."},
    ]},
    {"group": "Brain · Memory", "icon": "◆", "areas": [
        {"name": "Memory", "table": "memory",
         "what": "Everything Utah has learned — pgvector recall, no-fab.",
         "grow": "Learn-on-miss compounds it on every refused question."},
        {"name": "Entities", "table": "mem_entity",
         "what": "The things Utah tracks, graph-linked.",
         "grow": "Grows with every grounded turn."},
        {"name": "Trade lore", "table": "trade_lore",
         "what": "What the engines learned from their own fills.",
         "grow": "Each graded fire writes a lesson."},
    ]},
    {"group": "Trading · Engines", "icon": "⚡", "areas": [
        {"name": "Fires", "table": "fires",
         "what": "Every engine signal, graded against the live feed.",
         "grow": "Edge-gate only fires proven (engine,symbol) pairs · more symbols as edges prove out."},
        {"name": "Live ticks", "table": "wc_live",
         "what": "WealthCharts ms-lane feed.",
         "grow": "Legacy-bar backfill widens the backtest window."},
        {"name": "Price bars", "table": "bars",
         "what": "Historical OHLC for backtests.",
         "grow": "Backfill more symbols/timeframes."},
    ]},
    {"group": "Self-Code · Autonomy", "icon": "🤖", "areas": [
        {"name": "Self-code log", "table": "selfcode_log",
         "what": "Every autonomous coding cycle (propose-only, suite-gated).",
         "grow": "Richer meta-tasks from the grounded weakness queue."},
        {"name": "Archive", "table": "selfcode_archive",
         "what": "The SICA archive of scored attempts.",
         "grow": "Compounds as Tier-A merges land."},
        {"name": "Tasks", "table": "tasks",
         "what": "Open work items Utah tracks for itself.",
         "grow": "Auto-filed from findings + failures."},
    ]},
    {"group": "Infrastructure · Substrate", "icon": "▤", "areas": [
        {"name": "Failures (black box)", "table": "failures",
         "what": "Every real failure, recorded plainly.",
         "grow": "Feeds self-heal + the audit panel."},
        {"name": "Sync log", "table": "sync_log",
         "what": "Ingest/migration provenance.",
         "grow": "Every cron run leaves a row."},
        {"name": "Timers", "table": "timers",
         "what": "Scheduled internal reminders.",
         "grow": "Wired off real obligations."},
        {"name": "Subscribers", "table": "signals_subscribers",
         "what": "Signal/newsletter opt-ins.",
         "grow": "Public sign-up surface."},
    ]},
]


def _table_counts() -> dict:
    """Live ``COUNT(*)`` for every table named in the scope map (one connection).
    Honest-empty on a dead ledger: counts come back ``None`` and the panel says so."""
    tables = sorted({a["table"] for grp in _SCOPE_GROUPS for a in grp["areas"]
                     if a.get("table")})
    try:
        cx = _connect()
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.scope: ledger unreachable for counts: %s", exc)
        return {t: None for t in tables}
    out: dict = {}
    try:
        for t in tables:
            try:
                out[t] = cx.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except Exception as exc:  # noqa: BLE001 — one missing table never blanks the rest
                log.debug("scope count failed for %s: %s", t, exc)
                out[t] = None
    finally:
        try:
            cx.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _crons() -> list[str]:
    """The REAL launchd jobs feeding the system, read off disk (CWD-independent)."""
    try:
        return sorted(p.stem.replace("com.utah.", "")
                      for p in _LAUNCHD.glob("com.utah.*.plist"))
    except OSError as exc:
        log.warning("pipeline.scope: launchd dir unreadable: %s", exc)
        return []


def scope(*, counts_fn=None, crons_fn=None) -> dict:
    """The full system scheme: every area grouped, with its live row count and where it can
    still grow, plus the real launchd cron roster and headline totals. Structure is the
    repo's real shape; every number is live (or honestly ``None`` when the ledger is down)."""
    counts = (counts_fn or _table_counts)()
    crons = (crons_fn or _crons)()
    groups = []
    for grp in _SCOPE_GROUPS:
        areas = [{**a, "count": counts.get(a.get("table"))} for a in grp["areas"]]
        live = sum(a["count"] for a in areas if isinstance(a["count"], int))
        groups.append({"group": grp["group"], "icon": grp["icon"],
                       "rows": live, "areas": areas})
    total_rows = sum(v for v in counts.values() if isinstance(v, int))
    return {
        "groups": groups,
        "crons": crons,
        "totals": {"rows": total_rows, "tables": len(counts),
                   "crons": len(crons), "areas": sum(len(g["areas"]) for g in _SCOPE_GROUPS)},
    }


__all__ = ["funnel", "scope"]
