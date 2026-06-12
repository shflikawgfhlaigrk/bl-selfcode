"""Report-shape edges for maintenance.run beyond the basic hardening file:
a None report, a bool masquerading as a count (bool IS an int in Python — but
``turns_seen=True`` is an upstream bug, not '1 turn'), and the bounded error
string in the RETURN value (not just the failure log)."""
from __future__ import annotations

from types import SimpleNamespace

from utah import failures, maintenance
from tests.fakes import FakeFailureStore


def _report(**kw):
    base = dict(turns_seen=0, facts_promoted=0, facts_skipped=0,
                brain_failures=0, archived=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_none_report_is_malformed_not_attributeerror():
    store = FakeFailureStore()
    failures.set_store(store)
    r = maintenance.run(consolidate_fn=lambda: None)
    assert r["ok"] is False and "malformed" in r["error"]
    assert any(row[2] == "malformed_report" for row in store.rows)


def test_bool_count_is_rejected_as_malformed():
    store = FakeFailureStore()
    failures.set_store(store)
    r = maintenance.run(consolidate_fn=lambda: _report(turns_seen=True))
    assert r["ok"] is False and "malformed" in r["error"]
    assert any(row[2] == "malformed_report" for row in store.rows)


def test_returned_error_is_bounded_like_the_failure_log():
    """The ok=False dict flows to the daemon/deck — a 10K-char error string must
    not ride along; both surfaces share the same 500-char bound."""
    failures.set_store(FakeFailureStore())

    def boom():
        raise RuntimeError("y" * 10_000)

    r = maintenance.run(consolidate_fn=boom)
    assert r["ok"] is False
    assert len(r["error"]) <= 500


def test_extra_report_attributes_are_ignored_not_fatal():
    """consolidate() growing new fields must not break the wrapper."""
    failures.set_store(FakeFailureStore())
    r = maintenance.run(consolidate_fn=lambda: _report(turns_seen=1, new_field="x"))
    assert r["ok"] is True and r["turns_seen"] == 1
