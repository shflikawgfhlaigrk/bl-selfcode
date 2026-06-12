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


def test_brief_carries_deck_tailnet_link(monkeypatch):
    # brief is priority 0 → quiet-hours gated; pin the clock to noon so the test
    # is deterministic regardless of when the suite/verify-gate runs (was flaky-RED at night).
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 12, 0, 0))
    s = _capture()
    alerts.brief("UTAH MORNING BRIEF\n164 leads", sender=s)
    assert s.calls[0]["url"] == config.DECK_TAILNET_URL
    assert "164 leads" in s.calls[0]["message"]


def test_leads_and_probate_summaries(monkeypatch):
    # leads_probate is priority <1 → quiet-hours gated; pin the clock to noon so the
    # test is deterministic regardless of run time (was flaky-RED at night).
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 12, 0, 0))
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
        def record_fire(self, engine, direction, entry=None, synthetic=False, **kw):
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


# --- courier IS the live transport (deleting courier.py kills every page) -------

def test_default_transport_routes_through_courier(monkeypatch):
    """With no injected sender (the live daemon/cron shape), _send hands delivery to
    courier.deliver — the one channel router — instead of hand-picking pushover.
    The injectable _SENDER seam is untouched: an injected sender still bypasses."""
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 12, 0, 0))
    routed = []

    def fake_deliver(message, *, via, subject="Utah", priority=0, target=None,
                     url=None, url_title=None, **kw):
        routed.append({"message": message, "via": via, "subject": subject,
                       "priority": priority, "url": url, "url_title": url_title})
        return {"via": via, "sent": True, "gated": False}

    monkeypatch.setattr(courier, "deliver", fake_deliver)
    monkeypatch.setattr(alerts, "_mirror_desktop", lambda *a, **k: None)
    alerts.set_sender(None)                      # the live shape: no injected sender
    r = alerts.critical("watchdog", "daemon down", key="courier-live-path")
    assert r["sent"] is True
    assert routed and routed[0]["via"] == "push" and routed[0]["priority"] == 2
    assert "daemon down" in routed[0]["message"]


def test_courier_routed_brief_keeps_deck_link(monkeypatch):
    """The brief's deck tap-through survives the courier hop on the live path."""
    monkeypatch.setattr(alerts, "_now", lambda: datetime(2026, 6, 7, 12, 0, 0))
    routed = []
    monkeypatch.setattr(courier, "deliver",
                        lambda m, **k: routed.append({"message": m, **k})
                        or {"via": k.get("via"), "sent": True, "gated": False})
    alerts.set_sender(None)
    r = alerts.brief("UTAH MORNING BRIEF\n164 leads")
    assert r["sent"] is True
    assert routed[0]["via"] == "push" and routed[0]["url"] == config.DECK_TAILNET_URL


def test_injected_sender_still_bypasses_courier(monkeypatch):
    """The _SENDER seam exists so the suite never pushes for real — an injected
    sender must keep bypassing courier entirely (no double delivery)."""
    def boom(*a, **k):
        raise AssertionError("courier must not be consulted when a sender is injected")
    monkeypatch.setattr(courier, "deliver", boom)
    s = _capture()
    r = alerts.critical("watchdog", "daemon down", key="sender-bypass", sender=s)
    assert r["sent"] is True and len(s.calls) == 1


def test_desktop_mirror_routes_through_courier(monkeypatch):
    """The high-priority desktop mirror (the non-push channel) also rides courier —
    with the perms gate intact and the emoji-cleaned title preserved."""
    from utah.integrations import notify
    monkeypatch.setattr(notify, "perms_available", lambda: True)
    calls = []
    monkeypatch.setattr(courier, "deliver",
                        lambda m, **k: calls.append({"message": m, **k})
                        or {"via": k.get("via"), "sent": True, "gated": False})
    alerts._mirror_desktop("⚠️ Utah CRITICAL", "daemon down", priority=2)
    assert calls and calls[0]["via"] == "notify"
    assert calls[0]["subject"] == "Utah CRITICAL"        # emoji stripped, title kept


def test_desktop_mirror_still_gated_on_perms(monkeypatch):
    from utah.integrations import notify
    monkeypatch.setattr(notify, "perms_available", lambda: False)
    def boom(*a, **k):
        raise AssertionError("no perms => the mirror must not deliver")
    monkeypatch.setattr(courier, "deliver", boom)
    alerts._mirror_desktop("⚠️ Utah CRITICAL", "daemon down", priority=2)  # no raise


# ── storm-proofing (2026-06-10 "why the fuck do i have hundreds of trade
# notifications"): durable dedup, trade TTL, hourly page budget ────────────────

def test_dedup_survives_a_process_restart(tmp_path):
    """The storm root cause: in-memory dedup wiped on every wcfeed restart →
    every active signal re-paged. The map is now file-backed: a 'restarted'
    module (fresh in-memory state, same file) still suppresses."""
    alerts.set_seen_path(tmp_path / "seen.json")
    s = _capture()
    assert alerts.trade_fire("breakout", "long", 100.0, symbol="US.SPY", sender=s)["sent"]
    # simulate the restart: drop ALL in-memory state, keep the file
    alerts._reset_seen_for_tests()
    r = alerts.trade_fire("breakout", "long", 101.0, symbol="US.SPY", sender=s)
    assert r["gated"] is True and r["reason"] == "deduped"
    assert len(s.calls) == 1


def test_trade_uses_its_own_longer_ttl(monkeypatch, tmp_path):
    """trade pages dedup on TRADE_ALERT_DEDUP_SECONDS (2h default), not the 30-min
    stream TTL — one page per signal episode per engine+symbol+direction."""
    alerts.set_seen_path(tmp_path / "seen.json")
    monkeypatch.setattr(config, "ALERT_DEDUP_SECONDS", 0)      # stream TTL: no dedup
    monkeypatch.setattr(config, "TRADE_ALERT_DEDUP_SECONDS", 3600)
    s = _capture()
    assert alerts.trade_fire("meanrev", "short", 50.0, symbol="US.GLD", sender=s)["sent"]
    r = alerts.trade_fire("meanrev", "short", 51.0, symbol="US.GLD", sender=s)
    assert r["reason"] == "deduped"                            # trade TTL still holds
    assert len(s.calls) == 1


def test_trade_budget_caps_pages_per_hour_but_distinct_keys_until_then(monkeypatch, tmp_path):
    """36 distinct engine+symbol+direction keys can each pass dedup — the per-hour
    budget is the hard phone protector. Capped fires return gated:trade_budget."""
    alerts.set_seen_path(tmp_path / "seen.json")
    monkeypatch.setattr(config, "TRADE_ALERTS_PER_HOUR", 3)
    s = _capture()
    syms = ["US.SPY", "US.QQQ", "US.GLD", "US.IWM", "US.XLE"]
    results = [alerts.trade_fire("breakout", "long", 1.0, symbol=sym, sender=s)
               for sym in syms]
    assert [r["sent"] for r in results] == [True, True, True, False, False]
    assert all(r["reason"] == "trade_budget" for r in results[3:])
    assert len(s.calls) == 3


def test_trade_budget_never_starves_critical(monkeypatch, tmp_path):
    """The budget is TRADE-stream only: a critical page goes out even when trade
    pages are capped."""
    alerts.set_seen_path(tmp_path / "seen.json")
    monkeypatch.setattr(config, "TRADE_ALERTS_PER_HOUR", 0)
    s = _capture()
    assert alerts.trade_fire("breakout", "long", 1.0, symbol="US.SPY", sender=s)["sent"] is False
    assert alerts.critical("watchdog", "daemon down", sender=s)["sent"] is True
