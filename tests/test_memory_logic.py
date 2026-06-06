"""Pure decision rules: every threshold tested directly, no I/O anywhere."""
from __future__ import annotations

import math

import pytest

from utah import config, memory
from utah.memory import Neighbor, compute_decay, decide_write, rrf_fuse, should_archive
from utah.objects import WriteAction


# --- RRF fusion -------------------------------------------------------------------

def test_rrf_both_lanes_beats_one_lane():
    scores = rrf_fuse([[1, 2], [1, 3]])
    assert scores[1] == pytest.approx(2 / 61)
    assert scores[2] == pytest.approx(1 / 62)
    assert scores[3] == pytest.approx(1 / 62)
    assert scores[1] > scores[2]


def test_rrf_rank_is_one_based_with_explicit_k():
    scores = rrf_fuse([[7]], k=10)
    assert scores[7] == pytest.approx(1 / 11)


def test_rrf_default_k_is_60():
    assert config.RRF_K == 60
    assert rrf_fuse([[5]])[5] == pytest.approx(1 / 61)


def test_rrf_empty_lanes():
    assert rrf_fuse([]) == {}
    assert rrf_fuse([[], []]) == {}


# --- content words / overlap -----------------------------------------------------

def test_content_words_strip_stopwords_and_short_tokens():
    assert memory.content_words("Where does Michael live? A 1 x") == ["michael", "live"]


def test_lexical_overlap_substring_containment():
    assert memory.lexical_overlap("where does Michael live", "Michael lives in Utah") == 1.0


def test_lexical_overlap_partial_and_empty():
    assert memory.lexical_overlap("Michael banana", "Michael lives in Utah") == 0.5
    assert memory.lexical_overlap("the of and", "anything") == 0.0


# --- answer gate ---------------------------------------------------------------------

def test_gate_requires_both_thresholds():
    assert memory.passes_gate(config.ANSWER_MIN_SIM, config.ANSWER_MIN_OVERLAP)
    assert not memory.passes_gate(config.ANSWER_MIN_SIM - 0.01, 1.0)
    assert not memory.passes_gate(1.0, config.ANSWER_MIN_OVERLAP - 0.01)
    assert not memory.passes_gate(0.0, 0.0)


# --- write decision -------------------------------------------------------------------

def _nb(id: int, content: str, sim: float) -> Neighbor:
    return Neighbor(id, content, sim)


def test_dedup_reinforces_at_threshold():
    decision = decide_write("Michael lives in Utah", [_nb(1, "Michael lives in Utah.", 0.995)])
    assert decision.action is WriteAction.REINFORCED
    assert decision.reinforce_id == 1


def test_exact_text_match_reinforces_even_below_sim_threshold():
    decision = decide_write("Michael  lives in   UTAH", [_nb(2, "michael lives in utah", 0.91)])
    assert decision.action is WriteAction.REINFORCED
    assert decision.reinforce_id == 2


def test_paraphrase_supersede_no_entity_needed():
    decision = decide_write(
        "the deadline moved to monday", [_nb(3, "the deadline is friday", 0.90)]
    )
    assert decision.action is WriteAction.INSERTED
    assert decision.supersede_ids == [3]


def test_below_paraphrase_threshold_without_shared_entity_inserts_clean():
    decision = decide_write(
        "the deadline moved to monday", [_nb(3, "the deadline is friday", 0.89)]
    )
    assert decision.supersede_ids == []


def test_entity_path_supersedes_attribute_change_below_090():
    """The Newman regression at the rule level: same entity, changed value,
    cosine in [0.78, 0.90) MUST supersede."""
    decision = decide_write(
        "Michael lives in Utah", [_nb(4, "Michael lives in Gulf Shores", 0.85)]
    )
    assert decision.action is WriteAction.INSERTED
    assert decision.supersede_ids == [4]


def test_entity_path_threshold_is_exact():
    old = "Michael lives in Gulf Shores"
    at = decide_write("Michael lives in Utah", [_nb(5, old, config.SUPERSEDE_ENT)])
    below = decide_write("Michael lives in Utah", [_nb(5, old, config.SUPERSEDE_ENT - 0.001)])
    assert at.supersede_ids == [5]
    assert below.supersede_ids == []


def test_entity_path_requires_shared_entity():
    decision = decide_write(
        "Michael lives in Utah", [_nb(6, "Newman lives in Gulf Shores", 0.85)]
    )
    assert decision.supersede_ids == []


def test_entity_match_is_case_insensitive():
    decision = decide_write(
        "MICHAEL lives in Utah", [_nb(7, "Michael lives in Gulf Shores", 0.80)]
    )
    assert decision.supersede_ids == [7]


def test_whole_window_is_scanned_not_just_top1():
    """The Newman bug was a top-1-only check; all contradicting rows go."""
    decision = decide_write(
        "Michael lives in Utah",
        [
            _nb(10, "Michael keeps a list of project ideas", 0.86),  # no city, but same entity
            _nb(11, "Michael lives in Gulf Shores", 0.84),
            _nb(12, "Michael lives in Adelaide", 0.80),
            _nb(13, "Newman lives in Adelaide", 0.80),               # different entity
            _nb(14, "Michael lives somewhere", 0.50),                # below both thresholds
        ],
    )
    assert decision.action is WriteAction.INSERTED
    assert 11 in decision.supersede_ids and 12 in decision.supersede_ids
    assert 13 not in decision.supersede_ids
    assert 14 not in decision.supersede_ids


def test_no_neighbors_inserts_clean():
    decision = decide_write("anything at all", [])
    assert decision.action is WriteAction.INSERTED
    assert decision.supersede_ids == []


# --- decay ---------------------------------------------------------------------------

def test_decay_fresh_reinforced_confident_row_is_high():
    assert compute_decay(0, 20, 1.0) == pytest.approx(0.5 + 0.3 + 0.2)


def test_decay_halflife_recency_term():
    score = compute_decay(config.DECAY_HALFLIFE_SECONDS, 1, 0.0)
    assert score == pytest.approx(0.5 * math.exp(-1) + 0.3 * math.log(2) / 3)


def test_decay_floor_for_unreinforced_turn():
    """conf-0.5 unreinforced rows floor at ~0.169 — which is why the archive
    threshold is 0.25 (the prototype's 0.15 was unreachable)."""
    floor = compute_decay(1e12, 1, 0.5)
    assert floor == pytest.approx(0.3 * math.log(2) / 3 + 0.1, abs=1e-6)
    assert floor > 0.15           # the old threshold could never fire
    assert floor < config.DECAY_ARCHIVE_BELOW  # the new one can


def test_should_archive_rules():
    day = 86_400
    # stale unreinforced turn -> archived
    stale = compute_decay(90 * day, 1, 0.5)
    assert should_archive(stale, 90 * day, "turn", superseded=False)
    # protected sources never archive
    assert not should_archive(stale, 90 * day, "fact", superseded=False)
    assert not should_archive(stale, 90 * day, "consolidation", superseded=False)
    # superseded rows are left alone (already out of recall)
    assert not should_archive(stale, 90 * day, "turn", superseded=True)
    # grace period
    assert not should_archive(0.0, 6 * day, "turn", superseded=False)
    # healthy score survives
    assert not should_archive(0.5, 90 * day, "turn", superseded=False)


def test_reinforced_rows_never_fade():
    """A row touched ~20 times keeps decay_score >= 0.3 + confidence term."""
    score = compute_decay(1e12, 19, 0.5)
    assert score >= 0.3
    assert not should_archive(score, 1e12, "turn", superseded=False)


def test_recall_pool_floor_and_factor():
    assert memory.recall_pool(5) == 20
    assert memory.recall_pool(10) == 40
