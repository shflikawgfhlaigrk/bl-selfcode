"""Day-3 / day-7 follow-up sequence — only possible now that reply detection exists
(the ledger filters to non-repliers/non-bounces). Two touches max, each at most once,
business-hours gated, CAN-SPAM footer intact, and follow-ups must never break or
double-count the cold-send path."""
from __future__ import annotations

import pytest

from utah.product import outreach


class FakeLedger:
    def __init__(self, candidates_by_campaign=None):
        self._cands = candidates_by_campaign or {}
        self.logged: list[tuple[str, str]] = []
        self.mailed: list[tuple[str, str]] = []

    def followup_candidates(self, base, fu_campaign, age_days, limit=10):
        return list(self._cands.get(fu_campaign, []))[:limit]

    def log_outreach(self, recipient, campaign, channel="email"):
        key = (recipient, campaign)
        if key in self.logged:
            return False
        self.logged.append(key)
        return True

    def record_mail(self, recipient, subject, status="sent", channel="email"):
        self.mailed.append((recipient, subject))
        return True


CAND = {"recipient": "thachhutlaundry@gmail.com", "name": "Thach Hut Laundry",
        "kind": "laundry", "region": "Coweta County, GA", "contact": {}}


def _footer():
    return {"address": "20050 Oak Tree Road E, Apt 8004, Gulf Shores, AL 36542",
            "unsubscribe": "Reply STOP to opt out."}


@pytest.fixture(autouse=True)
def real_footer(monkeypatch):
    monkeypatch.setattr(outreach, "default_footer", _footer)
    monkeypatch.setattr(outreach, "_footer_is_real", lambda f: True)


def test_day3_followup_sends_and_suppresses_per_stage():
    led = FakeLedger({"smb_no_website_fu3": [CAND]})
    sent = []
    res = outreach.run_followups(ledger=led, now_hour=10,
                                 send_fn=lambda to, s, b: sent.append((to, s)))
    assert res["sent"] == 1 and res["stages"] == {"fu3": 1}
    assert led.logged == [("thachhutlaundry@gmail.com", "smb_no_website_fu3")]
    assert led.mailed and "Following up" in led.mailed[0][1]
    assert sent[0][0] == "thachhutlaundry@gmail.com"


def test_day7_final_copy_differs():
    led = FakeLedger({"smb_no_website_fu7": [CAND]})
    sent = []
    res = outreach.run_followups(ledger=led, now_hour=10,
                                 send_fn=lambda to, s, b: sent.append((to, s, b)))
    assert res["stages"] == {"fu7": 1}
    assert "Last note" in sent[0][1]
    assert "last note" in sent[0][2]


def test_stage_already_sent_is_skipped():
    led = FakeLedger({"smb_no_website_fu3": [CAND]})
    led.logged.append(("thachhutlaundry@gmail.com", "smb_no_website_fu3"))
    res = outreach.run_followups(ledger=led, now_hour=10,
                                 send_fn=lambda *a: pytest.fail("must not send"))
    assert res["sent"] == 0


def test_business_hours_gate():
    led = FakeLedger({"smb_no_website_fu3": [CAND]})
    res = outreach.run_followups(ledger=led, now_hour=5,
                                 send_fn=lambda *a: pytest.fail("5am send"))
    assert res["skipped"] is True and res["sent"] == 0


def test_followup_body_keeps_canspam_footer():
    msg = outreach.compose_followup(CAND, final=False, footer=_footer())
    assert "Gulf Shores" in msg["body"] and "STOP" in msg["body"]
    assert "678-876-1170" in msg["body"]
    msg7 = outreach.compose_followup(CAND, final=True, footer=_footer())
    assert "Gulf Shores" in msg7["body"]


def test_limit_caps_total_across_stages():
    many = [dict(CAND, recipient=f"p{i}@x.com") for i in range(8)]
    led = FakeLedger({"smb_no_website_fu3": many, "smb_no_website_fu7": many})
    sent = []
    res = outreach.run_followups(ledger=led, limit=5, now_hour=10,
                                 send_fn=lambda to, s, b: sent.append(to))
    assert res["sent"] == 5 and len(sent) == 5


def test_run_scheduled_rides_followups_on_email_runs(monkeypatch):
    """The hourly cron's email/auto runs must also flush due follow-ups, and a
    follow-up failure must never break the cold path."""
    led = FakeLedger({"smb_no_website_fu3": [CAND]})
    led.uncontacted_email_leads = lambda campaign, limit: []
    led.uncontacted_phone_leads = lambda campaign, limit: []
    monkeypatch.setattr(outreach, "OUTREACH_CHANNEL", "email")
    sent = []
    res = outreach.run_scheduled(ledger=led, foundation_gate=lambda cap: None,
                                 channel="email", now_hour=10,
                                 send_fn=lambda to, s, b: sent.append(to))
    assert res.get("followups", {}).get("sent") == 1
    assert sent == ["thachhutlaundry@gmail.com"]
