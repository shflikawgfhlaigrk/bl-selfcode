"""Revenue self-heal — a dark producer queues a self-code repair for Ace; a healthy one
does not; an unassessable one never false-triggers. The filed task is pickable by the SICA
loop, so 'leads don't generate -> Ace fixes his own code' closes end-to-end."""
from __future__ import annotations

from utah import revenue_heal, sica_discover


def test_scan_files_repair_for_dark_producer():
    filed, recs = [], []
    actions = revenue_heal.scan(
        age_fn=lambda table: 99.0 if table == "leads" else 1.0,   # leads dark, probate fresh
        file_fn=lambda domain, task: filed.append((domain, task)) or {"filed": True},
        record_fn=lambda src, kind, detail: recs.append((src, kind, detail)),
    )
    assert any(a["producer"] == "leads" and a["filed"] for a in actions)
    assert not any(a["producer"] == "probate" for a in actions)      # fresh → untouched
    assert filed and "leads.py" in filed[0][1] and "REVENUE OUTAGE" in filed[0][1]
    assert any(kind == "stale_producer" for _, kind, _ in recs)      # documented


def test_scan_no_action_when_healthy():
    actions = revenue_heal.scan(age_fn=lambda t: 1.0,
                                file_fn=lambda d, t: {"filed": True},
                                record_fn=lambda *a: None)
    assert actions == []


def test_scan_never_false_triggers_when_unassessable():
    """Empty/unreachable table (age None) must NOT file a self-code run — that would be a
    perpetual no-op coding loop, the exact failure we're preventing."""
    calls = []
    revenue_heal.scan(age_fn=lambda t: None,
                      file_fn=lambda d, t: calls.append(1) or {"filed": True},
                      record_fn=lambda *a: None)
    assert calls == []


def test_check_producer_zero_yield_files():
    filed = []
    r = revenue_heal.check_producer("leads", {"new": 0, "met": False},
                                    file_fn=lambda d, t: filed.append(t) or {"filed": True},
                                    record_fn=lambda *a: None)
    assert r["healed"] is True and filed and "leads.py" in filed[0]


def test_check_producer_healthy_does_not_file():
    filed = []
    r = revenue_heal.check_producer("leads", {"new": 5},
                                    file_fn=lambda d, t: filed.append(t) or {"filed": True},
                                    record_fn=lambda *a: None)
    assert r["healed"] is False and not filed


def test_check_producer_gated_does_not_file():
    filed = []
    r = revenue_heal.check_producer("leads", {"gated": True, "reason": "feed down"},
                                    file_fn=lambda d, t: filed.append(t) or {"filed": True},
                                    record_fn=lambda *a: None)
    assert r["healed"] is False and not filed


def test_filed_task_is_deduped_and_pickable_by_loop(tmp_path):
    log, used = tmp_path / "disc.jsonl", tmp_path / "used.json"
    a = sica_discover.file_task("revenue", "fix leads generator", log_path=log, used_path=used)
    b = sica_discover.file_task("revenue", "fix leads generator", log_path=log, used_path=used)
    assert a["filed"] is True
    assert b["filed"] is False and b["reason"] == "already pending"   # one repair, not one/sweep
    nxt = sica_discover.next_pending_task(log_path=log, used_path=used)
    assert nxt is not None and nxt[0] == "revenue" and nxt[1] == "fix leads generator"
