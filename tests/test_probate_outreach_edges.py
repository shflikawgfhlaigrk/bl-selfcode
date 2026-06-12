"""Probate direct-mail edges — raising providers, per-case fault isolation, suppression
on prior contact, unwritable letter dir, and the never-raises cron boundary.

``run_scheduled`` is a launchd entrypoint (``print(run_scheduled())``): a dead store
or a blowing-up print-mail provider must degrade to an honest dict + recorded failure —
never a crash, never a suppression row for a letter that didn't actually go out.
"""
from __future__ import annotations

import pytest

from utah import failures
from utah.product import probate_outreach
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


class _Ledger:
    def __init__(self, contacted=()):
        self.contacted = set(contacted)
        self.logged = []

    def is_contacted(self, recipient, campaign):
        return recipient in self.contacted

    def log_outreach(self, recipient, campaign, channel="mail"):
        self.logged.append((recipient, campaign, channel))
        return True


def _case(name="Estate of Jane Doe"):
    return {"case_name": name, "county": "hall",
            "heir_contact": {"owner_mail": {"street": "1 Elm", "full": "1 Elm, Gainesville GA"}}}


# --- raising provider: isolated, falls back to the letter file, never suppresses --------

def test_raising_provider_falls_back_to_letter_file_and_never_suppresses(tmp_path, monkeypatch, _store):
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", tmp_path / "letters")
    monkeypatch.setattr(probate_outreach, "MAIL_SERVICE_CREDS", tmp_path / "absent.json")

    def kaboom(letter):
        raise OSError("lob api socket reset")

    lg = _Ledger()
    r = probate_outreach.queue(lg, [_case()], can_send=True, send_fn=kaboom)
    assert r["sent"] == 0 and r["queued"] == 1              # letter still written for Michael
    assert lg.logged == []                                  # estate keeps its one shot
    assert (tmp_path / "letters" / "estate-of-jane-doe.txt").exists()
    assert any(row[2] == "provider_send_failed" for row in _store.rows)


def test_one_bad_case_never_aborts_the_batch(tmp_path, monkeypatch, _store):
    """A ledger blow-up on case #1's suppression commit is counted + documented; case #2
    still sends. (The letter for #1 DID go out — that is recorded, never silently lost.)"""
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", tmp_path / "letters")

    class _Boom(_Ledger):
        def log_outreach(self, recipient, campaign, channel="mail"):
            if recipient == "Estate of Boom":
                raise RuntimeError("outreach_ledger down")
            return super().log_outreach(recipient, campaign, channel)

    lg = _Boom()
    r = probate_outreach.queue(lg, [_case("Estate of Boom"), _case("Estate of Fine")],
                               can_send=True, send_fn=lambda letter: {"sent": True})
    assert r["sent"] == 1 and r["errors"] == 1
    assert lg.logged == [("Estate of Fine", probate_outreach.PROBATE_OUTREACH_CAMPAIGN, "mail")]
    assert any(row[2] == "case_failed" for row in _store.rows)


# --- suppression + unwritable letters dir ------------------------------------------------

def test_already_contacted_estate_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", tmp_path / "letters")
    lg = _Ledger(contacted={"Estate of Jane Doe"})
    r = probate_outreach.queue(lg, [_case()], can_send=True,
                               send_fn=lambda letter: {"sent": True})
    assert r["suppressed"] == 1 and r["sent"] == 0 and lg.logged == []


def test_unwritable_letters_dir_is_not_counted_queued(tmp_path, monkeypatch):
    """The no-vendor path writes a file; when even THAT fails the case is not counted as
    queued (a letter that doesn't exist was never 'queued') and the run continues."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a dir must go")
    monkeypatch.setattr(probate_outreach, "LETTERS_DIR", blocker / "letters")
    monkeypatch.setattr(probate_outreach, "MAIL_SERVICE_CREDS", tmp_path / "absent.json")
    r = probate_outreach.queue(_Ledger(), [_case()], can_send=False)
    assert r["queued"] == 0 and r["letters"] == []


# --- run_scheduled: never-raises cron boundary --------------------------------------------

def test_run_scheduled_is_honest_when_store_is_dead(_store):
    class DeadLedger:
        def probate_uncontacted_with_mail(self, limit):
            raise RuntimeError("pg refused")

    out = probate_outreach.run_scheduled(ledger=DeadLedger(),
                                         foundation_gate=lambda cap: None)
    assert out["sent"] == 0 and out["queued"] == 0
    assert "pg refused" in out["error"]
    assert any(row[2] == "store_unreachable" for row in _store.rows)


def test_run_scheduled_no_cases_documents_the_reason():
    class EmptyLedger:
        def probate_uncontacted_with_mail(self, limit):
            return []

    out = probate_outreach.run_scheduled(ledger=EmptyLedger(),
                                         foundation_gate=lambda cap: None)
    assert out == {"sent": 0, "queued": 0,
                   "reason": "no probate cases with a resolved mailing address"}


# --- small pure helpers --------------------------------------------------------------------

def test_slug_degenerate_names_never_empty():
    assert probate_outreach._slug("") == "letter"
    assert probate_outreach._slug(None) == "letter"
    assert probate_outreach._slug("///???") == "letter"
    assert probate_outreach._slug("Estate of Jane Doe") == "estate-of-jane-doe"


def test_compose_letter_without_owner_addresses_property_owner():
    letter = probate_outreach.compose_letter(
        {"case_name": "Estate of X", "heir_contact": {"owner_mail": {"full": "1 Elm, GA"}}},
        footer_address="28 Dogwood Rd, Newnan GA 30263")
    assert letter["addressee"] == "Property Owner"
    assert letter["body"].startswith("Property Owner\n1 Elm, GA")
