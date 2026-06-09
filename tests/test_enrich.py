"""Email enrichment — turn a lead into a real business email (audit §1.1 revenue ceiling).

The pure pieces (regex extract, junk-filter, best-pick) are unit-tested with no network;
the orchestration (find_email, run_scheduled) is tested with injected fetch/search/verify so
no real HTTP/DNS is hit. This is the supply engine that lifts the emailable pool past ~22.
"""
from __future__ import annotations

from utah.product import enrich


# --- pure extraction -------------------------------------------------------

def test_extract_emails_pulls_real_addresses():
    html = ('<a href="mailto:Info@JoesPlumbing.com">email us</a> '
            'or reach owner@joesplumbing.com for quotes')
    got = enrich.extract_emails(html)
    assert "info@joesplumbing.com" in got        # lowercased, mailto + text
    assert "owner@joesplumbing.com" in got


def test_extract_emails_drops_junk():
    html = ("noreply@joes.com privacy@joes.com sprite@2x.png logo.png@cdn.com "
            "real@joesdiner.com user@wixpress.com user@sentry.io")
    got = enrich.extract_emails(html)
    assert got == ["real@joesdiner.com"]          # noreply/privacy/image/placeholder all dropped


def test_extract_emails_drops_placeholder_locals():
    # real-data bug: a 'username@frontier.com' template string scraped off a page
    html = "Sign in with username@frontier.com or your-email@example.com — real biz: shop@acme.com"
    assert enrich.extract_emails(html) == ["shop@acme.com"]


def test_best_email_prefers_contact_inbox():
    cands = ["bob.smith@biz.com", "info@biz.com", "jane@biz.com"]
    assert enrich.best_email(cands) == "info@biz.com"
    assert enrich.best_email(["jane@biz.com", "bob@biz.com"]) == "jane@biz.com"   # else first
    assert enrich.best_email([]) is None


# --- find_email orchestration (injected fetch/search/verify, no network) ----

def test_find_email_scrapes_known_website():
    lead = {"name": "Joe's Diner", "region": "Newnan GA",
            "contact": {"website": "https://joesdiner.com"}}
    pages = {"https://joesdiner.com": '<a href="mailto:hello@joesdiner.com">contact</a>'}
    r = enrich.find_email(lead, fetch_html=lambda u: pages.get(u, ""),
                          verify_fn=lambda domain: True)
    assert r["email"] == "hello@joesdiner.com"
    assert r["source_url"] == "https://joesdiner.com"


def test_find_email_searches_when_no_website():
    lead = {"name": "Ray's Nursery", "region": "Coweta GA", "contact": {}}
    searched = {}

    def fake_search(q, k):
        searched["q"] = q
        return [("Ray's Nursery - Facebook", "https://facebook.com/raysnursery")]

    pages = {"https://facebook.com/raysnursery": "Contact: raysnursery@gmail.com"}
    r = enrich.find_email(lead, search_fn=fake_search,
                          fetch_html=lambda u: pages.get(u, ""), verify_fn=lambda d: True)
    assert r["email"] == "raysnursery@gmail.com"
    assert "Ray's Nursery" in searched["q"]                # searched by name


def test_entity_match_keeps_on_name_token_rejects_strangers():
    # real-data bug: blind search grabbed a UGA staffer for "University ACE" and a personal
    # Frontier email for "Weagle One Stop" — neither shares a business-name token.
    assert enrich._entity_match("thachhutlaundry@gmail.com", "Thach Hut Coin-op Laundry") is True
    assert enrich._entity_match("greg@frontierautosalesga.com", "Best Auto Sales") is True
    assert enrich._entity_match("jandwglass@outlook.com", "J&W Glass Co. LLC") is True
    assert enrich._entity_match("andrew.long@uga.edu", "University ACE") is False
    assert enrich._entity_match("chrissy.murray@ftr.com", "Weagle One Stop") is False


def test_find_email_rejects_wrong_entity_match():
    """An email that doesn't share any business-name token is discarded (wrong business)."""
    lead = {"name": "Weagle One Stop", "region": "GA", "contact": {}}
    pages = {"https://x": "questions? chrissy.murray@ftr.com"}
    r = enrich.find_email(lead, search_fn=lambda q, k: [("x", "https://x")],
                          fetch_html=lambda u: pages.get(u, ""), verify_fn=lambda d: True)
    assert r["email"] is None


def test_find_email_rejects_unresolvable_domain():
    lead = {"name": "Ghost Co", "region": "GA", "contact": {"website": "https://ghost.co"}}
    pages = {"https://ghost.co": "reach us at sales@nonexistent-xyz.invalid"}
    r = enrich.find_email(lead, fetch_html=lambda u: pages.get(u, ""),
                          verify_fn=lambda domain: False)   # domain doesn't resolve
    assert r["email"] is None


# --- run_scheduled writes enriched emails back to the ledger ----------------

def test_run_scheduled_enriches_and_writes():
    updated = []

    class FakeLedger:
        def update_lead(self, name, region, *, contact=None):
            updated.append((name, region, contact))
            return True

    leads = [
        {"name": "A Co", "region": "GA", "contact": {"website": "https://a.co"}},
        {"name": "B Co", "region": "GA", "contact": {"phone": "555"}},
    ]

    def fake_find(lead, **kw):
        return {"email": "found@a.co"} if lead["name"] == "A Co" else {"email": None}

    r = enrich.run_scheduled(ledger=FakeLedger(), lead_fetch=lambda limit: leads,
                             find_fn=fake_find, limit=10)
    assert r["enriched"] == 1
    assert updated == [("A Co", "GA", {"email": "found@a.co"})]
