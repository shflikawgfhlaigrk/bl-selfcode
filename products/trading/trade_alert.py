"""Pushover trade-fire alert — rich entry/stop/target/rationale on the phone.

Delegates to :mod:`utah.alerts` (which routes through :mod:`utah.integrations.pushover`).
Honest gate: no creds → ``sent=False, gated=True`` — never faked.
"""
from __future__ import annotations


def compose(fire: dict) -> str:
    """Human-readable alert body from a fire dict (for tests and logging)."""
    parts = [f"🔥 {fire.get('engine', '?')} {str(fire.get('direction', '?')).upper()} "
             f"@ {fire.get('entry')}"]
    if fire.get("stop") is not None:
        parts.append(f"stop {fire['stop']}")
    if fire.get("target") is not None:
        parts.append(f"target {fire['target']}")
    if fire.get("rationale"):
        parts.append(f"— {fire['rationale']}")
    return " | ".join(parts)


def send_fire_alert(fire: dict, *, sender=None) -> dict:
    """Page the phone for a trade fire. Never raises."""
    from utah import alerts

    return alerts.trade_fire(
        str(fire.get("engine", "?")),
        str(fire.get("direction", "?")),
        fire.get("entry"),
        fire_id=fire.get("fire_id") or fire.get("id"),
        stop=fire.get("stop"),
        target=fire.get("target"),
        rationale=fire.get("rationale"),
        sender=sender,
    )


__all__ = ["compose", "send_fire_alert"]
