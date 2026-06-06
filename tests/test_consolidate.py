"""Consolidation: promoted facts use the SAME pipeline as live writes (no side
door), carry source='consolidation' provenance, and a consolidation-written
fact supersedes a stale live fact (the Newman bug through the sleep path)."""
from __future__ import annotations

import json

import pytest

from utah import memory
from utah.brain import BrainUnavailable
from utah.consolidate import consolidate
from utah.memory import MemoryUnavailable
from tests.fakes import basis, blend


def _extractor(mapping):
    """Brain runner: answers the EXTRACT prompt from exchange-text mapping."""

    def respond(prompt: str) -> str:
        for needle, facts in mapping.items():
            if needle in prompt:
                return json.dumps(facts)
        return "[]"

    return respond


def test_promoted_fact_carries_consolidation_provenance(mem, fake_brain):
    memory.store("Q: I prefer tea\nA: noted", source="turn", confidence=0.5)
    fake_brain.respond = _extractor({"I prefer tea": ["Michael prefers tea"]})

    report = consolidate()

    assert report.turns_seen == 1
    assert report.facts_promoted == 1
    promoted = [r for r in mem.store.rows.values() if r.source == "consolidation"]
    assert [r.content for r in promoted] == ["Michael prefers tea"]
    assert promoted[0].confidence == pytest.approx(0.8)


def test_newman_regression_through_consolidation(mem, fake_brain):
    """A fact written by CONSOLIDATION must supersede the stale live fact —
    consolidation promoted facts without proving them was the original sin."""
    gulf_v = basis(0)
    utah_v = blend(basis(0), basis(1), 0.85)  # entity path only (cos < 0.90)
    mem.embedder.register("Michael lives in Gulf Shores", gulf_v)
    mem.embedder.register("Michael lives in Utah", utah_v)

    old = memory.store("Michael lives in Gulf Shores", source="fact", confidence=0.8)
    memory.store("Q: I moved\nA: noted, where to?", source="turn", confidence=0.5)
    fake_brain.respond = _extractor({"I moved": ["Michael lives in Utah"]})

    report = consolidate()

    assert report.facts_promoted == 1
    old_row = mem.store.get(old.id)
    assert old_row.superseded_by is not None
    assert old_row.archived is True
    new_row = mem.store.get(old_row.superseded_by)
    assert new_row.content == "Michael lives in Utah"
    assert new_row.source == "consolidation"
    # recall now returns only the new fact
    mem.embedder.register("Where does Michael live?", blend(utah_v, basis(2), 0.9))
    contents = [h.content for h in memory.recall("Where does Michael live?")]
    assert "Michael lives in Utah" in contents
    assert "Michael lives in Gulf Shores" not in contents


def test_turns_are_marked_promoted_and_not_reprocessed(mem, fake_brain):
    result = memory.store("Q: I prefer tea\nA: noted", source="turn")
    fake_brain.respond = _extractor({"I prefer tea": ["Michael prefers tea"]})
    consolidate()
    assert "promoted" in mem.store.get(result.id).tags
    second = consolidate()
    assert second.turns_seen == 0


def test_no_facts_still_marks_the_turn_done(mem, fake_brain):
    result = memory.store("Q: hi\nA: hello", source="turn")
    fake_brain.respond = "[]"
    report = consolidate()
    assert report.facts_promoted == 0
    assert "promoted" in mem.store.get(result.id).tags


def test_brain_failure_leaves_turn_unmarked_for_retry(mem, fake_brain):
    result = memory.store("Q: I moved\nA: noted", source="turn")
    fake_brain.respond = BrainUnavailable("cli down")
    report = consolidate()
    assert report.brain_failures == 1
    assert report.facts_promoted == 0
    assert "promoted" not in mem.store.get(result.id).tags
    # next pass (brain back) picks the turn up again
    fake_brain.respond = _extractor({"I moved": ["Michael moved"]})
    retry = consolidate()
    assert retry.turns_seen == 1
    assert retry.facts_promoted == 1


def test_bad_fact_is_skipped_and_the_pass_continues(mem, fake_brain):
    memory.store("Q: two facts\nA: ok", source="turn")
    mem.embedder.fail_texts.add("unembeddable fact")
    fake_brain.respond = _extractor({"two facts": ["unembeddable fact", "good fact"]})
    report = consolidate()
    assert report.facts_skipped == 1
    assert report.facts_promoted == 1
    assert "good fact" in mem.store.live_contents()


def test_duplicate_promotion_reinforces_not_duplicates(mem, fake_brain):
    memory.store("Michael prefers tea", source="fact")
    memory.store("Q: tea again\nA: noted", source="turn")
    fake_brain.respond = _extractor({"tea again": ["Michael prefers tea"]})
    report = consolidate()
    assert report.facts_promoted == 1
    rows = [r for r in mem.store.rows.values() if r.content == "Michael prefers tea"]
    assert len(rows) == 1
    assert rows[0].reinforcement == 2


def test_consolidate_runs_decay(mem, fake_brain):
    from datetime import timedelta

    memory.store("Q: old\nA: stale exchange", source="turn", confidence=0.5)
    mem.store.now += timedelta(days=90)
    fake_brain.respond = "[]"
    report = consolidate()
    assert report.archived == 1


def test_store_down_aborts_the_pass(mem, fake_brain):
    mem.store.fail = True
    with pytest.raises(MemoryUnavailable):
        consolidate()
