"""Tests for the SICA goal source: domain rotation + grounded, domain-targeted
task generation (baseline / leads / autonomy), all with injected signals + brain."""
from __future__ import annotations

from utah import sica, sica_goals


def test_domain_rotation_covers_all():
    got = [sica_goals.pick_domain(n) for n in range(6)]
    assert got == ["baseline", "leads", "autonomy", "baseline", "leads", "autonomy"]


def test_leads_signal_uses_live_ledger():
    def fake_db(sql):
        if "GROUP BY source" in sql:
            return [("osm", 962), ("probate", 0)]
        if "contact" in sql:
            return [(120,)]
        if "FROM probate" in sql:
            return [(0,)]
        return []
    sig = sica_goals.gather_signals("leads", db_query=fake_db)
    assert "total=962" in sig and "osm=962" in sig and "with_contact=120" in sig
    assert "leads.py" in sig


def test_leads_signal_defensive_on_db_error():
    def boom(sql):
        raise RuntimeError("no db")
    sig = sica_goals.gather_signals("leads", db_query=boom)
    assert "unavailable" in sig and "leads.py" in sig   # never raises


def test_baseline_signal_reads_verifier():
    sig = sica_goals.gather_signals(
        "baseline", read_text=lambda p: '{"state": "green", "detail": "all pass"}')
    assert "state=green" in sig and "coverage" in sig.lower()


def test_autonomy_signal_summarizes_archive(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    arch.record(sica.make_attempt(task="t", branch="b", tier="A", passed=True,
                                  output="2 passed", cost_usd=0, elapsed_s=1,
                                  timed_out=False, merged=True, sha="s", reason="ok"))
    sig = sica_goals.gather_signals("autonomy", archive=arch)
    assert "1 attempts" in sig and "sica_loop.py" in sig and "Tier-D" in sig


def test_next_task_feeds_domain_and_signals_to_brain():
    seen = {}

    def brain(prompt):
        seen["prompt"] = prompt
        return "  expand the OSM lead frontier to two more counties  "

    task = sica_goals.next_task("leads", brain_fn=brain,
                                db_query=lambda sql: [("osm", 962)] if "GROUP" in sql else [(0,)])
    assert task == "expand the OSM lead frontier to two more counties"   # stripped
    assert "Domain THIS cycle: leads" in seen["prompt"]
    assert "NEVER edit the safety core" in seen["prompt"]


def test_next_cycle_index_increments(tmp_path, monkeypatch):
    monkeypatch.setattr(sica_goals, "CYCLE_N", tmp_path / "n.json")
    a = sica_goals.next_cycle_index()
    b = sica_goals.next_cycle_index()
    assert a == 0 and b == 1
