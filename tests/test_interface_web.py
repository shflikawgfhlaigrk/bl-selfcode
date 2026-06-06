"""The web bridge's deck-state builder: ONE live backend feed covering every
panel domain. Real where a producer exists (spine/daemon/memory from the live
daemon), honest-empty where none has landed yet (real-or-DORMANT, never faked),
so a new producer lights its panel with no frontend change."""
from __future__ import annotations

from utah.interface import web

# every panel on the deck maps to a domain in the single /state feed
PANEL_DOMAINS = {
    "health", "spine", "daemon", "memory",       # live (real producers exist)
    "engines", "agents", "risk", "voice", "leads",  # domain feeds
    "probate", "outreach", "audit", "tick", "sync", "events",
}

ST = {"version": "0.1.0", "uptime_s": 1.0, "memory": {"total": 3, "live": 2}}


def test_deck_state_exposes_every_panel_domain():
    missing = PANEL_DOMAINS - set(web._deck_state(ST))
    assert not missing, f"missing panel domains: {missing}"


def test_deck_state_health_live_when_daemon_up():
    assert web._deck_state(ST)["health"] == "live"


def test_deck_state_health_down_when_daemon_unreachable():
    assert web._deck_state(None)["health"] == "down"


def test_deck_state_no_producer_domains_are_honest_empty_lists():
    d = web._deck_state(ST)
    for k in ["engines", "agents", "leads", "probate", "outreach", "tick",
              "audit", "sync", "events"]:
        assert d[k] == [], f"{k} must be an honest-empty list (DORMANT), got {d[k]!r}"


def test_deck_state_passes_through_live_memory_and_spine():
    d = web._deck_state(ST)
    assert d["memory"] == {"total": 3, "live": 2}
    assert d["spine"] == ST and d["daemon"] == ST


def test_deck_state_when_daemon_down_spine_is_empty_not_fabricated():
    d = web._deck_state(None)
    assert d["spine"] == {} and d["memory"] == {}
