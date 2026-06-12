"""Trade-lore hardening — env-overridable legacy path, bounded/leak-free sqlite read,
honest degradation on every failure lane (store down, brain down, one bad fire/row)."""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from utah.product import trade_lore


FIRE = {"id": 7, "engine": "meanrev", "direction": "long", "entry": 101.25,
        "outcome": "win", "pnl": 3.5, "symbol": "CM.NQM6", "ts": None}


def test_legacy_db_path_is_env_overridable(monkeypatch):
    """The legacy corpus location must not be a hardcoded user path — the env knob
    relocates it (test boxes, future machines) without an edit."""
    monkeypatch.setenv("UTAH_LEGACY_ACE_DB", "/tmp/elsewhere/ace.db")
    importlib.reload(trade_lore)
    try:
        assert trade_lore.LEGACY_DB == "/tmp/elsewhere/ace.db"
    finally:
        monkeypatch.delenv("UTAH_LEGACY_ACE_DB")
        importlib.reload(trade_lore)
    assert trade_lore.LEGACY_DB.endswith("ace.db")


def test_prompt_omits_win_rate_when_unknown():
    """A scorecard with win_rate=None (no graded fires yet) must not render a percent —
    no fabricated 0% and no crash on the format spec."""
    seen = {}

    def think(q, ctx):
        seen["ctx"] = ctx
        return "Read. This is data, not a directive."

    text = trade_lore.assess_fire(FIRE, {"graded": 0, "wins": 0, "win_rate": None,
                                         "net_pnl": 0.0}, [], think)
    assert text is not None
    assert "%" not in seen["ctx"].split("HISTORICAL")[0]   # no percent in the scorecard line
    assert "None%" not in seen["ctx"]


def test_disclaimer_appended_when_brain_forgets_it():
    text = trade_lore.assess_fire(FIRE, {}, [], lambda q, c: "Solid fade at the band edge.")
    assert text.endswith(trade_lore.DISCLAIMER)


def test_whitespace_only_brain_reply_is_none():
    assert trade_lore.assess_fire(FIRE, {}, [], lambda q, c: "   \n  ") is None


def test_run_assessments_store_down_reports_error_never_raises():
    class _DeadLedger:
        def fires_missing_assessment(self, limit=3):
            raise RuntimeError("pg down")

    out = trade_lore.run_assessments(_DeadLedger(), limit=3, think_fn=lambda q, c: "x")
    assert out["checked"] == 0 and out["assessed"] == 0
    assert "pg down" in out["error"]


def test_run_assessments_one_bad_write_does_not_abort_the_run():
    class _FlakyLedger:
        def __init__(self):
            self.written = []

        def fires_missing_assessment(self, limit=3):
            return [dict(FIRE, id=i) for i in (1, 2, 3)]

        def engine_scorecard(self, engine):
            return {"graded": 1, "wins": 1, "win_rate": 1.0, "net_pnl": 2.0}

        def lore_for(self, engine):
            return []

        def set_fire_assessment(self, fid, text):
            if fid == 2:
                raise RuntimeError("row lock")
            self.written.append(fid)
            return True

    lg = _FlakyLedger()
    out = trade_lore.run_assessments(lg, limit=3,
                                     think_fn=lambda q, c: "R. This is data, not a directive.")
    assert out["assessed"] == 2 and out["skipped"] == 1
    assert lg.written == [1, 3]


def test_run_assessments_limit_is_clamped():
    class _CountingLedger:
        def __init__(self):
            self.asked = None

        def fires_missing_assessment(self, limit=3):
            self.asked = limit
            return []

    lg = _CountingLedger()
    out = trade_lore.run_assessments(lg, limit=-4, think_fn=lambda q, c: "x")
    assert out == {"checked": 0, "assessed": 0, "skipped": 0}
    assert lg.asked == 0                      # negative caller limit never reaches the store


def test_migrate_legacy_bad_schema_reports_error_and_releases_the_handle(tmp_path):
    db = tmp_path / "ace.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE wrong_table (x TEXT)")
    c.commit(); c.close()

    class _Ledger:
        def init_schema(self):
            pass

    out = trade_lore.migrate_legacy(_Ledger(), db_path=str(db))
    assert "error" in out and out["migrated"] == 0
    # the read-only handle was closed even though the query failed: the file is
    # immediately re-openable for exclusive write without a lock error
    w = sqlite3.connect(db, timeout=0.1)
    w.execute("BEGIN EXCLUSIVE")
    w.execute("ROLLBACK")
    w.close()


def test_migrate_legacy_honors_the_row_limit(tmp_path):
    db = tmp_path / "ace.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE engine_fire_commentary (fire_ts TEXT, engine TEXT, "
              "fire_kind TEXT, commentary TEXT, confidence REAL)")
    c.executemany("INSERT INTO engine_fire_commentary VALUES (?,?,?,?,?)",
                  [(f"2026-05-19T11:0{i}:00", "shadow", "OPEN",
                    f"a real commentary row long enough to keep #{i}", 0.5)
                   for i in range(5)])
    c.commit(); c.close()

    class _Ledger:
        def __init__(self):
            self.rows = []

        def init_schema(self):
            pass

        def add_lore(self, ts, engine, kind, content, confidence=None):
            self.rows.append(ts)
            return True

    lg = _Ledger()
    out = trade_lore.migrate_legacy(lg, db_path=str(db), limit=2)
    assert out["read"] == 2 and out["migrated"] == 2 and len(lg.rows) == 2


def test_assess_per_run_default_is_small():
    """The cron cap exists because every read is a real `claude -p` call."""
    assert 1 <= trade_lore.ASSESS_PER_RUN <= 10
