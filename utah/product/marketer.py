"""Marketer capability — ready-skeleton, GATED on IG/TikTok posting creds.

Ace's marketer transitions HERE as a capability behind the brain, not an agent. It composes
a caption and routes a post (caption + media reference) through the publish boundary. The
boundary activates when channel creds land at ``~/.utah/secrets/<channel>.json``; until then
``post`` documents the gate and returns ``posted=False`` — it NEVER fakes a post (Ace's
"reels rendered into a folder nothing posted from" anti-pattern). Publisher injectable.
"""
from __future__ import annotations

import logging

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.product.marketer")

MAX_CAPTION = 2200  # Instagram caption limit
_SECRETS = runtime.UTAH_HOME / "secrets"


def creds_available(channel: str) -> bool:
    return (_SECRETS / f"{channel}.json").exists()


def compose_caption(subject: dict) -> str:
    """A short, honest promo caption for a local business subject."""
    name = (subject.get("name") or "this local business").strip()
    kind = (subject.get("kind") or "business").strip()
    cap = (f"Spotlight: {name} — a local {kind} worth knowing. "
           "Support local. DM us to get your business featured. #local #smallbusiness")
    return cap[:MAX_CAPTION]


def _real_publish(caption: str, media_ref: str, channel: str):  # pragma: no cover — network
    """REAL Instagram Reels publish via the Graph API (two-step: create container with a
    PUBLIC video_url, then publish). Was a stub that raised — the marketer could never
    post even WITH creds. Creds: ~/.utah/secrets/instagram.json
    {"access_token": "...", "ig_user_id": "..."} (IG Business/Creator account token with
    instagram_content_publish). media_ref must be a PUBLIC https mp4 URL. Other channels
    still raise → honest post_failed, never faked."""
    if channel != "instagram":
        raise RuntimeError(f"{channel} posting not implemented (creds at {_SECRETS}/{channel}.json)")
    import json as _json
    import time as _time
    import urllib.parse as _up
    import urllib.request as _ur

    creds = _json.loads((_SECRETS / "instagram.json").read_text())
    token, user = creds["access_token"], creds["ig_user_id"]
    base = f"https://graph.facebook.com/v21.0/{user}"

    def _post(url, params):
        data = _up.urlencode(params).encode()
        with _ur.urlopen(_ur.Request(url, data=data), timeout=60) as r:
            return _json.loads(r.read().decode())

    container = _post(f"{base}/media", {
        "media_type": "REELS", "video_url": media_ref,
        "caption": caption[:2190], "access_token": token,
    })["id"]
    for _ in range(30):                       # video processing: poll until FINISHED
        st = _json.loads(_ur.urlopen(
            f"https://graph.facebook.com/v21.0/{container}"
            f"?fields=status_code&access_token={_up.quote(token)}", timeout=30).read())
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"IG container processing failed: {st}")
        _time.sleep(5)
    return _post(f"{base}/media_publish",
                 {"creation_id": container, "access_token": token})["id"]


def post(caption: str, *, media_ref: str, channel: str = "instagram", publish_fn=None) -> dict:
    """Publish a post to *channel*. With an injected publisher or real creds it posts; with
    neither it documents the gate and returns posted=False (never fabricates). Never raises."""
    if publish_fn is None and not creds_available(channel):
        failures.record("marketer", "gated",
                        f"{channel} post gated: no creds at {_SECRETS}/{channel}.json "
                        "(Michael's posting creds)")
        return {"posted": False, "gated": True, "channel": channel}
    publisher = publish_fn or _real_publish
    try:
        post_id = publisher(caption, media_ref, channel)
        log.info("marketer: posted to %s (%s)", channel, post_id)
        return {"posted": True, "gated": False, "channel": channel, "id": post_id}
    except Exception as exc:  # noqa: BLE001
        failures.record("marketer", "post_failed", f"{channel}: {exc}")
        return {"posted": False, "gated": False, "channel": channel, "error": str(exc)}


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


def run_scheduled(ledger=None, *, to: str | None = None, foundation_gate=None) -> dict:
    """``com.utah.marketer`` cron — spotlight ONE not-yet-featured lead/day via email.
    UNIQUE(channel, media_ref) in marketer_posts prevents re-spotlighting the same business."""
    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("marketer")
    if skip:
        return skip

    import psycopg

    from utah import config
    from utah.product.ledger import Ledger

    lg = ledger or Ledger()
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        row = c.execute(
            "SELECT name, kind FROM leads WHERE name NOT IN "
            "(SELECT subject FROM marketer_posts WHERE channel='email_spotlight') "
            "ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return {"status": "no_fresh_lead"}
    out = spotlight({"name": row[0], "kind": row[1]}, to=to, ledger=lg)
    log.info("marketer cron: %s", out)
    return out


__all__ = ["compose_caption", "post", "spotlight", "run_scheduled",
           "creds_available", "MAX_CAPTION"]
