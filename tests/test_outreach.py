"""Outreach capability — Ace's outreach transitions here (NOT an agent): compose a
CAN-SPAM-compliant cold pitch for a no-website SMB, content-lint it, pick a channel from
the lead's contact, and suppression-queue via the ledger (never-twice). The actual SEND is
GATED on Michael's business inputs (SMS/email creds + CAN-SPAM physical address) — so it is
never faked; an attempted send records a DOCUMENTED gate to the failure log (the goal)."""
from __future__ import annotations

from utah import config, failures
from utah.product import outreach
from tests.fakes import FakeFailureStore


def test_compose_includes_name_and_canspam_footer():
    msg = outreach.compose({"name": "Joe's Diner"}, "smb_no_website",
                           footer={"address": "123 Main St, Newnan GA", "unsubscribe": "STOP to opt out"})
    assert "Joe's Diner" in msg["body"]
    assert "123 Main St, Newnan GA" in msg["body"]      # CAN-SPAM physical address
    assert "STOP to opt out" in msg["body"]             # opt-out
    assert msg["subject"]


def test_content_lint_blocks_spam_passes_plain():
    spammy = "FREE!!! ACT NOW!!! GUARANTEED $$$ CLICK HERE RISK-FREE WINNER"
    plain = "Hi, I noticed your shop doesn't have a website. I build simple ones for local businesses."
    assert outreach.content_score(spammy)["block"] is True
    assert outreach.content_score(plain)["block"] is False


def test_pick_channel_from_contact():
    assert outreach.pick_channel({"email": "a@b.com"}) == "email"
    assert outreach.pick_channel({"phone": "555-1"}) == "sms"
    assert outreach.pick_channel({}) is None
    assert outreach.pick_channel({"phone": "555-1", "email": "a@b.com"}, prefer="sms") == "sms"
    assert outreach.pick_channel({"phone": "555-1", "email": "a@b.com"}, prefer="email") == "email"


class _RecLedger:
    def __init__(self):
        self.calls, self._seen = [], set()

    def log_outreach(self, recipient, campaign, channel="email"):
        key = (recipient, campaign)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.calls.append((recipient, campaign, channel))
        return True


def test_queue_counts_channels_suppresses_and_gates_send():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    leads = [
        {"name": "Phone Co", "contact": {"phone": "555-1"}},
        {"name": "Email Co", "contact": {"email": "x@y.com"}},
        {"name": "No Contact Co", "contact": {}},
    ]
    r = outreach.queue(lg, "smb_no_website", leads)
    assert r["queued"] == 2 and r["needs_contact"] == 1
    assert r["sent"] == 0                                # send is gated, never faked
    assert r["gated"]                                    # a documented gate reason


def test_queue_suppresses_duplicates():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    leads = [{"name": "Phone Co", "contact": {"phone": "555-1"}}]
    assert outreach.queue(lg, "c1", leads)["queued"] == 1
    assert outreach.queue(lg, "c1", leads)["queued"] == 0   # never-twice


def test_send_gate_is_documented_in_failure_log():
    store = FakeFailureStore()
    failures.set_store(store)
    lg = _RecLedger()
    outreach.queue(lg, "c1", [{"name": "Phone Co", "contact": {"phone": "555-1"}}])
    assert any("gated" in row[2] or "gate" in row[2] for row in store.rows)  # why-it-didn't-send recorded


def test_send_refused_with_placeholder_canspam_address(monkeypatch, tmp_path):
    """can_send=True must NOT send when the CAN-SPAM physical address is still the
    placeholder — that is a non-compliant email that burns the prospect's one shot. Refuse,
    document the gate, keep the lead queued (never sent). This is the module's stated contract.

    Hermetic: force the *unconfigured* regime (no env address, business.json absent) so the
    default footer falls back to the placeholder regardless of the live ~/.utah/secrets."""
    monkeypatch.delenv("UTAH_CANSPAM_ADDRESS", raising=False)
    monkeypatch.setattr(config, "BUSINESS_CREDS", tmp_path / "absent.json")
    store = FakeFailureStore()
    failures.set_store(store)
    lg = _RecLedger()
    sent_to: list[str] = []

    def fake_send(to, subject, body):
        sent_to.append(to)
        return {"sent": True}

    leads = [{"name": "Email Co", "contact": {"email": "x@y.com"}}]
    r = outreach.queue(lg, "c1", leads, can_send=True, send_fn=fake_send)  # footer=None -> placeholder addr
    assert sent_to == []                                   # NO non-compliant email left the building
    assert r["sent"] == 0
    assert "address" in r["gated"].lower()                 # the refusal is surfaced to the caller/deck
    # rows are (seq, source, kind, detail) — the gate names WHY in the detail too
    assert any("address" in row[3].lower() for row in store.rows)


def test_run_scheduled_drives_real_sends_to_uncontacted_email_leads(monkeypatch):
    """The missing driver: run_scheduled pulls uncontacted email-leads and actually SENDS
    (suppression-aware, CAN-SPAM footer), instead of the leads sitting in the pile forever."""
    sent: list[str] = []

    class FakeLedger:
        def uncontacted_email_leads(self, campaign, limit):
            leads = [{"name": "Joe's Diner", "kind": "restaurant",
                      "contact": {"email": "joe@example.com", "address": "1 Main St"}},
                     {"name": "Salon X", "kind": "salon",
                      "contact": {"email": "x@salon.com", "address": "2 Oak St"}}]
            return leads[:limit]
        def is_contacted(self, r, c):
            return False
        def log_outreach(self, r, c, channel="email"):
            return True
        def record_mail(self, *a, **k):
            return True

    monkeypatch.setattr(outreach, "default_footer",
                        lambda: {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
                               foundation_gate=lambda cap: None,        # substrate green
                               channel="email", now_hour=10,           # pin a business hour
                               send_fn=lambda to, s, b: sent.append(to) or {"sent": True},
                               verify_fn=lambda e: {"deliverable": True})  # isolate send-driving from DNS
    assert r["sent"] == 1 and sent == ["joe@example.com"]               # capped + actually sent


def test_run_scheduled_drives_sms_to_uncontacted_phone_leads(monkeypatch):
    sent: list[str] = []

    class FakeLedger:
        def uncontacted_phone_leads(self, campaign, limit):
            return [{"name": "Joe Handyman", "kind": "trade",
                     "contact": {"phone": "+15551234567", "address": "1 Main St"}}]
        def is_contacted(self, r, c):
            return False
        def log_outreach(self, r, c, channel="sms"):
            return True

    monkeypatch.setattr(outreach, "default_footer",
                        lambda: {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"})
    monkeypatch.setattr("utah.sms.send", lambda to, body: sent.append(to) or {"sent": True})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(), now_hour=10,
                               foundation_gate=lambda cap: None, channel="sms")
    assert r["sent"] == 1 and sent == ["+15551234567"]


def test_run_scheduled_skips_chains_and_corporate_inboxes(monkeypatch):
    """Autonomous outreach must NOT cold-pitch a big brand. A corporate-domain lead (Tesla)
    is filtered; the genuine SMB behind it gets the send."""
    sent: list[str] = []

    class FakeLedger:
        def uncontacted_email_leads(self, campaign, limit):
            return [{"name": "Tesla Savannah", "kind": "car_repair",
                     "contact": {"email": "savannahservice@tesla.com"}},
                    {"name": "La Monarca", "kind": "restaurant",
                     "contact": {"email": "lamonarca@gmail.com", "address": "1 Main"}}]
        def is_contacted(self, r, c):
            return False
        def log_outreach(self, r, c, channel="email"):
            return True
        def record_mail(self, *a, **k):
            return True

    monkeypatch.setattr(outreach, "default_footer",
                        lambda: {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=5, ledger=FakeLedger(), foundation_gate=lambda cap: None,
                               channel="email", now_hour=10,
                               send_fn=lambda to, s, b: sent.append(to) or {"sent": True})
    assert sent == ["lamonarca@gmail.com"]               # Tesla filtered, real SMB sent
    assert r["sent"] == 1


def test_run_scheduled_gates_on_red_substrate(monkeypatch):
    """A red Postgres/daemon must SKIP outreach explicitly, never send blind."""
    r = outreach.run_scheduled(foundation_gate=lambda cap: {"status": "substrate_red"},
                               ledger=object(), now_hour=10)
    assert r.get("status") == "substrate_red"


def test_queue_refuses_probate_campaign():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    r = outreach.queue(lg, outreach.PROBATE_OUTREACH_CAMPAIGN,
                       [{"name": "Estate of Smith", "kind": "probate",
                         "contact": {"phone": "+15551234567"}, "source": "probate"}])
    assert r["sent"] == 0 and r["queued"] == 0 and "probate" in r["gated"].lower()


def test_queue_skips_non_smb_source_on_smb_campaign():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    leads = [
        {"name": "Bad Source Co", "contact": {"phone": "555-1"}, "source": "manual_import"},
        {"name": "Good Co", "contact": {"phone": "+15551234567"}, "source": "google_maps"},
    ]
    r = outreach.queue(lg, "smb_no_website", leads)
    assert r["queued"] == 1 and len(lg.calls) == 1


def test_send_proceeds_with_real_canspam_address():
    """With a real physical address, the send actually proceeds (the gate is a gate, not a wall)."""
    store = FakeFailureStore()
    failures.set_store(store)
    lg = _RecLedger()
    sent_to: list[str] = []

    def fake_send(to, subject, body):
        sent_to.append(to)
        return {"sent": True}

    leads = [{"name": "Email Co", "contact": {"email": "x@y.com"}}]
    footer = {"address": "123 Main St, Newnan GA 30263", "unsubscribe": "Reply STOP to opt out."}
    r = outreach.queue(lg, "c1", leads, footer=footer, can_send=True, send_fn=fake_send,
                       verify_fn=lambda e: {"deliverable": True})  # isolate from DNS — gate has its own tests
    assert sent_to == ["x@y.com"]                          # real address -> real send proceeds
    assert r["sent"] == 1


def test_compose_includes_business_site_and_passes_spam_gate():
    """Michael's directive: every outreach email carries the business site (proof of
    work). Cold pitch + follow-ups all include it, and the link must never trip the
    spam-content gate that would block the send."""
    msg = outreach.compose({"name": "Joe's Diner", "kind": "restaurant"}, "smb_no_website",
                           footer={"address": "123 Main St, Newnan GA", "unsubscribe": "STOP"})
    assert outreach.BUSINESS_SITE in msg["body"]
    assert not outreach.content_score(msg["subject"] + " " + msg["body"])["block"]
    for final in (False, True):
        fu = outreach.compose_followup({"name": "Joe's Diner"}, final=final,
                                       footer={"address": "123 Main St, Newnan GA",
                                               "unsubscribe": "STOP"})
        assert outreach.BUSINESS_SITE in fu["body"]
        assert not outreach.content_score(fu["subject"] + " " + fu["body"])["block"]


def test_landed_send_flips_lead_status_to_contacted(monkeypatch):
    """Funnel truth: a send that actually LANDS marks the lead contacted (B-fix: 58
    sends had left every lead at status='new', blinding the funnel metrics). The flip
    happens ONLY on success — a gated/failed send leaves the lead untouched."""
    flipped: list[str] = []

    class FakeLedger:
        def uncontacted_email_leads(self, campaign, limit):
            return [{"name": "Joe's Diner", "kind": "restaurant",
                     "contact": {"email": "joe@example.com"}}][:limit]
        def is_contacted(self, r, c):
            return False
        def log_outreach(self, r, c, channel="email"):
            return True
        def record_mail(self, *a, **k):
            return True
        def mark_lead_contacted(self, recipient):
            flipped.append(recipient)
            return 1

    monkeypatch.setattr(outreach, "default_footer",
                        lambda: {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
                               foundation_gate=lambda cap: None, channel="email",
                               now_hour=10, send_fn=lambda to, s, b: {"sent": True},
                               verify_fn=lambda e: {"deliverable": True})  # isolate from DNS
    assert r["sent"] == 1 and flipped == ["joe@example.com"]

    # failed send: no flip, lead keeps status (and its one shot)
    flipped.clear()
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
                               foundation_gate=lambda cap: None, channel="email",
                               now_hour=10, send_fn=lambda to, s, b: {"sent": False, "error": "smtp down"},
                               verify_fn=lambda e: {"deliverable": True})  # reach the send to test smtp-down
    assert r["sent"] == 0 and flipped == []


# --- deliverability gate at the send path: never send to an address that can't receive ---
_REAL_FOOTER = {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP"}


def test_queue_does_not_send_to_unverifiable_email():
    """The send-path deliverability gate: an address that can't receive mail is NEVER sent
    to — that hard bounce is exactly what blacklists the sending domain. Counted as
    unverified, not sent. verify_fn is injectable so the test never touches real DNS."""
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    sent: list = []
    leads = [{"name": "Dead Co", "contact": {"email": "owner@no-such-mail.invalid"}}]
    r = outreach.queue(lg, "c1", leads, footer=_REAL_FOOTER, can_send=True,
                       send_fn=lambda *a: sent.append(a) or {"sent": True},
                       verify_fn=lambda e: {"deliverable": False, "reason": "no-mail-server"})
    assert sent == []                       # nothing left the building
    assert r["unverified"] == 1
    assert r["sent"] == 0


def test_queue_sends_to_verified_email():
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    sent: list = []
    leads = [{"name": "Live Co", "contact": {"email": "owner@livebiz.com"}}]
    r = outreach.queue(lg, "c1", leads, footer=_REAL_FOOTER, can_send=True,
                       send_fn=lambda to, s, b: sent.append(to) or {"sent": True},
                       verify_fn=lambda e: {"deliverable": True, "reason": "ok", "confidence": "mx"})
    assert sent == ["owner@livebiz.com"]
    assert r["sent"] == 1
    assert r["unverified"] == 0
