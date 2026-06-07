"""Tests for the SICA goal source: domain rotation + grounded, domain-targeted
task generation (baseline / leads / autonomy), all with injected signals + brain."""
from __future__ import annotations

from utah import sica, sica_goals


def test_domain_rotation_covers_all():
    got = [sica_goals.pick_domain(n) for n in range(8)]
    assert got == ["baseline", "leads", "autonomy", "frontend",
                   "baseline", "leads", "autonomy", "frontend"]


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


def test_frontend_signal_renders_live_deck_and_grounds_in_the_dom():
    """Ace uses its OWN browser: when the live deck renders, its real DOM markers are
    summarized into the prompt so the brain targets a real frontend optimization."""
    def fake_render(url):
        assert url == sica_goals.DASHBOARD_URL
        return {"rendered": True, "html": "<div>DORMANT</div><div>GATED</div> awaiting events…",
                "chars": 48, "url": url}
    sig = sica_goals.gather_signals("frontend", render_fn=fake_render)
    assert "rendered its OWN live deck" in sig
    assert "dormant" in sig.lower()                 # observed marker fed to the brain
    assert "live.html" in sig and "web.py" in sig    # the editable frontend surface


def test_frontend_signal_falls_back_to_the_sim_twin_when_live_wont_settle():
    """The real case: the live deck's persistent SSE stops --dump-dom settling, so the
    browser renders the quiescent /sim twin instead and still grounds the brain — AND
    reports the live render-hang as its own finding."""
    def fake_render(url):
        if url == sica_goals.DASHBOARD_URL:
            return {"rendered": False, "error": "timed out after 8s", "url": url}
        assert url == sica_goals.DASHBOARD_SIM_URL
        return {"rendered": True, "html": "<div class='card'>panels here</div>", "url": url}
    sig = sica_goals.gather_signals("frontend", render_fn=fake_render)
    assert "structural twin" in sig                  # rendered the /sim fallback
    assert "persistent SSE" in sig                   # live render-hang flagged as a finding
    assert "live.html" in sig


def test_frontend_signal_degrades_when_browser_fully_gated():
    """No Chrome at all (both live + twin gated) → a safe generic prompt, never raises."""
    sig = sica_goals.gather_signals(
        "frontend", render_fn=lambda url: {"rendered": False, "gated": True, "url": url})
    assert "unavailable" in sig and "live.html" in sig


def test_frontend_signal_defensive_on_render_error():
    def boom(url):
        raise RuntimeError("chrome crashed")
    sig = sica_goals.gather_signals("frontend", render_fn=boom)
    assert "unavailable" in sig and "live.html" in sig   # never raises into the loop


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
