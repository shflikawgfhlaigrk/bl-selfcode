"""B12: the OUTCOME gate — the post-mortem's rule #1 ('measure outcomes, not activity')
put in the machine. The system is NOT 'green' while $0 is moving, no matter how busy it
is, and a send-drought files a self-code repair against the SENDER (not a scraper)."""
from __future__ import annotations

import pytest

from utah import failures, revenue_heal
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    failures.set_store(FakeFailureStore())
    yield


def test_outcome_gate_red_when_no_sends_or_sales(monkeypatch):
    monkeypatch.setattr(revenue_heal, "_count_since", lambda table, hours: 0)
    g = revenue_heal.outcome_gate()
    assert g["ok"] is False and g["assessable"] is True
    assert g["sends"] == 0 and "NOT done" in g["reason"]
    assert revenue_heal.is_revenue_green() is False


def test_outcome_gate_green_when_a_real_send_landed(monkeypatch):
    monkeypatch.setattr(revenue_heal, "_count_since",
                        lambda table, hours: 3 if table == "mail_ledger" else 0)
    g = revenue_heal.outcome_gate()
    assert g["ok"] is True and g["sends"] == 3 and "flowing" in g["reason"]
    assert revenue_heal.is_revenue_green() is True


def test_outcome_gate_green_when_a_sale_landed(monkeypatch):
    monkeypatch.setattr(revenue_heal, "_count_since",
                        lambda table, hours: 1 if table == "sales" else 0)
    assert revenue_heal.outcome_gate()["ok"] is True


def test_outcome_gate_unassessable_when_db_unreachable(monkeypatch):
    monkeypatch.setattr(revenue_heal, "_count_since", lambda table, hours: None)
    g = revenue_heal.outcome_gate()
    assert g["ok"] is False and g["assessable"] is False   # honest: never claims $0 blindly


def test_sent_producer_files_a_repair_against_the_sender(monkeypatch):
    """A send-drought (mail_ledger stale) files a repair against outreach.py with a
    conversion-focused task, not a scraper task."""
    filed = []
    record = []
    # leads/probate healthy; only 'sent' is stale
    ages = {"leads": 1.0, "probate": 1.0, "mail_ledger": 99.0}
    actions = revenue_heal.scan(
        age_fn=lambda table: ages.get(table, 1.0),
        file_fn=lambda dom, task: (filed.append(task) or {"filed": True}),
        record_fn=lambda *a, **k: record.append(a),
    )
    assert any(a["producer"] == "sent" for a in actions)
    sent_task = next(t for t in filed if "OUTCOME OUTAGE" in t)
    assert "mail_ledger" in sent_task and "outreach.py" in sent_task and "fake a send" in sent_task


def test_sent_producer_silent_when_sends_are_fresh(monkeypatch):
    actions = revenue_heal.scan(
        age_fn=lambda table: 1.0,            # everything fresh
        file_fn=lambda dom, task: {"filed": True},
        record_fn=lambda *a, **k: None,
    )
    assert not any(a["producer"] == "sent" for a in actions)
