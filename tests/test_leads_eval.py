"""Self-measuring lead quality (audit TIER3): measure the scraper's 'actually no website'
precision so its false-positive rate is a number, not a hope."""
from __future__ import annotations

from utah.product import leads_eval


def test_own_site_detection_ignores_directories_and_social():
    assert leads_eval._looks_like_own_site("https://joesdiner.com/menu", "Joe's Diner") is True
    assert leads_eval._looks_like_own_site("https://facebook.com/joesdiner", "Joe's Diner") is False
    assert leads_eval._looks_like_own_site("https://yelp.com/biz/joes", "Joe's Diner") is False
    assert leads_eval._looks_like_own_site("https://someoneelse.com", "Joe's Diner") is False


def test_has_real_website_uses_injected_search():
    lead = {"name": "Acme Plumbing", "region": "GA"}
    found = leads_eval.has_real_website(
        lead, search_fn=lambda q, k: [("t", "https://acmeplumbing.com")])
    missing = leads_eval.has_real_website(
        lead, search_fn=lambda q, k: [("t", "https://facebook.com/acme")])
    assert found is True and missing is False


def test_measure_precision_counts_false_positives():
    leads = [{"name": "True Negative Co"}, {"name": "Has Site Co"}, {"name": "Also Clean"}]
    # 'Has Site Co' actually has a site → a false positive
    q = leads_eval.measure_precision(
        leads, check_fn=lambda l: l["name"] == "Has Site Co")
    assert q.sampled == 3 and q.false_positives == 1 and q.no_website_confirmed == 2
    assert q.precision == round(2 / 3, 4) and q.leaked == ["Has Site Co"]


def test_empty_sample_is_zeroed():
    q = leads_eval.measure_precision([])
    assert q.sampled == 0 and q.precision == 0.0


def test_search_failure_is_not_a_false_positive():
    def boom(q, k): raise RuntimeError("blocked")
    assert leads_eval.has_real_website({"name": "X", "region": "GA"}, search_fn=boom) is False
