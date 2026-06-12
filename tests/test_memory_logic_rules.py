"""Direct unit tests for the pure rules that were only covered indirectly through
the pipeline: entity grounding (the wrong-entity answer guard), tokenizer edges,
decay clamping on hostile inputs, and the exact archive age boundary."""
from __future__ import annotations

import math

import pytest

from utah import config
from utah.memory.logic import (
    compute_decay,
    content_words,
    entity_grounds,
    lexical_overlap,
    recall_pool,
    rrf_fuse,
    should_archive,
)


# --- entity_grounds: the tri-state wrong-entity guard ------------------------------


def test_entity_grounds_true_when_hit_entity_named_in_query():
    assert entity_grounds("how tall is Mount Everest",
                          "Mount Everest is 8849 metres") is True


def test_entity_grounds_false_when_query_names_a_different_entity():
    """The live Kilimanjaro bug: an Everest fact must NOT ground a Kilimanjaro
    question, however much generic word overlap there is."""
    assert entity_grounds("how tall is mount kilimanjaro in metres",
                          "Mount Everest is the tallest mountain at 8849 metres") is False


def test_entity_grounds_none_when_hit_has_no_entities():
    """Entity-less hits (definitions) are UNDECIDABLE — None, never False, so the
    answer gate falls back to the lexical gate instead of rejecting them."""
    assert entity_grounds("what is the speed of light",
                          "the speed of light is constant") is None


def test_entity_grounds_multiword_entity_requires_every_token():
    content = "Michael lives in Gulf Shores"
    assert entity_grounds("weather in gulf shores today", content) is True
    assert entity_grounds("the shores of lake erie", content) is False   # 'gulf' missing
    assert entity_grounds("MICHAEL's plans", content) is True            # casefolded


# --- tokenizer / overlap edges ------------------------------------------------------


def test_content_words_keeps_digits_drops_single_chars_and_stopwords():
    assert content_words("Is 5 a 42-page doc?") == ["42", "page", "doc"]
    assert content_words("") == []
    assert content_words("the of and a") == []


def test_lexical_overlap_empty_content_is_zero():
    assert lexical_overlap("michael utah", "") == 0.0


def test_lexical_overlap_is_substring_containment_by_design():
    """'live' matches 'lives' (stemming-for-free) — the documented trade-off."""
    assert lexical_overlap("where does michael live", "Michael lives in Utah") == 1.0


# --- rrf ------------------------------------------------------------------------------


def test_rrf_four_lane_fusion_orders_by_total_score():
    scores = rrf_fuse([[1, 2], [2], [2], []])
    assert scores[2] > scores[1]                       # 3 lanes beat 1 lane
    assert scores[2] == pytest.approx(1 / 62 + 2 / 61)


# --- decay clamps + archive boundary --------------------------------------------------


def test_compute_decay_clamps_negative_age_and_reinforcement():
    """Clock skew (negative age) and bad counters must not produce a score > max
    or a math domain error."""
    score = compute_decay(-3600.0, -5, 0.5)
    assert score == pytest.approx(
        config.DECAY_W_RECENCY * 1.0 + config.DECAY_W_CONFIDENCE * 0.5)


def test_compute_decay_frequency_saturates_at_one():
    big = compute_decay(0.0, 10_000, 0.0)
    assert big <= config.DECAY_W_RECENCY + config.DECAY_W_FREQUENCY + 1e-9
    assert math.isfinite(big)


def test_should_archive_exact_min_age_boundary():
    min_age = config.DECAY_MIN_AGE_DAYS * 86_400
    low = config.DECAY_ARCHIVE_BELOW - 0.01
    assert should_archive(low, min_age, "turn", superseded=False) is True
    assert should_archive(low, min_age - 1, "turn", superseded=False) is False


def test_should_archive_exact_score_boundary():
    age = (config.DECAY_MIN_AGE_DAYS + 1) * 86_400
    assert should_archive(config.DECAY_ARCHIVE_BELOW, age, "turn", False) is False
    assert should_archive(config.DECAY_ARCHIVE_BELOW - 1e-9, age, "turn", False) is True


def test_recall_pool_never_below_floor():
    assert recall_pool(1) == config.RECALL_POOL_MIN
    assert recall_pool(0) == config.RECALL_POOL_MIN
    assert recall_pool(100) == 100 * config.RECALL_POOL_FACTOR
