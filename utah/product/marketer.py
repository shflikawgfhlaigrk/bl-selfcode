"""Marketer capability — ready-skeleton, GATED on IG/TikTok posting creds.

Ace's marketer transitions HERE as a capability behind the brain, not an agent. It composes
a caption and routes a post (caption + media reference) through the publish boundary. The
boundary activates when channel creds land at ``~/.utah/secrets/<channel>.json``; until then
``post`` documents the gate and returns ``posted=False`` — it NEVER fakes a post (Ace's
"reels rendered into a folder nothing posted from" anti-pattern). Publisher injectable.
"""
from __future__ import annotations

import logging

import psycopg

from utah import config, failures
from utah.integrations import social_post

log = logging.getLogger("utah.product.marketer")

MAX_CAPTION = 2200  # Instagram caption limit


def creds_available(channel: str) -> bool:
    return social_post.creds_available(channel)


def compose_caption(subject: dict) -> str:
    """A short, honest promo caption for a local business subject."""
    name = (subject.get("name") or "this local business").strip()
    kind = (subject.get("kind") or "business").strip()
    cap = (f"Spotlight: {name} — a local {kind} worth knowing. "
           "Support local. DM us to get your business featured. #local #smallbusiness")
    return cap[:MAX_CAPTION]


def _real_publish(caption: str, media_ref: str, channel: str) -> str:  # pragma: no cover
    """Delegate to the social posting boundary (IG Graph + TikTok Content Posting)."""
    pub = social_post.publish_instagram if channel == "instagram" else social_post.publish_tiktok
    return pub(caption, media_ref)


def post(caption: str, *, media_ref: str, channel: str = "instagram", publish_fn=None) -> dict:
    """Publish a post to *channel*. With an injected publisher or real creds it posts; with
    neither it documents the gate and returns posted=False (never fabricates). Never raises."""
    if publish_fn is not None:
        try:
            post_id = publish_fn(caption, media_ref, channel)
            log.info("marketer: posted to %s (%s)", channel, post_id)
            return {"posted": True, "gated": False, "channel": channel, "id": post_id}
        except Exception as exc:  # noqa: BLE001
            failures.record("marketer", "post_failed", f"{channel}: {exc}")
            return {"posted": False, "gated": False, "channel": channel, "error": str(exc)}

    res = social_post.post(caption, media_ref=media_ref, channel=channel)
    if res.get("gated"):
        failures.record("marketer", "gated", f"{channel} post gated (Michael's posting creds)")
    elif res.get("error"):
        failures.record("marketer", "post_failed", f"{channel}: {res['error']}")
    elif res.get("posted"):
        log.info("marketer: posted to %s (%s)", channel, res.get("id"))
    return res


def spotlight(lead: dict, *, to: str | None = None, send_fn=None, ledger=None) -> dict:
    """Marketing v1 — email-spotlight a real local business. EMAIL is the only channel
    live today (gmail.json present; IG/TikTok gated on creds + a media renderer), so this
    is the minimal REAL, autonomous, provable marketing action: compose a caption, send it
    as an email via the proven mail path, and record it in marketer_posts (deck lights up).
    Defaults to Michael (a brand digest), never cold-emails a business here — that's the
    outreach path with its suppression. Never fabricates a post."""
    from utah import mail
    from utah.product.ledger import Ledger

    lg = ledger or Ledger()
    name = (lead.get("name") or "a local business").strip()
    caption = compose_caption(lead)
    media_ref = f"spotlight:{name}"
    res = (send_fn or mail.send)(to or "mtuburnsbarber@gmail.com", f"Spotlight: {name}", caption)
    status = "posted" if res.get("sent") else ("gated" if res.get("gated") else "failed")
    try:
        lg.record_post("email_spotlight", caption, media_ref, name, status,
                       res.get("to") if res.get("sent") else None)
    except Exception as exc:  # noqa: BLE001 — a ledger hiccup must not lose the send result
        failures.record("marketer", "ledger_write_failed", str(exc))
    return {"channel": "email_spotlight", "subject": name, "status": status,
            "sent": bool(res.get("sent"))}


def _pick_fresh_lead() -> tuple[str, str] | None:
    """The next not-yet-spotlighted lead as ``(name, kind)``, or None when every lead has
    been featured. BOUNDED read (connect_timeout + statement_timeout — house DB rule, same
    pattern as selfcode_web) so a stalled Postgres can't hang the daily marketer cron."""
    with psycopg.connect(
            config.DB_DSN, autocommit=True, connect_timeout=8,
            options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as c:
        row = c.execute(
            "SELECT name, kind FROM leads WHERE name NOT IN "
            "(SELECT subject FROM marketer_posts WHERE channel='email_spotlight') "
            "ORDER BY ts DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else None


def run_scheduled(ledger=None, *, to: str | None = None, foundation_gate=None,
                  pick_fn=None, send_fn=None) -> dict:
    """``com.utah.marketer`` cron — spotlight ONE not-yet-featured lead/day via email.
    UNIQUE(channel, media_ref) in marketer_posts prevents re-spotlighting the same business.

    Launchd entrypoint (``print(run_scheduled())``): NEVER raises. A dead Postgres
    degrades to ``{"status": "store_unreachable", ...}`` with a recorded failure.
    ``pick_fn`` / ``send_fn`` are injectable so tests never touch the live store."""
    from utah import foundation
    from utah.product.ledger import Ledger

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("marketer")
    if skip:
        return skip
    lg = ledger or Ledger()
    try:
        picked = (pick_fn or _pick_fresh_lead)()
    except Exception as exc:  # noqa: BLE001 — cron boundary: dead store degrades, never raises
        failures.record("marketer", "store_unreachable", f"spotlight pick failed: {exc}")
        log.warning("marketer cron: lead store unreachable: %s", exc)
        return {"status": "store_unreachable", "error": str(exc)}
    if not picked:
        return {"status": "no_fresh_lead"}
    name, kind = picked
    out = spotlight({"name": name, "kind": kind}, to=to, send_fn=send_fn, ledger=lg)
    log.info("marketer cron: %s", out)
    return out


__all__ = ["compose_caption", "post", "spotlight", "run_scheduled",
           "creds_available", "MAX_CAPTION", "_pick_fresh_lead"]
