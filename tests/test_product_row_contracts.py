"""B4: the product-row contracts (Lead / Fire / OutreachRow) — one typed shape that the
leads producer, the outreach consumer, and the ledger all agree on. A field-name drift is
now a type error, not a silent missed send."""
from __future__ import annotations

import msgspec
import pytest

from utah.objects import Fire, Lead, OutreachRow
from utah.product import outreach


def test_structs_are_frozen_and_typed():
    lead = Lead(name="Joe's Diner", kind="restaurant", contact={"email": "joe@x.com"})
    assert lead.source == "osm" and lead.id is None
    with pytest.raises((AttributeError, TypeError)):
        lead.name = "x"   # frozen


def test_from_mapping_ignores_extras_and_round_trips():
    raw = {"name": "Ace Plumbing", "kind": "plumber", "contact": {"phone": "+1555"},
           "source": "google_maps", "stray_key": "ignored", "id": 7}
    lead = Lead.from_mapping(raw)
    assert lead.name == "Ace Plumbing" and lead.id == 7 and lead.source == "google_maps"
    assert "stray_key" not in lead.as_dict()
    # as_dict feeds the dict-shaped consumers unchanged
    assert lead.as_dict()["contact"] == {"phone": "+1555"}


def test_lead_contract_flows_producer_to_outreach_consumer():
    """A Lead built the way leads.py builds it is consumed correctly by outreach
    (channel selection + compose), proving producer and consumer agree on the shape."""
    lead = Lead(name="Glam Salon", kind="salon",
                contact={"email": "glam@x.com", "phone": "+15551234567"}).as_dict()
    assert outreach.pick_channel(lead["contact"], "email") == "email"
    msg = outreach.compose(lead, outreach.SMB_OUTREACH_CAMPAIGN,
                           {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "STOP"})
    assert "Glam Salon" in msg["body"]


def test_fire_and_outreachrow_contracts():
    fire = Fire(engine="breakout", direction="long", entry=29374.0)
    assert fire.synthetic is False and fire.pnl is None      # never fabricated until closed
    assert msgspec.structs.asdict(fire)["engine"] == "breakout"
    row = OutreachRow.from_mapping({"recipient": "x@y.com", "campaign": "smb_no_website",
                                    "channel": "email", "extra": 1})
    assert row.recipient == "x@y.com" and "extra" not in row.as_dict()