"""Connectivity probe contracts beyond tests/test_news_connectivity.py:
HTTP-level errors prove the network WORKS, defaults are real, the boundary
never raises, and the probe timeout stays bounded."""
from __future__ import annotations

import urllib.error

from utah import connectivity, failures
from tests.fakes import FakeFailureStore


def test_http_error_response_counts_as_online():
    """A 405/503 means DNS+TCP+HTTP all worked — the box IS online. Counting a
    server's error page as 'unreachable' would fabricate an outage."""
    failures.set_store(FakeFailureStore())

    def fetch(url):
        raise urllib.error.HTTPError(url, 405, "method not allowed", None, None)

    r = connectivity.check(hosts=["https://a.example"], fetch=fetch)
    assert r["hosts"] == {"https://a.example": True}
    assert r["online"] is True


def test_http_error_on_one_host_records_no_outage():
    store = FakeFailureStore()
    failures.set_store(store)

    def fetch(url):
        raise urllib.error.HTTPError(url, 503, "unavailable", None, None)

    connectivity.check(hosts=["https://a.example"], fetch=fetch)
    assert store.rows == []          # server answered → not an outage


def test_default_hosts_are_actually_probed():
    failures.set_store(FakeFailureStore())
    seen: list[str] = []
    r = connectivity.check(fetch=lambda u: seen.append(u))
    assert set(seen) == set(connectivity._HOSTS)
    assert len(connectivity._HOSTS) >= 2     # one flaky host can't fake an outage
    assert r["online"] is True


def test_offline_record_names_the_probed_hosts():
    store = FakeFailureStore()
    failures.set_store(store)

    def fetch(url):
        raise OSError("no route to host")

    r = connectivity.check(hosts=["https://a.example", "https://b.example"], fetch=fetch)
    assert r["online"] is False
    assert any("https://a.example" in row[3] for row in store.rows)


def test_probe_timeout_is_bounded_and_positive():
    assert 0 < connectivity._TIMEOUT_S <= 30
