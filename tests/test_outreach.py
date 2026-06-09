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
                        lambda: {"address": "28 Dogwood Rd, Newnan GA", "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
                               foundation_gate=lambda cap: None,        # substrate green
                               channel="email",
                               send_fn=lambda to, s, b: sent.append(to) or {"sent": True})
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
                        lambda: {"address": "28 Dogwood Rd", "unsubscribe": "Reply STOP"})
    monkeypatch.setattr("utah.sms.send", lambda to, body: sent.append(to) or {"sent": True})
    r = outreach.run_scheduled(limit=1, ledger=FakeLedger(),
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
                        lambda: {"address": "28 Dogwood Rd, Newnan GA", "unsubscribe": "Reply STOP"})
    r = outreach.run_scheduled(limit=5, ledger=FakeLedger(), foundation_gate=lambda cap: None,
                               channel="email",
                               send_fn=lambda to, s, b: sent.append(to) or {"sent": True})
    assert sent == ["lamonarca@gmail.com"]               # Tesla filtered, real SMB sent
    assert r["sent"] == 1


def test_run_scheduled_gates_on_red_substrate(monkeypatch):
    """A red Postgres/daemon must SKIP outreach explicitly, never send blind."""
    r = outreach.run_scheduled(foundation_gate=lambda cap: {"status": "substrate_red"},
                               ledger=object())
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
    r = outreach.queue(lg, "c1", leads, footer=footer, can_send=True, send_fn=fake_send)
    assert sent_to == ["x@y.com"]                          # real address -> real send proceeds
    assert r["sent"] == 1
