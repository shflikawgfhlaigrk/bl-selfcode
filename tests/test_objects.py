"""Core types: frozen for real (mutation = error, not a comment)."""
from __future__ import annotations

import msgspec
import pytest

from utah.objects import (
    ConsolidationReport,
    Hit,
    Reply,
    ReplySource,
    WriteAction,
    WriteDecision,
    WriteResult,
)


def test_hit_is_frozen():
    hit = Hit(id=1, content="x", source="user", score=0.5, sim=0.4)
    with pytest.raises(AttributeError):
        hit.content = "mutated"


def test_reply_is_frozen_and_defaults_empty_hits():
    reply = Reply(text="hi", source=ReplySource.BRAIN)
    assert reply.hits == []
    with pytest.raises(AttributeError):
        reply.text = "mutated"


def test_mutable_default_is_not_shared():
    a = Reply(text="a", source=ReplySource.BRAIN)
    b = Reply(text="b", source=ReplySource.BRAIN)
    assert a.hits is not b.hits


def test_unknown_field_is_an_error_not_a_silent_bag():
    with pytest.raises(TypeError):
        Hit(id=1, content="x", source="user", score=0.0, payload={"drift": True})


def test_enums_not_strings():
    assert ReplySource("memory") is ReplySource.MEMORY
    assert WriteAction("inserted") is WriteAction.INSERTED
    with pytest.raises(ValueError):
        ReplySource("hallucinated")


def test_reply_serializes_to_json_one_model_for_the_wire():
    reply = Reply(
        text="t",
        source=ReplySource.MEMORY,
        hits=[Hit(id=1, content="c", source="fact", score=1.0, sim=0.9)],
    )
    raw = msgspec.json.encode(reply)
    decoded = msgspec.json.decode(raw, type=Reply)
    assert decoded == reply


def test_write_decision_defaults():
    d = WriteDecision(action=WriteAction.INSERTED)
    assert d.reinforce_id is None
    assert d.supersede_ids == []


def test_write_result_and_report_shape():
    r = WriteResult(id=3, action=WriteAction.REINFORCED)
    assert r.superseded == []
    report = ConsolidationReport(
        turns_seen=1, facts_promoted=2, facts_skipped=0, brain_failures=0, archived=4
    )
    assert report.archived == 4
