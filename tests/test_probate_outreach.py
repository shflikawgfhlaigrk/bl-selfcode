"""Probate direct-mail last-mile (audit §1.2): heir mailing-address resolve → letter →
suppressed, gated send. Heirs get a physical letter (CAN-SPAM governs email, not paper)."""
from __future__ import annotations

import pytest

from utah import failures
from utah.product import probate_outreach, property as prop
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    failures.set_store(FakeFailureStore())
    yield


# --- heir mailing-address extraction from a parcel record -------------------

def test_owner_mailing_address_pulled_from_varied_field_names():
    attrs = {"OWNER": "SMITH JOHN", "SITEADDR": "10 Oak St",
             "MAILADDR": "PO Box 55", "MAILCITY": "Newnan", "MAILSTATE": "GA", "MAILZIP": "30263"}
    m = prop._owner_mailing_address(attrs)
    assert m["street"] == "PO Box 55" and m["city"] == "Newnan"
    assert m["full"] == "PO Box 55, Newnan GA 30263"


def test_owner_mailing_address_empty_when_no_mail_field():
    assert prop._owner_mailing_address({"OWNER": "X", "SITEADDR": "1 Main"}) == {}


# --- letter composer --------------------------------------------------------

def test_compose_letter_uses_real_address_and_michaels_pitch():
    case = {"case_name": "Estate of John Smith", "county": "harris", "arv": 185000,
            "heir_contact": {"owner": "SMITH JOHN",
                             "owner_mail": {"street": "PO Box 55", "full": "PO Box 55, Newnan GA 30263"}}}
    letter = probate_outreach.compose_letter(case, footer_address="28 Dogwood Rd, Newnan GA 30263")
    assert letter["to"] == "PO Box 55, Newnan GA 30263"
    assert "678-876-1170" in letter["body"] and "$185,000" in letter["body"]
    assert "no-obligation" in letter["body"]


# --- queue: gated send writes a ready letter, never fakes, never suppresses --

def test_queue_writes_letter_and_does_not_suppress_when_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", tmp_path / "letters")
    monkeypatch.setattr(probate_outreach, "MAIL_SERVICE_CREDS", tmp_path / "absent.json")
    logged = []

    class FakeLedger:
        def is_contacted(self, r, c): return False
        def log_outreach(self, r, c, ch="mail"): logged.append((r, c, ch)); return True

    case = {"case_name": "Estate of Jane Doe", "county": "hall", "arv": None,
            "heir_contact": {"owner_mail": {"street": "1 Elm", "full": "1 Elm, Gainesville GA"}}}
    r = probate_outreach.queue(FakeLedger(), [case], can_send=True)   # provider gated
    assert r["sent"] == 0 and r["queued"] == 1
    assert logged == []                                     # NOT suppressed — estate keeps its shot
    assert (tmp_path / "letters" / "estate-of-jane-doe.txt").exists()


def test_queue_sends_and_suppresses_with_a_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", tmp_path / "letters")
    logged = []

    class FakeLedger:
        def is_contacted(self, r, c): return False
        def log_outreach(self, r, c, ch="mail"): logged.append((r, c, ch)); return True

    case = {"case_name": "Estate of Real Send", "county": "bryan",
            "heir_contact": {"owner_mail": {"street": "9 Pine", "full": "9 Pine, GA"}}}
    r = probate_outreach.queue(FakeLedger(), [case], can_send=True,
                               send_fn=lambda letter: {"sent": True})
    assert r["sent"] == 1 and r["queued"] == 0
    assert logged == [("Estate of Real Send", probate_outreach.PROBATE_OUTREACH_CAMPAIGN, "mail")]


def test_queue_skips_cases_without_a_mailing_address():
    class FakeLedger:
        def is_contacted(self, r, c): return False
        def log_outreach(self, r, c, ch="mail"): return True
    r = probate_outreach.queue(FakeLedger(),
                               [{"case_name": "No Addr", "heir_contact": {}}], can_send=True)
    assert r["no_addr"] == 1 and r["sent"] == 0 and r["queued"] == 0


def test_run_scheduled_skips_on_red_substrate():
    r = probate_outreach.run_scheduled(ledger=object(),
                                       foundation_gate=lambda cap: {"status": "substrate_red"})
    assert r.get("status") == "substrate_red"
