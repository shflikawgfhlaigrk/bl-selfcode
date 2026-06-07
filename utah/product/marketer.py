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


def _real_publish(caption: str, media_ref: str, channel: str):  # pragma: no cover
    raise RuntimeError(f"{channel} posting not configured (creds at {_SECRETS}/{channel}.json)")


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


__all__ = ["compose_caption", "post", "creds_available", "MAX_CAPTION"]
