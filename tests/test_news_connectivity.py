"""News (researcher wrapper) + connectivity (network probe)."""
from __future__ import annotations

from utah import connectivity, failures
from utah.product import news
from tests.fakes import FakeFailureStore


def test_news_routes_a_news_query_to_research():
    seen = {}
    def fake_research(q, k=3):
        seen["q"] = q; seen["k"] = k
        return {"query": q, "sources": 2, "facts": 5, "stored": 5}
    r = news.headlines("pgvector", k=2, research=fake_research)
    assert "latest news about pgvector" in seen["q"] and seen["k"] == 2
    assert r["stored"] == 5


def test_connectivity_online_when_any_host_up():
    failures.set_store(FakeFailureStore())
    def fetch(url):
        if "google" in url:
            raise RuntimeError("down")     # one host down...
        return None                        # ...the other up
    r = connectivity.check(fetch=fetch)
    assert r["online"] is True


def test_connectivity_partial_outage_records_nothing_and_maps_each_host():
    store = FakeFailureStore(); failures.set_store(store)
    def fetch(url):
        if "google" in url:
            raise RuntimeError("down")     # one of the two custom hosts is down
        return None
    r = connectivity.check(hosts=["https://google.com", "https://up.example"], fetch=fetch)
    # the returned map reflects per-host reachability...
    assert r["hosts"] == {"https://google.com": False, "https://up.example": True}
    # ...and a single host being down is NOT an outage, so nothing is documented
    assert store.rows == []


def test_connectivity_offline_is_documented():
    store = FakeFailureStore(); failures.set_store(store)
    def fetch(url):
        raise RuntimeError("no route")
    r = connectivity.check(fetch=fetch)
    assert r["online"] is False
    assert any("offline" in row[2] for row in store.rows)
