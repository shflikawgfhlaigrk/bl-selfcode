"""Outcome gate (B12) partial-assessability: ONE readable ledger is enough to assess
(the `sales` table doesn't exist until Stripe is wired — that must not make the gate
permanently unassessable), and the honest direction holds: unassessable is never 'ok'.
The DB probe is injected via the module seam; its own SQL is proven in test_revenue_heal_db."""
from __future__ import annotations

from utah import revenue_heal


def _patch_counts(monkeypatch, table_counts: dict):
    monkeypatch.setattr(revenue_heal, "_count_since",
                        lambda table, hours: table_counts.get(table))


def test_gate_assessable_when_only_sends_readable(monkeypatch):
    _patch_counts(monkeypatch, {"mail_ledger": 3, "sales": None})   # sales table absent
    g = revenue_heal.outcome_gate()
    assert g["assessable"] is True and g["ok"] is False   # sends ≠ revenue (J-021)
    assert g["sends"] == 3 and g["sales"] == 0
    assert "activity only" in g["reason"]


def test_gate_assessable_when_only_sales_readable(monkeypatch):
    _patch_counts(monkeypatch, {"mail_ledger": None, "sales": 1})
    g = revenue_heal.outcome_gate()
    assert g["assessable"] is True and g["ok"] is True and g["sales"] == 1


def test_gate_not_ok_on_zero_outcomes_with_honest_reason(monkeypatch):
    _patch_counts(monkeypatch, {"mail_ledger": 0, "sales": 0})
    g = revenue_heal.outcome_gate(window_h=26.0)
    assert g["ok"] is False and g["assessable"] is True
    assert "$0" in g["reason"] and "26" in g["reason"]


def test_is_revenue_green_false_when_unassessable(monkeypatch):
    _patch_counts(monkeypatch, {})                # both ledgers unreachable
    assert revenue_heal.is_revenue_green() is False   # never green on a blind read


def test_is_revenue_green_false_on_sends_without_sales(monkeypatch):
    _patch_counts(monkeypatch, {"mail_ledger": 2, "sales": 0})
    assert revenue_heal.is_revenue_green() is False


def test_window_override_is_threaded_through(monkeypatch):
    seen = []
    monkeypatch.setattr(revenue_heal, "_count_since",
                        lambda table, hours: seen.append((table, hours)) or 0)
    revenue_heal.outcome_gate(window_h=4.0)
    assert seen == [("mail_ledger", 4.0), ("sales", 4.0)]


def test_scan_survives_one_exploding_producer(monkeypatch):
    """One producer's probe crashing must not abort the sweep for the others."""
    def age(table):
        if table == "leads":
            raise RuntimeError("probe exploded")
        return 999.0                                 # probate + sent: dark

    filed = []
    actions = revenue_heal.scan(age_fn=age,
                                file_fn=lambda d, t: filed.append(t) or {"filed": True},
                                record_fn=lambda *a: None)
    assert {a["producer"] for a in actions} == {"probate", "sent"}
    assert len(filed) == 2
