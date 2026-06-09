"""CAN-SPAM gate must require a COMPLETE postal address, not just non-placeholder.

A bare street ("28 Dogwood Rd") is non-compliant: CAN-SPAM requires a valid physical
postal address (street + city/state + ZIP). Sending cold mail at volume with an
incomplete address is real legal exposure, so the SEND gate refuses until it is complete
— the gate lives in the machine, not the docs. A 5-digit ZIP is the completeness proxy.
"""
from __future__ import annotations

from utah import config
from utah.product import outreach


def test_bare_street_is_not_complete():
    assert config._canspam_is_complete("28 Dogwood Rd") is False


def test_full_address_is_complete():
    assert config._canspam_is_complete("28 Dogwood Rd, Cartersville, GA 30120") is True


def test_placeholder_is_not_complete():
    assert config._canspam_is_complete(config.CANSPAM_PLACEHOLDER) is False


def test_canspam_configured_requires_completeness(monkeypatch, tmp_path):
    bad = tmp_path / "business.json"
    bad.write_text('{"physical_address": "28 Dogwood Rd"}', encoding="utf-8")
    monkeypatch.delenv("UTAH_CANSPAM_ADDRESS", raising=False)
    monkeypatch.setattr(config, "BUSINESS_CREDS", bad)
    assert config.canspam_configured() is False


def test_footer_gate_refuses_incomplete_address():
    incomplete = {"address": "28 Dogwood Rd", "unsubscribe": "Reply STOP to opt out."}
    assert outreach._footer_is_real(incomplete) is False
    complete = {"address": "28 Dogwood Rd, Cartersville, GA 30120",
                "unsubscribe": "Reply STOP to opt out."}
    assert outreach._footer_is_real(complete) is True


def test_queue_refuses_send_with_incomplete_address():
    """can_send=True + incomplete address => 0 sent, gate documented (queue-only)."""
    sent: list[str] = []

    class _Lg:
        def log_outreach(self, *a, **k):
            return True

        def is_contacted(self, *a, **k):
            return False

    def fake_send(to, subject, body):
        sent.append(to)
        return {"sent": True}

    footer = {"address": "28 Dogwood Rd", "unsubscribe": "Reply STOP to opt out."}
    leads = [{"name": "Joe's Plumbing", "kind": "plumber", "source": "osm",
              "contact": {"email": "x@y.com"}}]
    r = outreach.queue(_Lg(), "c1", leads, footer=footer, can_send=True, send_fn=fake_send)
    assert sent == []          # nothing left the building
    assert r["sent"] == 0
