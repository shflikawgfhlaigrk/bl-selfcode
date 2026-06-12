"""Outreach edges — per-lead fault isolation, prospect filters, channel fallthrough,
the footer-incomplete follow-up gate, and the auto-channel mail-exhausted shift.

``queue()`` is the daily send loop: one lead whose ledger write blows up must never
abort the rest of the batch (the remaining sends are the day's real revenue work),
and a failed/raising send must never burn the prospect's one suppression slot.
"""
from __future__ import annotations

import pytest

from utah import failures, sms
from utah.product import outreach
from tests.fakes import FakeFailureStore

REAL_FOOTER = {"address": "123 Main St, Newnan GA 30263",
               "unsubscribe": "Reply STOP to opt out."}


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


class _Ledger:
    def __init__(self):
        self.calls, self._seen = [], set()

    def log_outreach(self, recipient, campaign, channel="email"):
        key = (recipient, campaign)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.calls.append((recipient, campaign, channel))
        return True


# --- per-lead fault isolation -----------------------------------------------------------

def test_queue_one_bad_lead_never_aborts_the_batch(_store):
    """A ledger hiccup on lead #1 (dead row, constraint bug) must be counted + documented
    and the batch must CONTINUE — lead #2 still gets queued."""
    class _Boom(_Ledger):
        def log_outreach(self, recipient, campaign, channel="email"):
            if recipient == "boom@x.com":
                raise RuntimeError("outreach_ledger constraint blew up")
            return super().log_outreach(recipient, campaign, channel)

    lg = _Boom()
    leads = [{"name": "Boom Co", "contact": {"email": "boom@x.com"}},
             {"name": "Fine Co", "contact": {"email": "fine@x.com"}}]
    r = outreach.queue(lg, "c1", leads)
    assert r["queued"] == 1                       # the batch survived the bad lead
    assert r["errors"] == 1
    assert lg.calls == [("fine@x.com", "c1", "email")]
    assert any(row[2] == "lead_failed" for row in _store.rows)


def test_queue_raising_sender_is_isolated_and_does_not_suppress(_store):
    """An injected sender that RAISES (not just returns sent=False) is a per-lead error:
    documented, counted, and the prospect keeps their one shot (no suppression row)."""
    def kaboom(to, subject, body):
        raise OSError("smtp socket reset")

    lg = _Ledger()
    r = outreach.queue(lg, "c1", [{"name": "Email Co", "contact": {"email": "x@y.com"}}],
                       footer=REAL_FOOTER, can_send=True, send_fn=kaboom,
                       verify_fn=lambda e: {"deliverable": True})
    assert r["sent"] == 0 and r["errors"] == 1
    assert lg.calls == []                         # never suppressed on a raise
    assert any(row[2] == "lead_failed" for row in _store.rows)


def test_queue_email_send_failure_keeps_the_prospects_one_shot(_store):
    """sent=False from the mail boundary: documented as send_failed, NOT suppressed and
    NOT counted queued — the prospect is retried on a later run."""
    lg = _Ledger()
    r = outreach.queue(lg, "c1", [{"name": "Email Co", "contact": {"email": "x@y.com"}}],
                       footer=REAL_FOOTER, can_send=True,
                       send_fn=lambda *a: {"sent": False, "error": "smtp 550"},
                       verify_fn=lambda e: {"deliverable": True})
    assert r["sent"] == 0 and r["queued"] == 0
    assert lg.calls == []                         # suppression only commits on a landed send
    assert any(row[2] == "send_failed" for row in _store.rows)


def test_queue_sms_send_failure_does_not_suppress(monkeypatch, _store):
    monkeypatch.setattr(sms, "send", lambda to, body: {"sent": False, "reason": "no creds"})
    lg = _Ledger()
    r = outreach.queue(lg, "c1", [{"name": "Phone Co", "contact": {"phone": "+17705551234"}}],
                       footer=REAL_FOOTER, can_send=True, prefer="sms")
    assert r["sent"] == 0 and lg.calls == []      # Twilio may land later; one shot kept
    assert any(row[2] == "send_failed" for row in _store.rows)


# --- channel fallthrough + prospect filters ----------------------------------------------

def test_pick_channel_prefer_falls_through_to_what_the_lead_has():
    """A prefer the lead can't satisfy falls through email-first (Michael's directive)."""
    assert outreach.pick_channel({"email": "a@b.com"}, prefer="sms") == "email"
    assert outreach.pick_channel({"phone": "555-1"}, prefer="email") == "sms"
    assert outreach.pick_channel(None, prefer="sms") is None


def test_corporate_inbox_detection_edges():
    assert outreach._is_corporate_inbox("not-an-email") is True       # malformed -> never send
    assert outreach._is_corporate_inbox("savannahservice@tesla.com") is True
    assert outreach._is_corporate_inbox("Customer.Service@joesdiner.com") is True  # role inbox
    assert outreach._is_corporate_inbox("joe@joesdiner.com") is False  # a real owner


def test_phone_prospect_rejects_tollfree_and_chains():
    assert outreach._is_phone_prospect(
        {"name": "Ace Hardware", "contact": {"phone": "+17705551234"}}) is False
    assert outreach._is_phone_prospect(
        {"name": "Joe's Fix-It", "contact": {"phone": "+18885091616"}}) is False  # toll-free
    assert outreach._is_phone_prospect(
        {"name": "Joe's Fix-It", "contact": {"phone": "+17705551234"}}) is True
    assert outreach._is_phone_prospect({"name": "Joe's Fix-It", "contact": {}}) is False


# --- follow-up footer gate -----------------------------------------------------------------

def test_followups_blocked_on_incomplete_canspam_footer(monkeypatch, tmp_path):
    """Follow-ups are emails too: an incomplete physical address blocks them the same as
    cold sends (a non-compliant nudge is still non-compliant)."""
    monkeypatch.delenv("UTAH_CANSPAM_ADDRESS", raising=False)
    from utah import config
    monkeypatch.setattr(config, "BUSINESS_CREDS", tmp_path / "absent.json")
    out = outreach.run_followups(ledger=object(), now_hour=10)
    assert out["sent"] == 0 and out.get("blocked") is True


# --- auto channel: mail-capped day shifts to SMS -------------------------------------------

def test_run_auto_shifts_full_quota_to_sms_when_mail_capped(monkeypatch):
    """Every inbox at its daily cap -> the email budget is 0 and the whole quota goes to
    phone leads, so the day isn't dead after the morning email burst."""
    from utah import mail
    monkeypatch.setattr(mail, "inboxes_exhausted", lambda: True)
    monkeypatch.setattr(mail, "sends_remaining", lambda: 0)
    monkeypatch.setenv("UTAH_CANSPAM_ADDRESS", "123 Main St, Newnan GA 30263")
    monkeypatch.setattr(sms, "send", lambda to, body: {"sent": True, "channel": "imessage"})

    class _AutoLedger(_Ledger):
        def uncontacted_email_leads(self, campaign, limit):
            return [{"id": 1, "name": "Email Co",
                     "contact": {"email": "owner@emailco.com"}, "source": "osm"}]

        def uncontacted_phone_leads(self, campaign, limit):
            return [{"id": 2, "name": "Joe's Fix-It",
                     "contact": {"phone": "+17705551234"}, "source": "google_maps"}]

        def mark_lead_contacted(self, recipient):
            return 1

    out = outreach._run_auto(_AutoLedger(), "smb_no_website", 5, None)
    assert out["mail_exhausted"] is True
    assert out["email"]["pulled"] == 0            # capped mail pulls NO email leads
    assert out["text"]["sent"] == 1 and out["sent"] == 1
