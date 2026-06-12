"""Trading hardening — run() honors its never-raises contract on a failing ledger,
the bars-table reads are BOUNDED (connect + statement timeout), and the dash-config
read survives a corrupt file. The DB-boundary tests stub psycopg.connect (the driver
dependency, never the module under test) to capture the connection kwargs."""
from __future__ import annotations

import json

import pytest

from utah import failures
from utah.product import trading
from tests.fakes import FakeFailureStore


def _breakout_closes():
    return [100.0] * 20 + [105.0]


class _ExplodingLedger:
    """record_fire raises — a dead Postgres mid-fire must not crash the cron."""

    def fire_state(self, engine):
        return {"open": False, "last_fire_age_s": None}

    def record_fire(self, *a, **kw):
        raise RuntimeError("pg down mid-fire")


def test_run_never_raises_when_record_fire_fails():
    store = FakeFailureStore(); failures.set_store(store)
    r = trading.run(_ExplodingLedger(), feed_fn=_breakout_closes)
    assert r["fires"] == 0 and "error" in r
    assert any("record_failed" in row[2] for row in store.rows)


class _BadStateLedger:
    """fire_state raises — the storm guard's state read must fail SAFE (no fire),
    never crash and never fire blind into an unknown position state."""

    def __init__(self):
        self.fires = []

    def fire_state(self, engine):
        raise RuntimeError("state query timeout")

    def record_fire(self, *a, **kw):
        self.fires.append(a)
        return 1


def test_run_fire_state_failure_fails_safe_no_fire_no_raise():
    store = FakeFailureStore(); failures.set_store(store)
    lg = _BadStateLedger()
    r = trading.run(lg, feed_fn=_breakout_closes)
    assert r["fires"] == 0 and "error" in r
    assert lg.fires == []                     # never fires blind into unknown state
    assert any("state_failed" in row[2] for row in store.rows)


class _FakeCursor:
    def __init__(self):
        self._rows = []

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **kw):
        return _FakeCursor()


def test_ohlc_bars_db_read_is_bounded(monkeypatch):
    """The bars read must carry connect_timeout + statement_timeout — an unbounded
    psycopg.connect on a stalled Postgres hung the apex-dash builder forever."""
    import psycopg

    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        return _FakeConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert trading._ohlc_bars("CM.NQM6") == []
    assert seen.get("connect_timeout"), "no connect_timeout on the bars read"
    assert "statement_timeout" in seen.get("options", "")


def test_busiest_symbol_db_read_is_bounded(monkeypatch):
    import psycopg

    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        return _FakeConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert trading._busiest_symbol() is None
    assert seen.get("connect_timeout"), "no connect_timeout on the busiest-symbol read"
    assert "statement_timeout" in seen.get("options", "")


def test_dash_config_corrupt_file_is_honest_empty(tmp_path, monkeypatch):
    cfg = tmp_path / "apex_dash.json"
    cfg.write_text("{not json !!!")
    monkeypatch.setattr(trading, "APEX_DASH_CONFIG", cfg)
    assert trading.dash_config() == {}


def test_dash_config_wrong_shape_is_honest_empty(tmp_path, monkeypatch):
    cfg = tmp_path / "apex_dash.json"
    cfg.write_text(json.dumps(["a", "list", "not", "a", "dict"]))
    monkeypatch.setattr(trading, "APEX_DASH_CONFIG", cfg)
    assert trading.dash_config() == {}


def test_fire_cooldown_env_default_is_15_minutes():
    """The storm fix (749 fires/day, 2026-06-10) hangs off this constant."""
    assert trading.FIRE_COOLDOWN_S >= 60.0


def test_fire_context_zero_risk_window_has_no_target():
    """Entry exactly at the structural stop → risk 0 → target must be None (a 0R
    target would be the entry itself, a meaningless alert)."""
    closes = [100.0] * 21
    ctx = trading._fire_context(closes, {"engine": "breakout", "direction": "long",
                                         "entry": 100.0})
    assert ctx["target"] is None and ctx["stop"] == 100.0


def test_fire_context_short_direction_math():
    closes = [100.0] * 20 + [95.0]
    ctx = trading._fire_context(closes, {"engine": "breakout", "direction": "short",
                                         "entry": 95.0})
    assert ctx["stop"] == 100.0                  # structural stop = window high for a short
    assert ctx["target"] == pytest.approx(85.0)  # 2R below entry
