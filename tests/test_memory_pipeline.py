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


def test_admission_rejects_turn_shaped_and_deadcode_as_facts(mem):
    """Confabulation guard: a durable fact can't be a stored conversation turn or a dead
    old-AceOS code dump — the exact shapes that flooded the fact source (rap lyrics, mic
    garble, [acesd/...] code). The 'turn' source IS legitimately Q/A, so it stays admissible."""
    for bad in ("Q: where do I live\nA: noted", "[acesd/voice/wake.py] old code description"):
        with pytest.raises(AdmissionDenied, match="durable fact"):
            memory.store(bad, source="fact")
    assert memory.store("Michael lives in Gulf Shores", source="fact")   # real fact admits
    assert memory.store("Q: hi\nA: hello", source="turn")                # a genuine turn admits


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


def test_answer_never_re_serves_a_conversational_turn(mem):
    """A 'turn' (record of a past exchange) must NOT be re-served as the authoritative
    answer even when it's the top, gate-passing hit — echoing a past hedge ossifies it and
    bypasses fresh reasoning over newer context. It stays a hit (context); answer is None."""
    past = "Q: which oauth function reuses the token\nA: I can't name it, the code isn't in my context"
    mem.embedder.register(past, basis(0))
    mem.embedder.register("which oauth function reuses the token", blend(basis(0), basis(1), 0.90))
    memory.store(past, source="turn", confidence=0.5)
    answer, hits = memory.answer("which oauth function reuses the token")
    assert answer is None                              # NOT re-served as the answer
    assert any(h.source == "turn" for h in hits)       # still available to the brain as context


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


# --- curated lane + source prior (keep the books/identity from being buried) -------

def test_source_prior_lifts_curated_over_a_tied_fact(mem):
    """Equal dense similarity + neutral reranker → the source-authority prior decides:
    a curated 'knowledge' row outranks the 'fact' it would otherwise tie with."""
    mem.embedder.register("how do I gain power", basis(0))
    mem.embedder.register("a stray fact about power", blend(basis(0), basis(2), 0.6))
    mem.embedder.register("48 Laws: conceal your intentions", blend(basis(0), basis(3), 0.6))
    memory.store("a stray fact about power", source="fact")
    memory.store("48 Laws: conceal your intentions", source="knowledge")
    hits = memory.recall("how do I gain power", k=2)
    assert hits[0].source == "knowledge"                     # the prior tipped the tie
    assert {h.source for h in hits} == {"knowledge", "fact"}  # both still present


def test_curated_lane_rescues_a_row_the_general_pool_drops(mem):
    """The diagnosed root cause: a relevant curated row never reaching the reranker
    because the fact pile fills the candidate pool. The curated lane gives it a slot."""
    mem.embedder.register("seeking power and influence", basis(0))
    # 25 facts CLOSER to the query than the lone knowledge row → they fill the pool (20).
    for i in range(25):
        v = blend(basis(0), basis(i + 4), 0.85)
        mem.embedder.register(f"fact number {i} about assorted things", v)
        memory.store(f"fact number {i} about assorted things", source="fact")
    kv = blend(basis(0), basis(1), 0.5)                      # FARTHER than every fact
    mem.embedder.register("48 Laws of Power: master your timing", kv)
    memory.store("48 Laws of Power: master your timing", source="knowledge")

    # the general dense pool DROPS it (25 closer facts > pool of 20)…
    pool_ids = [r.content for r in mem.store.dense_search(basis(0), 20)]
    assert "48 Laws of Power: master your timing" not in pool_ids
    # …but recall surfaces it anyway — it rode the curated lane to the reranker.
    hits = memory.recall("seeking power and influence", k=5)
    assert any(h.source == "knowledge" for h in hits)


def test_code_lane_rescues_a_code_function_the_general_pool_drops(mem):
    """A code/self question's answer is a specific FUNCTION chunk. Chatty 'turn' rows sit
    CLOSER to the query and swamp the general pool — without the dedicated code lane the
    function never reaches the reranker (the diagnosed miss where the brain couldn't name
    its own functions). The code lane gives it a slot."""
    for i in range(25):                                 # 25 turns CLOSER than the code fn
        v = blend(basis(0), basis(i + 4), 0.85)
        mem.embedder.register(f"turn {i}: earlier chat about google tokens and auth", v)
        memory.store(f"turn {i}: earlier chat about google tokens and auth", source="turn")
    fn = ("FILE utah/integrations/oauth.py — copy_ace_to_utah_if_correct "
          "def copy_ace_to_utah_if_correct(): reuse the existing google token")
    mem.embedder.register(fn, blend(basis(0), basis(1), 0.5))   # FARTHER than every turn
    memory.store(fn, source="code")

    # the general dense pool DROPS it (25 closer turns > pool)…
    assert fn not in [r.content for r in mem.store.dense_search(basis(0), 20)]
    # …but recall surfaces it — it rode the dedicated CODE lane to the reranker.
    hits = memory.recall("which function reuses the existing google token", k=5)
    assert any(h.source == "code" and "copy_ace_to_utah_if_correct" in h.content for h in hits)


def test_source_prior_never_overrides_a_strong_match(mem):
    """A strongly-relevant fact (high reranker score) must still beat a weakly-relevant
    curated row — the prior tips the low-confidence regime, it is not a trump card."""
    from utah import rerank as rerank_mod
    from tests.fakes import ScriptedReranker

    mem.embedder.register("where does Michael live", basis(0))
    mem.embedder.register("Michael lives in Gulf Shores, Alabama", blend(basis(0), basis(2), 0.8))
    mem.embedder.register("48 Laws: master your timing", blend(basis(0), basis(3), 0.5))
    memory.store("Michael lives in Gulf Shores, Alabama", source="fact")
    memory.store("48 Laws: master your timing", source="knowledge")
    rerank_mod.set_reranker(ScriptedReranker(lambda q, d: 9.0 if "Gulf Shores" in d else -8.0))
    try:
        hits = memory.recall("where does Michael live", k=2)
        assert hits[0].source == "fact" and "Gulf Shores" in hits[0].content
    finally:
        rerank_mod.set_reranker(mem.reranker)                # restore the neutral reranker
