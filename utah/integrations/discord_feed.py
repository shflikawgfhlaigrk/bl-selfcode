"""Discord feed bridge — the spine's live domains flow into the matching Discord
channels, so the server is a live mirror of the deck, not a static shell.

Each revenue/spine domain (leads, probate, fires, audit, merges, announcements,
alerts) has a channel with a "Utah Feed" webhook created during provisioning and
saved to ``~/.utah/secrets/discord_webhooks.json``. This module maps a logical feed
key -> that channel's webhook URL and posts to it. Honest gate: no webhook for a key
-> :func:`publish` returns False WITHOUT faking a post. Every failure is logged.

Wire-in is intentionally thin: callers already producing a domain row (``leads.run``,
``trading`` fires, ``failures.record``, self-code merges) call the matching helper.
Nothing here connects to the gateway — webhooks need no bot process running.
"""
from __future__ import annotations

import json
import logging

from utah import config
from utah.daemon import runtime
from utah.integrations import discord as discord_mod

log = logging.getLogger("utah.integrations.discord_feed")

WEBHOOKS = runtime.UTAH_HOME / "secrets" / "discord_webhooks.json"

#: feed key (config.DISCORD_FEED_CHANNELS value) -> channel name (its key)
_KEY_TO_CHANNEL = {v: k for k, v in config.DISCORD_FEED_CHANNELS.items()}

# brand colors for embeds, per feed key (Discord int color)
_COLORS = {
    "leads": 0x3B82F6, "probate": 0x8B5CF6, "outreach": 0x06B6D4,
    "fires": 0xEF4444, "audit": 0x6B7280, "selfcode": 0x10B981,
    "announce": 0xE6B800, "critical": 0xDC2626,
}


def _load_hooks() -> dict[str, str]:
    try:
        d = json.loads(WEBHOOKS.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — missing file is a gate, not a crash
        return {}


def webhook_for(feed_key: str) -> str | None:
    """Resolve a logical feed key (e.g. ``leads``) to its channel webhook URL."""
    channel = _KEY_TO_CHANNEL.get(feed_key)
    if not channel:
        return None
    return _load_hooks().get(channel)


def available(feed_key: str | None = None) -> bool:
    """True when at least one (or the named) feed webhook is configured."""
    hooks = _load_hooks()
    if feed_key is not None:
        return bool(webhook_for(feed_key))
    return bool(hooks)


def publish(feed_key: str, content: str = "", *, title: str = "", fields: dict | None = None,
            http_post=None) -> bool:
    """Post to the channel behind ``feed_key``. Builds a compact embed when ``title``
    or ``fields`` are given, else a plain message. Returns True on a real success."""
    url = webhook_for(feed_key)
    if not url:
        return False  # honest gate: no webhook wired for this domain
    embeds = None
    if title or fields:
        embed: dict = {"color": _COLORS.get(feed_key, 0xE6B800)}
        if title:
            embed["title"] = title[:256]
        if content:
            embed["description"] = content[:4000]
        if fields:
            embed["fields"] = [{"name": str(k)[:256], "value": str(v)[:1024], "inline": True}
                               for k, v in list(fields.items())[:25]]
        embeds = [embed]
        content = ""
    return discord_mod.post(url, content or "​", username="Utah", embeds=embeds, http_post=http_post)


# --- domain helpers (callers use these) --------------------------------------
def feed_lead(lead: dict, *, http_post=None) -> bool:
    name = lead.get("name") or lead.get("business") or "lead"
    return publish("leads", title=f"📈 {name}", fields={
        k: lead[k] for k in ("category", "region", "phone", "email", "address") if lead.get(k)
    }, http_post=http_post)


def feed_probate(row: dict, *, http_post=None) -> bool:
    return publish("probate", title=f"⚖️ {row.get('decedent') or row.get('case') or 'probate'}",
                   fields={k: row[k] for k in ("county", "filed", "heir_contact", "arv") if row.get(k)},
                   http_post=http_post)


def feed_fire(fire: dict, *, http_post=None) -> bool:
    eng = fire.get("engine") or "engine"
    return publish("fires", title=f"🔥 {eng} fired", fields={
        k: fire[k] for k in ("symbol", "side", "entry", "target", "stop", "confidence") if fire.get(k)
    }, http_post=http_post)


def feed_merge(branch: str, utility: str | float = "", tier: str = "", *, http_post=None) -> bool:
    return publish("selfcode", title="✅ Self-code merged to main",
                   fields={"branch": branch, "tier": tier, "utility": utility}, http_post=http_post)


def feed_audit(source: str, kind: str, detail: str = "", *, http_post=None) -> bool:
    return publish("audit", content=f"`{source}/{kind}` {detail}"[:1800], http_post=http_post)


def announce(text: str, *, http_post=None) -> bool:
    return publish("announce", content=text, http_post=http_post)


def alert(text: str, *, http_post=None) -> bool:
    return publish("critical", title="🚨 Critical", content=text, http_post=http_post)
