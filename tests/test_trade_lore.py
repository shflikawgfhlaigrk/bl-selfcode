"""Think-on-fire — Ace's live read on graded fires, grounded in the migrated corpus.
Michael's ask 2026-06-10: live assessments vs the thousands of logged trades."""
from utah.product import trade_lore


class FakeLedger:
    def __init__(self, fires=None):
        self.fires = fires or []
        self.assessments = {}
        self.lore = []

    def init_schema(self):
        pass

    def fires_missing_assessment(self, limit=3):
        return self.fires[:limit]

    def engine_scorecard(self, engine):
        return {"graded": 37, "wins": 14, "win_rate": 0.378, "net_pnl": -620.5}

    def lore_for(self, engine, limit=5):
        return [{"engine": engine, "kind": "OPEN",
                 "content": "Risk-to-reward looks reasonable; grade NONE — cautious."}]

    def set_fire_assessment(self, fid, text):
        self.assessments[fid] = text
        return True

    def add_lore(self, ts, engine, kind, content, confidence=None):
        key = (engine, ts, kind)
        if key in {(e, t, k) for (e, t, k, _c) in self.lore}:
            return False
        self.lore.append((engine, ts, kind, content))
        return True


FIRE = {"id": 9, "engine": "breakout", "direction": "short", "entry": 28980.25,
        "outcome": "timeout", "pnl": -4.75, "symbol": "CM.NQM6", "ts": None}


def test_assess_fire_grounds_in_ledger_facts_and_keeps_the_disclaimer():
    seen = {}

    def think(q, ctx):
        seen["q"], seen["ctx"] = q, ctx
        return "Timeout short into chop; the record says the edge is thin here."

    text = trade_lore.assess_fire(FIRE, {"graded": 37, "wins": 14, "win_rate": 0.378,
                                         "net_pnl": -620.5},
                                  [{"engine": "breakout", "kind": "OPEN",
                                    "content": "watch the chop"}], think)
    assert "28980.25" in seen["ctx"] and "-620.5" in seen["ctx"]   # real numbers reach the brain
    assert "watch the chop" in seen["ctx"]                          # the historical voice too
    assert text.endswith(trade_lore.DISCLAIMER)                     # framing preserved


def test_assess_fire_never_fabricates_on_refusal_or_failure():
    assert trade_lore.assess_fire(FIRE, {}, [], lambda q, c: "I don't know — no basis.") is None
    def boom(q, c):
        raise RuntimeError("brain down")
    assert trade_lore.assess_fire(FIRE, {}, [], boom) is None


def test_run_assessments_writes_each_read_once_and_is_bounded():
    lg = FakeLedger(fires=[dict(FIRE, id=1), dict(FIRE, id=2), dict(FIRE, id=3),
                           dict(FIRE, id=4)])
    r = trade_lore.run_assessments(lg, limit=3,
                                   think_fn=lambda q, c: "Read. This is data, not a directive.")
    assert r["assessed"] == 3 and set(lg.assessments) == {1, 2, 3}   # bounded at 3


def test_migrate_legacy_dedups_and_survives_missing_db(tmp_path):
    import sqlite3
    db = tmp_path / "ace.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE engine_fire_commentary (fire_ts TEXT, engine TEXT, "
              "fire_kind TEXT, commentary TEXT, confidence REAL)")
    c.executemany("INSERT INTO engine_fire_commentary VALUES (?,?,?,?,?)",
                  [("2026-05-19T11:26:15", "shadow", "OPEN",
                    "Shadow opened flat at 7396.75 — risk-to-reward looks reasonable.", 0.65),
                   ("2026-05-19T11:26:15", "shadow", "OPEN",          # exact dupe
                    "Shadow opened flat at 7396.75 — risk-to-reward looks reasonable.", 0.65),
                   ("2026-05-19T11:29:11", "shadow", "SIGNAL", "too short", 0.5)])  # <20 chars
    c.commit(); c.close()
    lg = FakeLedger()
    r = trade_lore.migrate_legacy(lg, db_path=str(db))
    assert r["migrated"] == 1 and r["skipped"] == 1                 # dedup held, junk filtered
    missing = trade_lore.migrate_legacy(lg, db_path=str(tmp_path / "nope.db"))
    assert "error" in missing and missing["migrated"] == 0
