"""Enrichment boundary hardening — the I/O edges test_enrich.py leaves open.

Search results are UNTRUSTED input, so the fetcher must refuse non-http(s) schemes
outright (a hostile listing can't make the cron read file:// or run javascript:).
The cron boundary (run_scheduled) must never raise into launchd: a dead Postgres is
an explicit ``error`` dict and one bad lead is a counted ``failed``, not a dead batch.
The lead query must ride the shared bounded pool (utah.db_pool), never a raw
unbounded connect."""
from __future__ import annotations

import pytest

from utah.product import enrich


# --- scheme guard: untrusted URLs never reach the fetcher -------------------------

def test_raw_fetch_refuses_non_http_schemes(monkeypatch):
    import urllib.request

    def trap(*a, **kw):
        raise AssertionError("urlopen must not be called for a refused scheme")

    monkeypatch.setattr(urllib.request, "urlopen", trap)
    assert enrich._raw_fetch("file:///etc/passwd") == ""
    assert enrich._raw_fetch("javascript:alert(1)") == ""
    assert enrich._raw_fetch("ftp://old.example.com") == ""
    assert enrich._raw_fetch("") == ""


def test_candidate_urls_drop_non_http_search_results():
    lead = {"name": "Joe's Diner", "region": "GA", "contact": {}}
    results = [("bad", "javascript:alert(1)"), ("worse", "file:///etc/hosts"),
               ("good", "https://joesdiner.com"), ("empty", "")]
    urls = enrich._candidate_urls(lead, lambda q, k: results)
    assert urls == ["https://joesdiner.com"]


# --- the lead query rides the bounded shared pool ----------------------------------

def test_leads_needing_email_reads_through_the_bounded_pool(monkeypatch):
    captured: dict = {}

    class _Conn:
        def execute(self, sql, params=None):
            captured["sql"], captured["params"] = sql, params

            class _R:
                @staticmethod
                def fetchall():
                    return [("A Co", "plumber", "GA", {"phone": "5"}),
                            ("B Co", "salon", "GA", None)]
            return _R()

    class _Checkout:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Checkout()

    from utah import db_pool
    monkeypatch.setattr(db_pool, "get_pool", lambda dsn, **kw: _Pool())
    rows = enrich._leads_needing_email(7)
    assert rows[0] == {"name": "A Co", "kind": "plumber", "region": "GA",
                       "contact": {"phone": "5"}}
    assert rows[1]["contact"] == {}                    # NULL jsonb -> empty dict
    assert "contact->>'email' IS NULL" in captured["sql"]
    assert captured["params"][-1] == 7                 # limit parameterized


# --- run_scheduled: cron boundary never raises --------------------------------------

class _FakeLedger:
    def __init__(self):
        self.updated: list = []

    def update_lead(self, name, region, *, contact=None):
        self.updated.append((name, region, contact))
        return True


def test_run_scheduled_survives_a_dead_lead_fetch():
    def dead_fetch(limit):
        raise RuntimeError("postgres is down")

    r = enrich.run_scheduled(ledger=_FakeLedger(), lead_fetch=dead_fetch,
                             foundation_gate=lambda cap: None)
    assert r["scanned"] == 0 and r["enriched"] == 0
    assert "postgres is down" in r["error"]            # honest, explicit failure


def test_run_scheduled_counts_a_failing_lead_and_finishes_the_batch():
    leads = [{"name": "Bad Co", "region": "GA", "contact": {}},
             {"name": "Good Co", "region": "GA", "contact": {}}]

    def find(lead, **kw):
        if lead["name"] == "Bad Co":
            raise ValueError("scrape exploded")
        return {"email": "owner@goodco.com"}

    ledger = _FakeLedger()
    r = enrich.run_scheduled(ledger=ledger, lead_fetch=lambda limit: leads,
                             find_fn=find, foundation_gate=lambda cap: None)
    assert r == {"scanned": 2, "enriched": 1, "failed": 1}
    assert ledger.updated == [("Good Co", "GA", {"email": "owner@goodco.com"})]


def test_run_scheduled_counts_a_failing_ledger_write_as_failed():
    class _BrokenLedger:
        def update_lead(self, *a, **kw):
            raise RuntimeError("ledger write refused")

    r = enrich.run_scheduled(
        ledger=_BrokenLedger(),
        lead_fetch=lambda limit: [{"name": "A", "region": "GA", "contact": {}}],
        find_fn=lambda lead, **kw: {"email": "a@a.com"},
        foundation_gate=lambda cap: None)
    assert r == {"scanned": 1, "enriched": 0, "failed": 1}


def test_run_scheduled_returns_the_gate_skip_untouched():
    skip = {"status": "substrate_red", "capability": "enrich"}
    assert enrich.run_scheduled(foundation_gate=lambda cap: skip) is skip


# --- small pure edges ----------------------------------------------------------------

def test_verify_email_empty_and_none_like_input_is_bad_syntax():
    assert enrich.verify_email("")["reason"] == "bad-syntax"
    assert enrich.verify_email("   ")["reason"] == "bad-syntax"


def test_registrable_domain_strips_www_and_ports_of_dots():
    assert enrich._registrable_domain("www.joesdiner.com") == "joesdiner.com"
    assert enrich._registrable_domain("shop.example.co") == "example.co"
    assert enrich._registrable_domain("") == ""


def test_domain_accepts_mail_blank_domain_is_false():
    assert enrich.domain_accepts_mail("", mx_lookup=lambda d: ["mx"],
                                      a_lookup=lambda d: True) is False
