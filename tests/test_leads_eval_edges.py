"""Lead-quality eval edges — own-site heuristics under hostile names/URLs, the
low-precision alarm, and the honest degrade when the lead store is unreachable.

run_eval is a cron-shaped boundary: a dead Postgres must yield a zeroed, honest
result with an ``error`` (and a recorded failure) — never a raise, never a
fabricated precision number.
"""
from __future__ import annotations

import pytest

from utah import failures
from utah.product import leads_eval
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


# --- _looks_like_own_site -------------------------------------------------------------

def test_no_url_or_schemeless_text_is_not_a_site():
    assert leads_eval._looks_like_own_site("", "Joe's Diner") is False
    assert leads_eval._looks_like_own_site("joesdiner.com", "Joe's Diner") is False


def test_stopword_only_name_never_matches_a_host():
    """'The Shop LLC' has no distinctive 4+ char token after stopword filtering —
    a random host must not be miscounted as their own site."""
    assert leads_eval._looks_like_own_site("https://randomhost.com", "The Shop LLC") is False


def test_name_token_must_appear_in_the_host():
    assert leads_eval._looks_like_own_site(
        "https://www.foxtailcoffee.com/about", "Foxtail Coffee Co.") is True
    assert leads_eval._looks_like_own_site(
        "https://newnanbusinessdirectory.com", "Foxtail Coffee Co.") is False


def test_directory_and_social_hosts_never_count_even_with_name_match():
    assert leads_eval._looks_like_own_site(
        "https://www.facebook.com/foxtailcoffee", "Foxtail Coffee") is False
    assert leads_eval._looks_like_own_site(
        "https://m.yelp.com/biz/foxtail-coffee", "Foxtail Coffee") is False


# --- measure_precision ------------------------------------------------------------------

def test_measure_precision_handles_none_input():
    q = leads_eval.measure_precision(None)
    assert q.sampled == 0 and q.precision == 0.0 and q.leaked == []


def test_measure_precision_rounds_to_4_decimals():
    leads = [{"name": f"Biz {i}"} for i in range(7)]
    q = leads_eval.measure_precision(leads, check_fn=lambda l: l["name"] == "Biz 0")
    assert q.precision == round(6 / 7, 4)
    assert q.false_positive_rate == round(1 / 7, 4)


# --- run_eval: alarm + honest store degrade ----------------------------------------------

class _Ledger:
    def __init__(self, rows):
        self.rows = rows

    def leads_missing_email(self, n):
        return self.rows[:n]


def test_run_eval_good_precision_records_no_alarm(_store):
    lg = _Ledger([{"name": "Clean A"}, {"name": "Clean B"}, {"name": "Clean C"},
                  {"name": "Clean D"}])
    out = leads_eval.run_eval(ledger=lg, check_fn=lambda l: False)
    assert out["sampled"] == 4 and out["precision"] == 1.0
    assert not any(row[2] == "low_precision" for row in _store.rows)


def test_run_eval_low_precision_raises_the_alarm(_store):
    lg = _Ledger([{"name": "Leak A"}, {"name": "Leak B"}, {"name": "Clean C"},
                  {"name": "Clean D"}])
    out = leads_eval.run_eval(ledger=lg, check_fn=lambda l: l["name"].startswith("Leak"))
    assert out["false_positive_rate"] == 0.5
    assert any(row[2] == "low_precision" for row in _store.rows)


def test_run_eval_is_honest_when_lead_store_is_dead(_store):
    class DeadLedger:
        def leads_missing_email(self, n):
            raise RuntimeError("pg down")

    out = leads_eval.run_eval(ledger=DeadLedger())
    assert out["sampled"] == 0 and out["precision"] == 0.0
    assert "pg down" in out["error"]
    assert any(row[2] == "eval_store_unreachable" for row in _store.rows)


def test_run_eval_respects_sample_size():
    lg = _Ledger([{"name": f"B{i}"} for i in range(50)])
    out = leads_eval.run_eval(sample=10, ledger=lg, check_fn=lambda l: False)
    assert out["sampled"] == 10
