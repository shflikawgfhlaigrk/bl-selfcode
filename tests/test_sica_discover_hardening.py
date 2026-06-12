"""sica_discover boundary hardening — the findings queue feeds revenue_heal,
sica_selfaudit, the console, and the autonomous cycle. Every queue operation
(file_task / mark_used / harvest / list) must be a never-raises boundary that
reports failure honestly instead of crashing the caller's cron.
"""
from __future__ import annotations

import json

from utah import sica_discover


def _blocked(tmp_path):
    """A path whose PARENT is a regular file — mkdir/open under it must fail."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    return blocker / "queue.jsonl"


# ── file_task: never-raises, dedup, honest reasons ───────────────────────────
def test_file_task_empty_is_rejected(tmp_path):
    out = sica_discover.file_task("revenue", "   ", log_path=tmp_path / "q.jsonl")
    assert out == {"filed": False, "reason": "empty task"}


def test_file_task_dedupes_identical_pending(tmp_path):
    log = tmp_path / "q.jsonl"
    used = tmp_path / "used.json"
    first = sica_discover.file_task("revenue", "fix the sender", log_path=log, used_path=used)
    assert first["filed"] is True
    again = sica_discover.file_task("revenue", "fix the sender", log_path=log, used_path=used)
    assert again["filed"] is False and again["reason"] == "already pending"
    assert len(log.read_text().splitlines()) == 1            # ONE repair, not one per sweep


def test_file_task_queue_write_failure_is_honest_not_a_crash(tmp_path):
    """revenue_heal calls file_task inside its sweep — a broken queue dir must
    come back as {filed: False, reason}, never an OSError into the cron."""
    out = sica_discover.file_task("revenue", "fix it", log_path=_blocked(tmp_path),
                                  used_path=tmp_path / "used.json")
    assert out["filed"] is False
    assert "queue" in out["reason"] or "write" in out["reason"].lower()


# ── mark_used: never-raises ──────────────────────────────────────────────────
def test_mark_used_survives_unwritable_used_path(tmp_path):
    rec = {"ts": 1.0, "domain": "frontend", "suggested_task": "t"}
    sica_discover.mark_used(rec, used_path=_blocked(tmp_path))  # must not raise


def test_mark_used_persists_and_skips_on_reread(tmp_path):
    log = tmp_path / "q.jsonl"
    used = tmp_path / "used.json"
    rec = {"ts": 2.0, "domain": "leads", "suggested_task": "do x", "brief_path": ""}
    log.write_text(json.dumps(rec) + "\n")
    sica_discover.mark_used(rec, used_path=used)
    assert sica_discover.pending_findings(log_path=log, used_path=used) == []


# ── list_findings: corrupt/unreadable logs degrade to [] ─────────────────────
def test_list_findings_skips_corrupt_lines(tmp_path):
    log = tmp_path / "q.jsonl"
    log.write_text('{"ts": 1, "domain": "a", "suggested_task": "x"}\n{broken\n\n'
                   '{"ts": 2, "domain": "b", "suggested_task": "y"}\n')
    rows = sica_discover.list_findings(log_path=log)
    assert [r["domain"] for r in rows] == ["a", "b"]


def test_list_findings_unreadable_path_returns_empty(tmp_path):
    isdir = tmp_path / "q.jsonl"
    isdir.mkdir()                                            # exists, but read_text fails
    assert sica_discover.list_findings(log_path=isdir) == []


# ── write_finding: brain contract violations degrade to None ─────────────────
def test_write_finding_empty_brief_returns_none_and_writes_nothing(tmp_path):
    out = sica_discover.write_finding("frontend", "sig", brain_fn=lambda p: "  ",
                                      findings_dir=tmp_path / "f",
                                      log_path=tmp_path / "q.jsonl")
    assert out is None
    assert not (tmp_path / "f").exists() or not list((tmp_path / "f").glob("*.md"))


def test_write_finding_non_string_brief_returns_none(tmp_path):
    """A brain that hands back a dict/None (transport quirk) must not crash the
    discovery pass with an AttributeError on .strip()."""
    for bad in (None, {"text": "x"}, 42):
        out = sica_discover.write_finding("frontend", "sig", brain_fn=lambda p, b=bad: b,
                                          findings_dir=tmp_path / "f",
                                          log_path=tmp_path / "q.jsonl")
        assert out is None


def test_write_finding_unwritable_findings_dir_returns_none(tmp_path):
    out = sica_discover.write_finding(
        "frontend", "sig", brain_fn=lambda p: "## Observed\nok\n\nTASK: do x",
        findings_dir=_blocked(tmp_path), log_path=tmp_path / "q.jsonl")
    assert out is None                                       # honest: no record, no crash


# ── harvest_failure_findings: never raises, dedupes, honest error key ─────────
def _rows(*pairs):
    class Row:
        def __init__(self, source, kind):
            self.source, self.kind = source, kind
    return [Row(s, k) for s, k in pairs]


def test_harvest_files_a_recurring_failure_once(tmp_path):
    log = tmp_path / "q.jsonl"
    rows = _rows(*[("voice", "deaf")] * 3, ("leads", "fetch"))
    out = sica_discover.harvest_failure_findings(recent_fn=lambda n: rows, log_path=log)
    assert out["harvested"] == 1 and out["scanned"] == 4
    pending = sica_discover.pending_findings(log_path=log, used_path=tmp_path / "u.json")
    assert len(pending) == 1 and "voice/deaf" in pending[0][1]
    # second sweep: same failures → deduped, queue does not spam
    again = sica_discover.harvest_failure_findings(recent_fn=lambda n: rows, log_path=log)
    assert again["harvested"] == 0 and again["skipped"] == 1


def test_harvest_below_min_count_files_nothing(tmp_path):
    out = sica_discover.harvest_failure_findings(
        recent_fn=lambda n: _rows(("voice", "deaf"), ("voice", "deaf")),
        log_path=tmp_path / "q.jsonl", min_count=3)
    assert out["harvested"] == 0


def test_harvest_feed_failure_is_reported_not_raised(tmp_path):
    def boom(n):
        raise RuntimeError("failure feed down")
    out = sica_discover.harvest_failure_findings(recent_fn=boom, log_path=tmp_path / "q.jsonl")
    assert out["harvested"] == 0 and "failure feed down" in out["error"]


def test_harvest_queue_write_failure_never_raises(tmp_path):
    out = sica_discover.harvest_failure_findings(
        recent_fn=lambda n: _rows(*[("voice", "deaf")] * 3),
        log_path=_blocked(tmp_path))
    assert out["harvested"] == 0 and "error" in out          # honest, contained


# ── run_discover: gate + per-domain containment ──────────────────────────────
def test_run_discover_respects_kill_switch(monkeypatch, tmp_path):
    from utah import selfcode
    monkeypatch.setattr(selfcode, "enabled", lambda: False)
    out = sica_discover.run_discover(brain_fn=lambda p: "TASK: x",
                                     findings_dir=tmp_path, log_path=tmp_path / "q.jsonl")
    assert out == {"ran": False, "reason": "kill switch"}


def test_run_discover_one_failing_domain_does_not_abort_the_other(monkeypatch, tmp_path):
    from utah import selfcode
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "off")

    def gather(domain):
        if domain == "frontend":
            raise RuntimeError("chrome crashed")
        return "signals"

    out = sica_discover.run_discover(
        brain_fn=lambda p: "## Observed\nok\n\nTASK: do one thing.",
        gather_fn=gather, findings_dir=tmp_path / "f", log_path=tmp_path / "q.jsonl")
    assert out["ran"] is True and out["count"] == 1          # research still landed
    assert out["findings"][0]["domain"] == "research"
