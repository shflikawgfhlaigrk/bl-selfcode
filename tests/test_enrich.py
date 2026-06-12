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


def test_find_email_accepts_own_website_domain_without_name_token():
    """Scraping the lead's own site: info@ domain match counts even when name tokens miss."""
    lead = {"name": "The Spot", "region": "GA", "contact": {"website": "https://thespotga.com"}}
    pages = {
        "https://thespotga.com": '<a href="mailto:info@thespotga.com">email</a>',
    }
    r = enrich.find_email(lead, fetch_html=lambda u: pages.get(u, ""),
                          verify_fn=lambda domain: domain == "thespotga.com")
    assert r["email"] == "info@thespotga.com"


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
                             find_fn=fake_find, limit=10,
                             foundation_gate=lambda cap: None)   # substrate green (hermetic)
    assert r["enriched"] == 1
    assert updated == [("A Co", "GA", {"email": "found@a.co"})]


# --- deliverability gate: a domain must ACCEPT mail (MX), not just resolve ---
# domain_resolves only proved an A-record (the business has a website); sending to a
# domain with no mail server hard-bounces, and hard bounces are what blacklist a sender.
# domain_accepts_mail is the real gate: MX record, with the RFC 5321 A-record fallback.

def test_domain_accepts_mail_true_when_mx_present():
    assert enrich.domain_accepts_mail("joesdiner.com",
                                      mx_lookup=lambda d: ["10 mail.joesdiner.com"],
                                      a_lookup=lambda d: False) is True


def test_domain_accepts_mail_falls_back_to_a_record():
    # RFC 5321 §5.1: no MX but an A-record host still accepts mail.
    assert enrich.domain_accepts_mail("joesdiner.com",
                                      mx_lookup=lambda d: [],
                                      a_lookup=lambda d: True) is True


def test_domain_accepts_mail_false_when_no_mx_no_a():
    assert enrich.domain_accepts_mail("nonexistent-xyz.invalid",
                                      mx_lookup=lambda d: [],
                                      a_lookup=lambda d: False) is False


def test_verify_email_rejects_role_and_junk_locals():
    v = enrich.verify_email("noreply@joesdiner.com", mx_fn=lambda d: True)
    assert v["deliverable"] is False
    assert v["reason"] == "role-or-junk"


def test_verify_email_rejects_bad_syntax():
    assert enrich.verify_email("not-an-email", mx_fn=lambda d: True)["deliverable"] is False
    assert enrich.verify_email("two@@at.com", mx_fn=lambda d: True)["deliverable"] is False


def test_verify_email_deliverable_with_mx():
    v = enrich.verify_email("owner@joesdiner.com", mx_fn=lambda d: True)
    assert v["deliverable"] is True
    assert v["confidence"] == "mx"


def test_verify_email_not_deliverable_when_domain_refuses_mail():
    v = enrich.verify_email("owner@joesdiner.com", mx_fn=lambda d: False)
    assert v["deliverable"] is False
    assert v["reason"] == "no-mail-server"


def test_find_email_default_verify_requires_mail_accepting_domain():
    # A real scraped address on a domain that doesn't accept mail must be dropped by the
    # DEFAULT verify path (not just when a test injects verify_fn=False).
    lead = {"name": "Joes Diner", "region": "GA", "contact": {"website": "https://joesdiner.com"}}
    pages = {"https://joesdiner.com": "contact owner@joesdiner.com"}
    # patch the module's mail check to refuse, leave everything else default
    import utah.product.enrich as e
    orig = e.domain_accepts_mail
    e.domain_accepts_mail = lambda dom, **kw: False
    try:
        r = e.find_email(lead, fetch_html=lambda u: pages.get(u, ""))
    finally:
        e.domain_accepts_mail = orig
    assert r["email"] is None
