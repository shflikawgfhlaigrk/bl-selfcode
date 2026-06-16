"""People-level contact engine — owning Apollo's moat in-house.

Apollo/Hunter don't magically know a person's email; they generate the standard email PATTERNS
a company uses (first.last@, flast@, first@, …), rank by commonality, and gate on real MX
deliverability. We build the same, free, reusing utah.product.enrich's MX verifier. Honest by
design: a pattern on a mail-accepting domain is a ranked GUESS, not an RCPT-confirmed mailbox.
All offline-testable via an injected verify_fn.
"""
from __future__ import annotations

from utah.product import contacts


def _accepts(_email):
    return {"deliverable": True, "reason": "ok", "confidence": "mx"}


def _rejects(_email):
    return {"deliverable": False, "reason": "no-mx", "confidence": "mx"}


def test_email_patterns_covers_the_common_formats():
    pats = contacts.email_patterns("John", "Doe", "acme.com")
    for want in ["john.doe@acme.com", "jdoe@acme.com", "john@acme.com",
                 "johndoe@acme.com", "j.doe@acme.com"]:
        assert want in pats
    assert all(p.endswith("@acme.com") and p == p.lower() for p in pats)


def test_email_patterns_sanitizes_messy_names_and_domain():
    pats = contacts.email_patterns("Mary-Jane", "O'Brien", "@ACME.com")
    assert "maryjane.obrien@acme.com" in pats        # punctuation stripped, lowercased
    assert all("@acme.com" in p for p in pats)


def test_email_patterns_empty_without_name_or_domain():
    assert contacts.email_patterns("", "", "acme.com") == []
    assert contacts.email_patterns("John", "Doe", "") == []


def test_guess_email_returns_top_pattern_on_mail_accepting_domain():
    g = contacts.guess_email("John", "Doe", "acme.com", verify_fn=_accepts)
    assert g["email"] == "john.doe@acme.com"
    assert g["confidence"] == "pattern+mx"
    assert "john.doe@acme.com" in g["candidates"]


def test_guess_email_honest_when_domain_rejects_mail():
    g = contacts.guess_email("John", "Doe", "acme.com", verify_fn=_rejects)
    assert g["email"] is None
    assert g["confidence"] == "none"
    assert g["candidates"]                       # still returns the patterns it would try


def test_guess_email_honest_without_inputs():
    g = contacts.guess_email("", "", "", verify_fn=_accepts)
    assert g["email"] is None and g["confidence"] == "none"


def test_find_contacts_builds_verified_people_contacts():
    people = [{"name": "John Doe", "title": "Owner"},
              {"name": "Jane Smith", "title": "Office Manager"}]
    out = contacts.find_contacts("Acme Plumbing", "acme.com", people, verify_fn=_accepts)
    assert [c["email"] for c in out] == ["john.doe@acme.com", "jane.smith@acme.com"]
    assert out[0]["title"] == "Owner" and out[0]["company"] == "Acme Plumbing"
    assert out[0]["confidence"] == "pattern+mx"


def test_find_contacts_single_name_falls_back_to_first_only():
    out = contacts.find_contacts("Acme", "acme.com", [{"name": "Cher", "title": "CEO"}],
                                 verify_fn=_accepts)
    assert out[0]["email"] == "cher@acme.com"
