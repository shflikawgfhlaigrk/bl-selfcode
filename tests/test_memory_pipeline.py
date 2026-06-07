"""The full memory pipeline on the fake backend: the SAME orchestration code as
production (store/recall/answer/decay), only models and storage are fakes.
Includes the Newman-class regression test."""
from __future__ import annotations

from datetime import timedelta

import pytest

from utah import config, memory
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable
from utah.objects import WriteAction
from tests.fakes import basis, blend


# --- admission gate ---------------------------------------------------------------

def test_admission_denies_empty_content(mem):
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(AdmissionDenied, match="empty"):
            memory.store(bad)
    assert mem.store.rows == {}


def test_admission_denies_unknown_source(mem):
    """No backfill, no synthetic — confabulation dies at admission."""
    for source in ("backfill", "synthetic", "scraped", ""):
        with pytest.raises(AdmissionDenied, match="source"):
            memory.store("a fact", source=source)
    assert mem.store.rows == {}


def test_admission_denies_oversize_content(mem):
    with pytest.raises(AdmissionDenied, match="chars"):
        memory.store("x" * (config.MAX_CONTENT_CHARS + 1))


def test_admission_denies_bad_confidence(mem):
    with pytest.raises(AdmissionDenied, match="confidence"):
        memory.store("a fact", confidence=1.5)
    with pytest.raises(AdmissionDenied, match="confidence"):
        memory.store("a fact", confidence=-0.1)


def test_admission_requires_embedding(mem):
    """Embed failure -> nothing is stored. A row without a vector never exists."""
    mem.embedder.fail = True
    with pytest.raises(EmbedError):
        memory.store("a fact")
    assert mem.store.rows == {}


def test_admitted_write_carries_source_and_entities(mem):
    result = memory.store("Michael lives in Utah", source="fact", confidence=0.8)
    assert result.action is WriteAction.INSERTED
    row = mem.store.get(result.id)
    assert row.source == "fact"
    assert row.confidence == 0.8
    names = mem.store.entity_names([result.id])[result.id]
    assert names == {"michael", "utah"}


# --- dedup-reinforce -----------------------------------------------------------------

def test_duplicate_reinforces_instead_of_inserting(mem):
    first = memory.store("Michael prefers tea", source="fact")
    second = memory.store("Michael prefers tea", source="fact")
    assert second.action is WriteAction.REINFORCED
    assert second.id == first.id
    assert len(mem.store.rows) == 1
    assert mem.store.get(first.id).reinforcement == 2


def test_near_identical_embedding_reinforces(mem):
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("Michael prefers tea.", blend(basis(0), basis(1), 0.997))
    first = memory.store("Michael prefers tea", source="fact")
    second = memory.store("Michael prefers tea.", source="fact")
    assert second.action is WriteAction.REINFORCED
    assert second.id == first.id


# --- supersede (both paths) -------------------------------------------------------------

def test_paraphrase_supersede_through_pipeline(mem):
    mem.embedder.register("the demo is on friday", basis(0))
    mem.embedder.register("the demo moved to monday", blend(basis(0), basis(1), 0.92))
    old = memory.store("the demo is on friday", source="fact")
    new = memory.store("the demo moved to monday", source="fact")
    assert new.superseded == [old.id]
    old_row = mem.store.get(old.id)
    assert old_row.superseded_by == new.id
    assert old_row.archived is True


def test_newman_regression_same_entity_attribute_change(mem):
    """THE regression: 'Michael lives in Gulf Shores' -> 'Michael lives in Utah'
    with cosine BELOW 0.90 must supersede via the shared-entity path, the old
    row must leave recall, and the new fact must be returned and gated."""
    gulf_v = basis(0)
    utah_v = blend(basis(0), basis(1), 0.85)          # cosine 0.85: entity path only
    query_v = blend(utah_v, basis(2), 0.90)           # close to the new fact
    mem.embedder.register("Michael lives in Gulf Shores", gulf_v)
    mem.embedder.register("Michael lives in Utah", utah_v)
    mem.embedder.register("Where does Michael live?", query_v)

    old = memory.store("Michael lives in Gulf Shores", source="fact", confidence=0.8)
    new = memory.store("Michael lives in Utah", source="fact", confidence=0.8)

    # superseded + archived, never deleted
    old_row = mem.store.get(old.id)
    assert old_row.superseded_by == new.id
    assert old_row.archived is True
    assert old.id in mem.store.rows  # still present (audit trail)

    # recall returns the new fact and NOT the stale one
    hits = memory.recall("Where does Michael live?")
    contents = [h.content for h in hits]
    assert "Michael lives in Utah" in contents
    assert "Michael lives in Gulf Shores" not in contents

    # and the answer gate confidently answers the new fact from memory
    answer, _ = memory.answer("Where does Michael live?")
    assert answer == "Michael lives in Utah"


def test_supersede_fires_even_when_contradiction_is_not_top1(mem):
    """The live-test failure mode: another row sits closer than the stale fact."""
    gulf_v = basis(0)
    distractor_v = blend(basis(0), basis(1), 0.60)
    utah_v = blend(basis(0), basis(2), 0.82)          # gulf sim 0.82 (entity path)
    mem.embedder.register("Michael lives in Gulf Shores", gulf_v)
    mem.embedder.register("Michael is planning a trip", distractor_v)
    mem.embedder.register("Michael lives in Utah", utah_v)

    old = memory.store("Michael lives in Gulf Shores", source="fact")
    memory.store("Michael is planning a trip", source="fact")
    new = memory.store("Michael lives in Utah", source="fact")

    assert old.id in new.superseded
    assert mem.store.get(old.id).superseded_by == new.id


def test_multiple_stale_variants_all_superseded(mem):
    import math

    gulf_v = basis(0)
    adelaide_v = blend(basis(0), basis(1), 0.50)      # far from gulf: both stay live
    # midpoint vector: cosine ~0.866 to BOTH stale variants (entity-path zone)
    mid = [(x + y) for x, y in zip(gulf_v, adelaide_v)]
    norm = math.sqrt(sum(x * x for x in mid))
    mid = [x / norm for x in mid]
    mem.embedder.register("Michael lives in Gulf Shores", gulf_v)
    mem.embedder.register("Michael lives in Adelaide", adelaide_v)
    mem.embedder.register("Michael lives in Utah", mid)

    old1 = memory.store("Michael lives in Gulf Shores", source="fact")
    old2 = memory.store("Michael lives in Adelaide", source="fact")
    assert mem.store.get(old1.id).superseded_by is None  # 0.50 apart: both live
    new = memory.store("Michael lives in Utah", source="fact")
    assert set(new.superseded) == {old1.id, old2.id}


def test_unrelated_entities_do_not_supersede(mem):
    mem.embedder.register("Michael lives in Utah", basis(0))
    mem.embedder.register("Newman lives in Adelaide", blend(basis(0), basis(1), 0.85))
    a = memory.store("Michael lives in Utah", source="fact")
    b = memory.store("Newman lives in Adelaide", source="fact")
    assert b.superseded == []
    assert mem.store.get(a.id).superseded_by is None


# --- recall ----------------------------------------------------------------------------

def test_recall_empty_store_and_empty_query(mem):
    assert memory.recall("anything") == []
    assert memory.recall("") == []
    assert memory.recall("   ") == []


def test_recall_excludes_archived_rows(mem):
    result = memory.store("an old stray note about kiwis", source="turn")
    mem.store.get(result.id).archived = True
    assert memory.recall("stray note about kiwis") == []


def test_recall_hybrid_sparse_lane_finds_lexical_match(mem):
    """A row whose vector is orthogonal to the query still surfaces via FTS."""
    mem.embedder.register("the vault passphrase is kiwi-canyon", basis(0))
    mem.embedder.register("vault passphrase", basis(5))  # orthogonal query
    memory.store("the vault passphrase is kiwi-canyon", source="fact")
    hits = memory.recall("vault passphrase")
    assert [h.content for h in hits] == ["the vault passphrase is kiwi-canyon"]
    assert hits[0].sim == 0.0  # surfaced by the sparse lane only


def test_recall_degrades_to_sparse_when_embedder_down(mem):
    memory.store("the vault passphrase is kiwi-canyon", source="fact")
    mem.embedder.fail = True
    hits = memory.recall("vault passphrase")
    assert [h.content for h in hits] == ["the vault passphrase is kiwi-canyon"]


def test_recall_entity_boost_breaks_ties(mem):
    """With a neutral reranker, the row sharing the query's entity wins."""
    mem.embedder.register("Newman filed the report", blend(basis(0), basis(1), 0.70))
    mem.embedder.register("someone filed the report", blend(basis(0), basis(2), 0.70))
    mem.embedder.register("what did Newman file?", basis(0))
    memory.store("someone filed the report", source="fact")
    memory.store("Newman filed the report", source="fact")
    hits = memory.recall("what did Newman file?")
    assert hits[0].content == "Newman filed the report"
    assert hits[0].score > hits[1].score


def test_recall_touches_returned_rows(mem):
    result = memory.store("Michael prefers tea", source="fact")
    before = mem.store.get(result.id).reinforcement
    memory.recall("Michael prefers tea")
    assert mem.store.get(result.id).reinforcement == before + 1


def test_recall_store_down_raises_memory_unavailable(mem):
    mem.store.fail = True
    with pytest.raises(MemoryUnavailable):
        memory.recall("anything")


# --- answer gate (through the pipeline) ---------------------------------------------------

def test_answer_returns_none_when_store_empty(mem):
    assert memory.answer("anything") == (None, [])


def test_answer_gate_blocks_low_similarity(mem):
    """Lexical match alone (sim below 0.45) must NOT answer from memory."""
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("does Michael prefer tea?", blend(basis(0), basis(1), 0.40))
    memory.store("Michael prefers tea", source="fact")
    answer, hits = memory.answer("does Michael prefer tea?")
    assert answer is None
    assert hits  # context still offered to the brain


def test_answer_gate_blocks_low_overlap(mem):
    """Semantic closeness alone (off-topic words) must NOT answer from memory."""
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("what hot drink does he like best", blend(basis(0), basis(1), 0.80))
    memory.store("Michael prefers tea", source="fact")
    answer, _ = memory.answer("what hot drink does he like best")
    assert answer is None


def test_answer_gate_passes_when_both_hold(mem):
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("Michael prefers what?", blend(basis(0), basis(1), 0.60))
    memory.store("Michael prefers tea", source="fact")
    answer, hits = memory.answer("Michael prefers what?")
    assert answer == "Michael prefers tea"
    assert hits[0].sim >= config.ANSWER_MIN_SIM


def test_answer_gate_rejects_wrong_entity_even_with_word_overlap(mem):
    """The live bug: an 'Everest' fact answered a 'Kilimanjaro' question because
    generic words (tall, mount, metres) gave a passing overlap. The hit's entity is
    NOT named in the query → it must NOT be served; fall to the brain (→ learn)."""
    fact = "Mount Everest is the tallest mountain at 8849 metres"
    mem.embedder.register(fact, basis(0))
    mem.embedder.register("how tall is mount kilimanjaro in metres", blend(basis(0), basis(1), 0.70))
    memory.store(fact, source="fact")
    answer, hits = memory.answer("how tall is mount kilimanjaro in metres")
    assert answer is None   # wrong entity → not answered (the fix)
    assert hits             # still offered to the brain as context


def test_answer_gate_answers_when_entity_matches(mem):
    """The right-entity fact still answers verbatim — the guard only rejects the wrong
    subject, never the correct one."""
    fact = "Mount Kilimanjaro is 5895 metres tall"
    mem.embedder.register(fact, basis(0))
    mem.embedder.register("how tall is mount kilimanjaro in metres", blend(basis(0), basis(1), 0.70))
    memory.store(fact, source="fact")
    answer, _ = memory.answer("how tall is mount kilimanjaro in metres")
    assert answer == fact


def test_answer_gate_falls_back_to_lexical_for_entity_less_hit(mem):
    """A hit with no proper-noun entity (a definition) still answers on the lexical
    gate — the entity signal only REJECTS wrong-entity hits, never blocks entity-less
    ones."""
    fact = "the speed of light is 299792 kilometres per second"
    mem.embedder.register(fact, basis(0))
    mem.embedder.register("what is the speed of light", blend(basis(0), basis(1), 0.70))
    memory.store(fact, source="fact")
    answer, _ = memory.answer("what is the speed of light")
    assert answer == fact


# --- decay (through the pipeline) -----------------------------------------------------------

def test_decay_archives_stale_turns_but_not_facts(mem):
    stale_turn = memory.store("Q: hi\nA: hello there", source="turn", confidence=0.5)
    stale_fact = memory.store("Michael lives in Utah", source="fact", confidence=0.5)
    stale_cons = memory.store("Utah is step one", source="consolidation", confidence=0.5)
    mem.store.now += timedelta(days=90)
    fresh_turn = memory.store("Q: new\nA: fresh exchange", source="turn", confidence=0.5)

    archived = memory.decay()
    assert archived == 1
    assert mem.store.get(stale_turn.id).archived is True
    assert mem.store.get(stale_fact.id).archived is False
    assert mem.store.get(stale_cons.id).archived is False
    assert mem.store.get(fresh_turn.id).archived is False


def test_decay_spares_reinforced_rows(mem):
    busy = memory.store("Q: status\nA: all green", source="turn", confidence=0.5)
    for _ in range(25):
        mem.store.reinforce(busy.id)
    mem.store.now += timedelta(days=90)
    memory.decay()
    assert mem.store.get(busy.id).archived is False


def test_decay_never_deletes(mem):
    result = memory.store("Q: hi\nA: hello", source="turn", confidence=0.5)
    mem.store.now += timedelta(days=120)
    memory.decay()
    assert result.id in mem.store.rows  # archived, not gone
