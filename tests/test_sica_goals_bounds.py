"""sica_goals boundary bounds — the REAL `_db_query` must be fully bounded
(connect AND statement timeouts, read-only), the cycle counter must survive
corruption, and the rotation/telemetry readers must tolerate garbage records.
"""
from __future__ import annotations

import pytest

from utah import sica_goals


class _FakeCursor:
    def __init__(self):
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self._sql = sql

    def fetchall(self):
        if "GROUP BY source" in self._sql:
            return [("osm", 3)]
        return [(7,)]


class _FakeConn:
    def __init__(self):
        self.read_only = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor()


def test_db_query_is_bounded_and_read_only(monkeypatch):
    """The live signal reader must never be able to hang the autonomous cycle:
    connect_timeout bounds a dead cluster, statement_timeout bounds a slow query,
    and read_only pins that signal-gathering can never mutate the ledger."""
    import psycopg

    seen = {}
    conn = _FakeConn()

    def fake_connect(dsn, **kw):
        seen["dsn"] = dsn
        seen.update(kw)
        return conn

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    sig = sica_goals._leads_signal()
    assert "total=3" in sig                                   # the fake rows flowed through
    assert seen["connect_timeout"] == 8
    assert "statement_timeout" in seen.get("options", "")     # a slow query cannot block
    assert conn.read_only is True


def test_probate_and_outreach_signals_ride_the_same_bounded_query(monkeypatch):
    import psycopg

    calls = []
    monkeypatch.setattr(psycopg, "connect",
                        lambda dsn, **kw: calls.append(kw) or _FakeConn())
    assert "probate total=7" in sica_goals._probate_signal()
    assert "outreach: leads=7" in sica_goals._outreach_signal()
    assert calls and all("statement_timeout" in c.get("options", "") for c in calls)


# ── cycle counter resilience ──────────────────────────────────────────────────
def test_next_cycle_index_recovers_from_corrupt_counter(tmp_path, monkeypatch):
    counter = tmp_path / "n.json"
    monkeypatch.setattr(sica_goals, "CYCLE_N", counter)
    counter.write_text("{definitely not json")
    assert sica_goals.next_cycle_index() == 0                 # corrupt → restart at 0
    assert sica_goals.next_cycle_index() == 1                 # …and persists again


def test_next_cycle_index_recovers_from_wrong_shape(tmp_path, monkeypatch):
    counter = tmp_path / "n.json"
    monkeypatch.setattr(sica_goals, "CYCLE_N", counter)
    counter.write_text('["a", "list"]')                       # json, wrong shape
    assert sica_goals.next_cycle_index() == 0


# ── rotation telemetry: garbage records never break selection ─────────────────
def test_select_domain_tolerates_garbage_cycle_records():
    garbage = [None, "str", 42, {"no_domain": 1}, {"domain": "leads", "attempts": "nope"},
               {"domain": "leads", "attempts": [None, "x", {"passed": True}]}]
    for n in range(len(sica_goals.DOMAINS)):
        d = sica_goals.select_domain(n, cycles_fn=lambda: garbage)
        assert d in sica_goals.DOMAINS                        # never raises, always picks


def test_is_cold_requires_a_full_streak():
    n = sica_goals.COLD_STREAK_N
    assert sica_goals._is_cold([False] * n, n) is True
    assert sica_goals._is_cold([False] * (n - 1), n) is False     # short history ≠ cold
    assert sica_goals._is_cold([True] + [False] * n, n) is False  # newest pass keeps it warm


# ── signal readers: corruption / unknown-domain fallbacks ─────────────────────
def test_baseline_signal_corrupt_verify_json_degrades_honestly():
    sig = sica_goals._baseline_signal(read_text=lambda p: "{broken json")
    assert "unavailable" in sig and "JSONDecodeError" in sig


def test_unknown_domain_routes_to_baseline():
    sig = sica_goals.gather_signals(
        "no-such-domain", read_text=lambda p: '{"state": "green"}')
    assert "verifier state=green" in sig


def test_autonomy_signal_defensive_on_archive_error():
    class Boom:
        def entries(self):
            raise RuntimeError("archive locked")
    sig = sica_goals._autonomy_signal(archive=Boom())
    assert "unavailable" in sig                               # never raises


@pytest.mark.parametrize("html,expect", [
    ("<div>DORMANT</div><div>dormant</div>", {"DORMANT": 2}),
    ("<div>all healthy</div>", {}),
])
def test_summarize_dom_counts_state_markers(html, expect):
    out = sica_goals._summarize_dom(html)
    assert f"{len(html)} chars rendered" in out
    for k, v in expect.items():
        assert f"'{k}': {v}" in out
