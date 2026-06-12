"""Per-probe canary contracts — each check's verdict logic, with the live HTTP/
ledger boundaries injected (monkeypatched), never mocked-as-the-system-under-test:
the verdict code runs for real, only the wire is scripted."""
from __future__ import annotations

import pytest

from utah import canary


# ── check_deck ────────────────────────────────────────────────────────────────

def _deck_state(**overrides):
    st = {"health": "live", "ledger": {"leads": 4666, "fires": 3},
          "leads": [{"id": 1}], "degraded": {}}
    st.update(overrides)
    return st


def test_deck_green_reports_counts(monkeypatch):
    monkeypatch.setattr(canary, "_http_json", lambda url, timeout=8.0: _deck_state())
    ok, detail = canary.check_deck()
    assert ok is True and "4666" in detail


def test_deck_not_live_is_a_failure(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: _deck_state(health="degraded"))
    ok, detail = canary.check_deck()
    assert ok is False and "degraded" in detail


def test_deck_empty_ledger_counts_is_dark_data_plane(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: _deck_state(ledger={}))
    ok, detail = canary.check_deck()
    assert ok is False and "dark" in detail


def test_deck_silent_blank_without_degraded_flag_fails(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: _deck_state(leads=[]))
    ok, detail = canary.check_deck()
    assert ok is False and "silent blank" in detail


def test_deck_blank_rows_with_honest_degraded_flag_passes(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: _deck_state(leads=[], degraded={"ledger": True}))
    ok, _ = canary.check_deck()
    assert ok is True


# ── check_ticks (session-aware freshness) ────────────────────────────────────

class _Ledger:
    def __init__(self, ticks):
        self._ticks = ticks

    def live_ticks(self):
        return self._ticks


def test_zero_age_tick_is_the_freshest_not_missing(monkeypatch):
    """age_ms=0 is a JUST-NOW tick. `or 9e12` treated 0 as absent and called a
    perfectly live feed dead — the falsy-zero bug."""
    monkeypatch.setattr(canary, "futures_session_open", lambda now=None: True)
    monkeypatch.setattr("utah.product.ledger.get_ledger",
                        lambda: _Ledger([{"age_ms": 0}]))
    ok, detail = canary.check_ticks()
    assert ok is True, detail


def test_stale_ticks_in_session_fail(monkeypatch):
    monkeypatch.setattr(canary, "futures_session_open", lambda now=None: True)
    monkeypatch.setattr("utah.product.ledger.get_ledger",
                        lambda: _Ledger([{"age_ms": 600_000}, {"age_ms": 900_000}]))
    ok, detail = canary.check_ticks()
    assert ok is False and "600" in detail


def test_no_ticks_in_session_fail(monkeypatch):
    monkeypatch.setattr(canary, "futures_session_open", lambda now=None: True)
    monkeypatch.setattr("utah.product.ledger.get_ledger", lambda: _Ledger([]))
    ok, detail = canary.check_ticks()
    assert ok is False and "no live ticks" in detail


def test_unreadable_age_ms_is_honest_shape_drift_not_a_crash(monkeypatch):
    monkeypatch.setattr(canary, "futures_session_open", lambda now=None: True)
    monkeypatch.setattr("utah.product.ledger.get_ledger",
                        lambda: _Ledger([{"age_ms": "garbage"}, {"age_ms": None}]))
    ok, detail = canary.check_ticks()
    assert ok is False and "age_ms" in detail


def test_closed_market_never_judges_freshness(monkeypatch):
    monkeypatch.setattr(canary, "futures_session_open", lambda now=None: False)
    ok, detail = canary.check_ticks()        # ledger never touched
    assert ok is True and "closed" in detail


# ── check_sovereign (multi-port) ─────────────────────────────────────────────

def test_sovereign_found_on_second_port(monkeypatch):
    def fake(url, timeout=8.0):
        if ":8775" in url:
            raise OSError("connection refused")
        return {"ok": True, "agents": 7}

    monkeypatch.setattr(canary, "_http_json", fake)
    ok, detail = canary.check_sovereign()
    assert ok is True and ":8765" in detail and "7" in detail


def test_sovereign_all_ports_dead_is_unreachable(monkeypatch):
    def fake(url, timeout=8.0):
        raise OSError("refused")

    monkeypatch.setattr(canary, "_http_json", fake)
    ok, detail = canary.check_sovereign()
    assert ok is False and "unreachable" in detail


def test_sovereign_answering_not_ok_is_a_failure(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: {"ok": False})
    ok, detail = canary.check_sovereign()
    assert ok is False and "ok=False" in detail


# ── check_voice / check_mail ─────────────────────────────────────────────────

@pytest.mark.parametrize("status", [None, "", "down"])
def test_voice_dead_statuses_fail(monkeypatch, status):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: {"status": status})
    ok, _ = canary.check_voice()
    assert ok is False


def test_voice_listening_passes(monkeypatch):
    monkeypatch.setattr(canary, "_http_json",
                        lambda url, timeout=8.0: {"status": "listening"})
    ok, detail = canary.check_voice()
    assert ok is True and "listening" in detail


def test_mail_creds_gate(monkeypatch):
    monkeypatch.setattr("utah.mail.creds_available", lambda: True)
    assert canary.check_mail()[0] is True
    monkeypatch.setattr("utah.mail.creds_available", lambda: False)
    ok, detail = canary.check_mail()
    assert ok is False and "gmail.json" in detail


# ── run_scheduled: paging crash must never take the canary down ───────────────

def test_alert_dispatch_crash_never_raises(monkeypatch):
    monkeypatch.setattr(canary.failures, "record", lambda *a: None)
    import utah.alerts as alerts_mod

    def boom(*a, **k):
        raise RuntimeError("pushover down")

    monkeypatch.setattr(alerts_mod, "critical_async", boom)
    r = canary.run_scheduled({"x": lambda: (False, "bad")})
    assert r["ok"] is False and r["failed"] == ["x"]
