"""Product-row + reply contracts in utah.objects — the edges test_objects.py and
test_product_row_contracts.py leave open: shared mutable defaults, the honest
failure mode of ``from_mapping`` (a malformed row fails LOUDLY, it does not produce
a half-typed object), and wire round-trips for the product rows."""
from __future__ import annotations

import msgspec
import pytest

from utah.objects import (
    Fire,
    Hit,
    Lead,
    OutreachRow,
    ReplySource,
    WriteAction,
    WriteDecision,
)


# --- mutable defaults are per-instance, never shared ---------------------------

def test_lead_default_contact_is_not_shared():
    a, b = Lead(name="A"), Lead(name="B")
    assert a.contact == {} and a.contact is not b.contact


def test_write_decision_default_supersede_list_is_not_shared():
    a = WriteDecision(action=WriteAction.INSERTED)
    b = WriteDecision(action=WriteAction.INSERTED)
    assert a.supersede_ids == [] and a.supersede_ids is not b.supersede_ids


# --- from_mapping: extras ignored, required fields still enforced ---------------

def test_from_mapping_drops_extras_but_keeps_required_enforcement():
    lead = Lead.from_mapping({"name": "Joe's", "rogue": True, "id": 3})
    assert lead.name == "Joe's" and lead.id == 3
    assert "rogue" not in lead.as_dict()
    with pytest.raises(TypeError):
        Lead.from_mapping({"kind": "diner"})   # no name -> loud, not half-typed


def test_from_mapping_none_is_empty_and_fails_on_required():
    with pytest.raises(TypeError):
        Fire.from_mapping(None)                # engine/direction are required


# --- wire round-trips: one model for jsonb/json -----------------------------------

def test_lead_json_round_trip_preserves_contact_bag():
    lead = Lead(name="Glam Salon", kind="salon",
                contact={"email": "g@x.com", "phone": "+1555"}, region="Newnan GA")
    decoded = msgspec.json.decode(msgspec.json.encode(lead), type=Lead)
    assert decoded == lead
    assert decoded.contact["phone"] == "+1555"


def test_fire_defaults_stay_unfabricated_through_round_trip():
    fire = Fire(engine="meanrev", direction="short")
    decoded = msgspec.json.decode(msgspec.json.encode(fire), type=Fire)
    assert decoded.pnl is None and decoded.outcome is None    # open until a broker closes
    assert decoded.synthetic is False and decoded.entry is None


def test_outreach_row_as_dict_round_trips_through_from_mapping():
    row = OutreachRow(recipient="x@y.com", campaign="smb_no_website", channel="sms")
    assert OutreachRow.from_mapping(row.as_dict()) == row


# --- provenance enum is closed -----------------------------------------------------

def test_reply_source_values_are_the_exact_provenance_set():
    assert {m.value for m in ReplySource} == {
        "memory", "social", "capability", "local", "brain", "learned", "unavailable"}
