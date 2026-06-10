"""Outreach cadence + reach (Michael's 2026-06-09 directive), proven in the machine:

  * SENDS only inside business hours — a 5am run sends NOTHING (never again).
  * ≤50/run == 50/hour; 10 business hours (08:00–17:00) × 50 = 500/day floor.
  * 'auto' channel: email a lead that has an email, TEXT (SMS→iMessage) a phone-only
    lead — so a phone-only lead is reached when no email exists.
  * No Twilio → the text goes out via the Mac's own iMessage relay.
"""
from __future__ import annotations

from utah import config, failures, sms
from utah.integrations import imessage
from utah.product import outreach
from tests.fakes import FakeFailureStore


_FOOTER = {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"}


# --- business-hours guard (the rule in the machine, not just the plist) ------

def test_within_business_hours_window():
    assert config.within_business_hours(5) is False     # 5am — Michael's "not at 5am"
    assert config.within_business_hours(7) is False      # before open
    assert config.within_business_hours(8) is True       # open
    assert config.within_business_hours(12) is True
    assert config.within_business_hours(17) is True      # 5pm close (inclusive)
    assert config.within_business_hours(18) is False     # after close
    assert config.within_business_hours(2) is False


def test_run_scheduled_refuses_to_send_at_5am(monkeypatch):
    """A run kicked at 5am — by a manual kickstart or a misconfigured cron — sends
    NOTHING and says why. This is the structural guarantee Michael asked for."""
    sent: list = []

    class Boom:
        # any DB access would raise — proving we bail out BEFORE touching the ledger
        def __getattr__(self, _):
            raise AssertionError("must not touch the ledger outside business hours")

    r = outreach.run_scheduled(ledger=Boom(), now_hour=5,
                               foundation_gate=lambda cap: None,
                               send_fn=lambda *a: sent.append(a) or {"sent": True})
    assert r["sent"] == 0 and r.get("skipped") is True
    assert "business hours" in r["reason"]
    assert sent == []


# --- 500/day floor (50/hour × 10 business hours) -----------------------------

def test_500_per_day_arithmetic_holds():
    business_hours = [h for h in range(24) if config.within_business_hours(h)]
    assert len(business_hours) == 10                      # 08:00..17:00 inclusive
    assert config.OUTREACH_PER_HOUR >= 50
    assert len(business_hours) * config.OUTREACH_PER_HOUR >= 500


# --- 'auto' channel: email-first, text-fallback, combined cap ----------------

def _auto_ledger(email_leads, phone_leads):
    class FakeLedger:
        def uncontacted_email_leads(self, campaign, limit):
            return list(email_leads)[:limit]
        def uncontacted_phone_leads(self, campaign, limit):
            return list(phone_leads)[:limit]
        def is_contacted(self, r, c):
            return False
        def log_outreach(self, r, c, channel="email"):
            return True
        def record_mail(self, *a, **k):
            return True
    return FakeLedger()


def test_auto_channel_emails_and_texts_and_respects_combined_cap(monkeypatch):
    failures.set_store(FakeFailureStore())
    emails, texts = [], []
    monkeypatch.setattr(outreach, "default_footer", lambda: _FOOTER)
    monkeypatch.setattr("utah.mail.send",
                        lambda to, s, b: emails.append(to) or {"sent": True})
    monkeypatch.setattr("utah.sms.send",
                        lambda to, body: texts.append(to) or {"sent": True})

    email_leads = [{"id": 1, "name": "La Monarca", "kind": "restaurant", "source": "osm",
                    "contact": {"email": "lamonarca@gmail.com"}}]
    phone_leads = [{"id": 2, "name": "Joe Handyman", "kind": "trade", "source": "google_maps",
                    "contact": {"phone": "+15551234567"}},
                   {"id": 3, "name": "Ace Plumbing", "kind": "trade", "source": "osm",
                    "contact": {"phone": "+15557654321"}}]

    r = outreach.run_scheduled(limit=3, ledger=_auto_ledger(email_leads, phone_leads),
                               foundation_gate=lambda cap: None, channel="auto", now_hour=10)
    assert r["channel"] == "auto"
    assert emails == ["lamonarca@gmail.com"]              # email lead → email
    assert texts == ["+15551234567", "+15557654321"]      # phone-only leads → text
    assert r["sent"] == 3                                  # combined fills the cap


def test_auto_channel_caps_total_at_limit(monkeypatch):
    """Combined email+text never exceeds the per-run (per-hour) cap."""
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(outreach, "default_footer", lambda: _FOOTER)
    monkeypatch.setattr("utah.mail.send", lambda to, s, b: {"sent": True})
    monkeypatch.setattr("utah.sms.send", lambda to, body: {"sent": True})
    email_leads = [{"id": i, "name": f"Biz{i}", "source": "osm",
                    "contact": {"email": f"b{i}@gmail.com"}} for i in range(10)]
    phone_leads = [{"id": 100 + i, "name": f"Ph{i}", "source": "osm",
                    "contact": {"phone": f"+1555000{i:04d}"}} for i in range(10)]
    r = outreach.run_scheduled(limit=5, ledger=_auto_ledger(email_leads, phone_leads),
                               foundation_gate=lambda cap: None, channel="auto", now_hour=10)
    assert r["sent"] == 5                                  # exactly the cap, not 20


# --- iMessage fallback when Twilio is absent ---------------------------------

def test_sms_falls_back_to_imessage_when_no_twilio(monkeypatch, tmp_path):
    from utah import sms as _sms
    monkeypatch.setattr(_sms, "DAILY_COUNTER", tmp_path / "sms_daily.json")   # live cap state never decides a unit test
    """No Twilio creds → the text goes out via Messages.app (Michael's own texts),
    not a dead gate."""
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(sms, "creds_available", lambda: False)
    delivered = []
    monkeypatch.setattr(imessage, "send",
                        lambda to, body: delivered.append((to, body)) or
                        {"sent": True, "channel": "imessage"})
    r = sms.send("+15551234567", "Hi — I build websites for local businesses.")
    assert r["sent"] is True and r["channel"] == "imessage"
    assert delivered and delivered[0][0] == "+15551234567"


def test_imessage_send_uses_injected_runner_and_gates_cleanly(monkeypatch):
    failures.set_store(FakeFailureStore())
    calls = []
    r = imessage.send("+15551234567", "hello",
                      send_fn=lambda to, body: calls.append((to, body)))
    assert r["sent"] is True and r["channel"] == "imessage"
    assert calls == [("+15551234567", "hello")]
    # empty recipient → documented gate, never a crash
    assert imessage.send("", "x")["gated"] is True


def test_imessage_failure_is_a_documented_gate_not_a_crash(monkeypatch):
    failures.set_store(FakeFailureStore())
    def boom(to, body):
        raise RuntimeError("Messages not authorized for Automation")
    r = imessage.send("+15551234567", "hi", send_fn=boom)
    assert r["sent"] is False and r["gated"] is True       # gate, not exception


# --- per-lead personalization (deliverability + relevance) -------------------

def test_compose_is_personalized_by_business_type_and_never_trips_spam_gate():
    """Each pitch carries a type-specific relevance line and a varied subject (so a
    500/day batch isn't identical bodies), while Michael's price/phone stay intact and
    the body never trips the spam-content gate."""
    leads = [
        {"name": "Joe's Diner", "kind": "restaurant"},
        {"name": "Ace Plumbing", "kind": "plumber"},
        {"name": "Glam Salon", "kind": "salon"},
        {"name": "Smith Auto", "kind": "car_repair"},
    ]
    subjects, bodies = set(), set()
    for lead in leads:
        m = outreach.compose(lead, outreach.SMB_OUTREACH_CAMPAIGN, _FOOTER)
        subjects.add(m["subject"]); bodies.add(m["body"])
        assert "$700" in m["body"] and "678-876-1170" in m["body"]   # Michael's copy intact
        assert "28 Dogwood Rd" in m["body"]                          # CAN-SPAM address stamped
        assert outreach.content_score(m["subject"] + " " + m["body"])["block"] is False
    assert len(subjects) == 4 and len(bodies) == 4                   # every lead distinct


def test_relevance_hook_is_deterministic_with_a_generic_fallback():
    assert "Diners" in outreach._relevance_hook("restaurant")
    assert outreach._relevance_hook("plumber") == outreach._relevance_hook("plumber")  # stable
    assert outreach._relevance_hook("totally_unknown_kind")          # non-empty fallback
    assert outreach._relevance_hook(None)                            # never crashes
