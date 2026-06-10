"""Tests for browser discovery → finding pages → task queue."""
from __future__ import annotations

import json

from utah import sica_discover


def test_write_finding_parses_task_and_writes_page(tmp_path):
    log = tmp_path / "discoveries.jsonl"
    fdir = tmp_path / "findings"
    brief = "## Observed\nDeck shows DORMANT marketing.\n\nTASK: Fix the marketing panel label."
    rec = sica_discover.write_finding(
        "frontend", "rendered deck", brain_fn=lambda p: brief,
        findings_dir=fdir, log_path=log,
    )
    assert rec["suggested_task"] == "Fix the marketing panel label."
    page = fdir / f"{int(rec['ts'])}-frontend.md"
    assert page.exists()
    assert "DORMANT" in page.read_text()
    assert json.loads(log.read_text().strip())["domain"] == "frontend"


def test_next_pending_skips_used(tmp_path):
    log = tmp_path / "discoveries.jsonl"
    used = tmp_path / "used.json"
    rec = {"ts": 1.0, "domain": "frontend", "suggested_task": "Add a test", "brief_path": "/x"}
    log.write_text(json.dumps(rec) + "\n")
    got = sica_discover.next_pending_task(log_path=log, used_path=used)
    assert got == ("frontend", "Add a test", rec)
    sica_discover.mark_used(rec, used_path=used)
    assert sica_discover.next_pending_task(log_path=log, used_path=used) is None


def test_run_discover_writes_both_browser_domains(tmp_path, monkeypatch):
    from utah import selfcode

    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "off")
    log = tmp_path / "discoveries.jsonl"
    fdir = tmp_path / "findings"
    out = sica_discover.run_discover(
        brain_fn=lambda p: f"## Observed\n{p[:20]}\n\nTASK: Do one safe thing.",
        gather_fn=lambda d: f"signals for {d}",
        findings_dir=fdir,
        log_path=log,
    )
    assert out["count"] == 2
    assert len(list(fdir.glob("*.md"))) == 2


def test_file_task_edge_triggers_selfcode_via_sentinel(tmp_path, monkeypatch):
    """B12b: a filed repair bumps the selfcode.trigger sentinel so the EDGE-TRIGGERED
    launchd job fires one cycle (no 24/7 KeepAlive loop). The cycle's own discovery does
    not touch it, so the job never self-retriggers."""
    trigger = tmp_path / "selfcode.trigger"
    monkeypatch.setattr(sica_discover, "TRIGGER_PATH", trigger)
    log = tmp_path / "discoveries.jsonl"
    # injected log_path is a test fixture → no trigger; the real (default) path touches it
    sica_discover.file_task("revenue", "fix the sender", log_path=log)
    assert not trigger.exists()
    monkeypatch.setattr(sica_discover, "DISCOVERIES_LOG", log)
    monkeypatch.setattr(sica_discover, "USED_PATH", tmp_path / "used.json")
    sica_discover.file_task("revenue", "fix the sender for real")   # default path → trigger
    assert trigger.exists()
