"""Eval-harness edges beyond the main eval suite: term cleaning subtleties,
the k=1 boundary, a recall_fn that returns None, and single-pass iterables —
each one a way the 'measured, not intended' claim could silently go wrong."""
from __future__ import annotations

import pytest

from utah.memory import eval as recall_eval


class _Hit:
    def __init__(self, content):
        self.content = content


def test_whitespace_only_relevant_term_is_dropped():
    """'   ' matches any hit containing a space run — as score-faking as '' is."""
    labeled = [{"query": "q", "relevant": ["   ", "\t"]}]
    hits = [_Hit("junk\ttext   with whitespace runs")]
    m = recall_eval.evaluate_recall(labeled, lambda q, k: hits, k=3)
    assert m.hit_rate == 0.0 and m.precision_at_k == 0.0


def test_k_of_one_is_accepted_and_judges_only_the_top_hit():
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    rf = lambda q, k: [_Hit("noise"), _Hit("GOLD")]  # noqa: E731
    m = recall_eval.evaluate_recall(labeled, rf, k=1)
    assert m.k == 1 and m.hit_rate == 0.0  # GOLD sits at rank 2, outside top-1


def test_negative_k_rejected_with_value_error():
    with pytest.raises(ValueError, match="k"):
        recall_eval.evaluate_recall([], lambda q, k: [], k=-3)


def test_recall_fn_returning_none_scores_zero_not_crash():
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    m = recall_eval.evaluate_recall(labeled, lambda q, k: None, k=5)
    assert m.queries == 1 and m.hit_rate == 0.0


def test_labeled_generator_is_consumed_exactly_once():
    """A single-pass iterable must work: the harness lists it before validating,
    so validation + scoring never double-consume."""
    gen = ({"query": "q", "relevant": ["GOLD"]} for _ in range(1))
    m = recall_eval.evaluate_recall(gen, lambda q, k: [_Hit("GOLD here")], k=2)
    assert m.queries == 1 and m.hit_rate == 1.0


def test_zero_metrics_shape_carries_the_requested_k():
    m = recall_eval.evaluate_recall([], lambda q, k: [], k=7)
    assert m.queries == 0 and m.k == 7
