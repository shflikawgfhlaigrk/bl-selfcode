"""Contact discovery — find the decision-makers (names + titles) from a company's own site.

This is the layer that feeds the email engine: scrape team/about/leadership pages for people,
then `find_contacts` turns each into a verified email. Parsing is pure and offline-testable;
fetching is injected. Heuristic + honest — it only keeps a name when a real role word sits with
it, so it doesn't invent people from stray capitalized words.
"""
from __future__ import annotations

from utah.product import contacts


def test_extract_people_one_liner_name_comma_title():
    html = "<p>John Doe, Owner</p><p>Jane Smith - Office Manager</p>"
    people = contacts.extract_people(html)
    assert {"name": "John Doe", "title": "Owner"} in people
    assert {"name": "Jane Smith", "title": "Office Manager"} in people


def test_extract_people_card_layout_name_then_role_line():
    html = "<div class='card'><h3>Maria Lopez</h3><span>Founder &amp; CEO</span></div>"
    people = contacts.extract_people(html)
    assert people and people[0]["name"] == "Maria Lopez"
    assert "founder" in people[0]["title"].lower()


def test_extract_people_ignores_capitalized_non_people():
    html = "<h1>Welcome Home</h1><p>Contact Us Today</p><p>Our Services</p>"
    assert contacts.extract_people(html) == []


def test_team_page_urls_cover_common_paths():
    urls = contacts.team_page_urls("acme.com")
    joined = " ".join(urls)
    assert "/team" in joined and "/about" in joined
    assert urls == list(dict.fromkeys(urls))           # deduped, order-preserving


def test_discover_people_scans_pages_and_dedupes():
    pages = {
        "https://acme.com/team": "<h3>John Doe</h3><p>Owner</p>",
        "https://acme.com/about": "<p>John Doe, Owner</p><p>Sara Vance, Director</p>",
    }
    people = contacts.discover_people("acme.com", fetch_html=lambda u: pages.get(u, ""))
    names = sorted(p["name"] for p in people)
    assert names == ["John Doe", "Sara Vance"]          # John Doe not double-counted


def test_discover_contacts_end_to_end():
    pages = {"https://acme.com/team": "<h3>John Doe</h3><p>Owner</p>"}
    out = contacts.discover_contacts(
        "Acme Plumbing", "https://acme.com",
        fetch_html=lambda u: pages.get(u, ""),
        verify_fn=lambda e: {"deliverable": True, "reason": "ok", "confidence": "mx"})
    assert out and out[0]["name"] == "John Doe"
    assert out[0]["email"] == "john.doe@acme.com"
    assert out[0]["company"] == "Acme Plumbing"
