"""Alert taxonomy + the four streams Michael chose (critical / trade / brief /
leads_probate) and their wiring sites (failures.record critical hook, trading fire,
brief push, courier push). Stream gating, quiet hours, and storm-dedup are proven with
an injected capturing sender — zero network, zero real pushes.
"""
from __future__ import annotations

from datetime import datetime

from utah import alerts, config, courier, failures
from utah.product import brief, trading
from tests.fakes import FakeFailureStore


def _capture():
    """A capturing push sender matching the pushover.send signature."""
    calls = []
    def sender(message, *, title="Utah", priority=0, target=None, url=None, url_title=None):
        calls.append({"message": message, "title": title, "priority": priority,
                      "target": target, "url": url, "url_title": url_title})
        return {"sent": True, "gated": False}
    sender.calls = calls
    return sender


# --- stream gating -------------------------------------------------------------

def test_disabled_stream_is_gated(monkeypatch):
    monkeypatch.setattr(config, "ALERT_STREAMS_ENABLED", frozenset())
    s = _capture()
    r = alerts.critical("watchdog", "down", sender=s)
    assert r["gated"] is True and not s.calls


def test_priorities_match_taxonomy():
    s = _capture()
    alerts.critical("w", "d", sender=s)
    alerts.trade_fire("breakout", "long", 100.0, fire_id=1, sender=s)
    by_title = {c["title"]: c for c in s.calls}
    assert by_title["⚠️ Utah CRITICAL"]["priority"] == 2
    assert by_title["📈 Utah trade fire"]["priority"] == 1


# --- quiet hours ---------------------------------------------------------------

def test_normal_stream_suppressed_in_quiet_hours(monkeypatch):
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 3, 0, 0))  # 03:00, inside 22:30–06:30
    s = _capture()
    r = alerts.brief("UTAH MORNING BRIEF\nall good", sender=s)
    assert r["gated"] is True and r["reason"] == "quiet_hours" and not s.calls


def test_emergency_bypasses_quiet_hours(monkeypatch):
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 3, 0, 0))
    s = _capture()
    r = alerts.critical("watchdog", "daemon down", sender=s)
    assert r.get("sent") is True and len(s.calls) == 1   # prio 2 wakes the phone anyway


def test_quiet_hours_window_wraps_midnight(monkeypatch):
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 12, 0, 0))  # noon: NOT quiet
    assert alerts.in_quiet_hours() is False
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 23, 0, 0))  # 23:00: quiet
    assert alerts.in_quiet_hours() is True


# --- storm dedup ---------------------------------------------------------------

def test_dedup_suppresses_a_storm():
    s = _capture()
    r1 = alerts.critical("daemon", "unreachable", key="daemon/unreachable", sender=s)
    r2 = alerts.critical("daemon", "unreachable", key="daemon/unreachable", sender=s)
    assert r1.get("sent") is True
    assert r2["gated"] is True and r2["reason"] == "deduped"
    assert len(s.calls) == 1                              # one page, not a storm


# --- the four stream message shapes -------------------------------------------

def test_trade_fire_message_has_engine_direction_entry():
    s = _capture()
    alerts.trade_fire("breakout", "long", 4321.5, fire_id=7, sender=s)
    msg = s.calls[0]["message"]
    assert "BREAKOUT" in msg and "LONG" in msg and "4321.5" in msg and "#7" in msg


def test_brief_carries_deck_tailnet_link():
    s = _capture()
    alerts.brief("UTAH MORNING BRIEF\n164 leads", sender=s)
    assert s.calls[0]["url"] == config.DECK_TAILNET_URL
    assert "164 leads" in s.calls[0]["message"]


def test_leads_and_probate_summaries():
    s = _capture()
    alerts.leads_probate({"new": 33, "found": 197, "tiles_scanned": 12}, kind="leads", sender=s)
    alerts.leads_probate({"new": 5, "found": 64}, kind="probate", sender=s)
    assert "33 new leads" in s.calls[0]["message"]
    assert "5 new probate" in s.calls[1]["message"]


# --- wiring sites --------------------------------------------------------------

def test_failures_record_pages_on_critical_kind(monkeypatch):
    failures.set_store(FakeFailureStore())
    seen = []
    monkeypatch.setattr(alerts, "critical_async",
                        lambda source, detail="", key=None: seen.append((source, detail, key)))
    failures.record("watchdog", "daemon_unreachable", "ping failed")
    assert seen == [("watchdog", "ping failed", "watchdog/daemon_unreachable")]


def test_failures_record_does_not_page_on_routine_kind(monkeypatch):
    failures.set_store(FakeFailureStore())
    seen = []
    monkeypatch.setattr(alerts, "critical_async",
                        lambda source, detail="", key=None: seen.append(source))
    failures.record("browser", "render_failed", "chrome timed out")   # NOT a critical kind
    assert seen == []


def test_trading_run_pages_on_real_fire():
    failures.set_store(FakeFailureStore())
    s = _capture(); alerts.set_sender(s)

    class _Ledger:
        def record_fire(self, engine, direction, entry=None, synthetic=False):
            return 42
    closes = [float(i) for i in range(25)] + [999.0]       # last close breaks the prior high
    r = trading.run(_Ledger(), feed_fn=lambda: closes)
    assert r["fires"] == 1
    assert len(s.calls) == 1 and s.calls[0]["priority"] == 1   # trade stream paged


def test_brief_run_pushes_via_push_fn():
    failures.set_store(FakeFailureStore())
    state = {"ledger_counts": {"leads": 1, "probate": 0, "outreach_ledger": 0, "fires": 0},
             "memory_live": 5, "failures_recent": [], "leads_recent": []}
    pushed = []
    r = brief.run(gather=lambda: state, speak_fn=None, can_email=False,
                  push_fn=lambda text: pushed.append(text) or {"sent": True})
    assert r["pushed"] is True and pushed


def test_courier_push_routes_to_sender():
    failures.set_store(FakeFailureStore())
    sent = {}
    r = courier.deliver("ping", via="push", subject="Hi", priority=1,
                        push_send=lambda m, **k: (sent.update({"m": m, **k}), {"sent": True})[1])
    assert r["via"] == "push" and r["sent"] is True
    assert sent["m"] == "ping" and sent["title"] == "Hi" and sent["priority"] == 1
