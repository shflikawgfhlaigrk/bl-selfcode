"""Rerank degrade paths beyond the basics: malformed score VALUES (not just a wrong
count) and non-finite scores must degrade to neutral zeros — recall must never die or
get NaN-poisoned inside the rerank boundary."""
from __future__ import annotations

import threading

from utah import rerank


class _BadValues:
    """Returns the right NUMBER of scores, but values float() chokes on."""

    def rerank(self, query, docs):
        return ["not-a-number"] * len(docs)


class _NaNValues:
    """Returns NaN scores — would silently poison the recall sort if passed through."""

    def rerank(self, query, docs):
        return [float("nan")] * len(docs)


class _InfValues:
    def rerank(self, query, docs):
        return [float("inf")] * len(docs)


def test_non_numeric_score_values_degrade_to_zeros_not_an_exception():
    rerank.set_reranker(_BadValues())
    assert rerank.rerank("q", ["a", "b"]) == [0.0, 0.0]


def test_nan_scores_degrade_to_zeros():
    rerank.set_reranker(_NaNValues())
    assert rerank.rerank("q", ["a", "b"]) == [0.0, 0.0]


def test_inf_scores_degrade_to_zeros():
    rerank.set_reranker(_InfValues())
    assert rerank.rerank("q", ["a"]) == [0.0]


def test_get_reranker_returns_one_instance_across_threads():
    """The process-global default must be a singleton even under concurrent first-touch
    (it lazily owns a model — two instances would double-load it)."""
    rerank.set_reranker(None)
    seen: list = []

    def grab():
        seen.append(rerank.get_reranker())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(r) for r in seen}) == 1
