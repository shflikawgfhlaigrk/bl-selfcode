"""The recall-quality eval must itself be trustworthy: exact metric arithmetic on
engineered cases, hostile inputs rejected loudly (bad k, non-dict examples) or
neutralized (empty relevant terms — which used to mark EVERY hit relevant because
'' is a substring of everything), and the live `run_eval` wiring proven against
the real pipeline on fakes."""
from __future__ import annotations

import pytest

from utah import memory
from utah.memory import eval as recall_eval
from tests.fakes import basis, blend


class _Hit:
    def __init__(self, content):
        self.content = content


def _fake_recall(mapping):
    return lambda q, k: [_Hit(c) for c in mapping.get(q, [])][:k]


# --- exact metric arithmetic --------------------------------------------------------


def test_precision_counts_relevant_fraction_of_returned_hits():
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    rf = _fake_recall({"q": ["GOLD one", "noise", "more GOLD", "noise2"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=4)
    assert m.precision_at_k == pytest.approx(0.5)   # 2 of 4 hits relevant
    assert m.hit_rate == 1.0
    assert m.mrr == 1.0                             # first hit is relevant


def test_recall_at_k_counts_distinct_relevant_terms_found():
    labeled = [{"query": "q", "relevant": ["alpha", "beta"]}]
    rf = _fake_recall({"q": ["mentions alpha only", "noise"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.recall_at_k == pytest.approx(0.5)      # 1 of 2 relevant terms surfaced
    assert m.hit_rate == 1.0


def test_metrics_average_across_queries():
    labeled = [
        {"query": "hit", "relevant": ["X"]},
        {"query": "miss", "relevant": ["Y"]},
    ]
    rf = _fake_recall({"hit": ["contains X"], "miss": ["nope"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.queries == 2
    assert m.hit_rate == pytest.approx(0.5)
    assert m.mrr == pytest.approx(0.5)


def test_hits_beyond_k_do_not_count():
    """recall_fn over-returning must not inflate the score: only top-k is judged."""
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    rf = lambda q, k: [_Hit("noise")] * 5 + [_Hit("GOLD at rank 6")]  # noqa: E731
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 0.0 and m.mrr == 0.0


# --- hostile / malformed inputs ----------------------------------------------------


def test_k_below_one_is_rejected():
    with pytest.raises(ValueError, match="k"):
        recall_eval.evaluate_recall([{"query": "q", "relevant": ["x"]}],
                                    _fake_recall({}), k=0)


def test_non_dict_example_is_rejected_with_index():
    with pytest.raises(TypeError, match=r"labeled\[1\]"):
        recall_eval.evaluate_recall([{"query": "q", "relevant": ["x"]}, "garbage"],
                                    _fake_recall({}), k=5)


def test_empty_relevant_terms_never_match_everything():
    """'' is a substring of every hit — an empty relevant term must be dropped,
    not silently score a perfect hit_rate."""
    labeled = [{"query": "q", "relevant": ["", None]}]
    rf = _fake_recall({"q": ["junk one", "junk two"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 0.0 and m.precision_at_k == 0.0


def test_non_string_relevant_terms_are_coerced():
    labeled = [{"query": "q", "relevant": [8849]}]
    rf = _fake_recall({"q": ["Everest is 8849 metres"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 1.0


def test_dict_hits_and_none_content_tolerated():
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    rf = lambda q, k: [{"content": None}, {"content": "has GOLD"}, {}]  # noqa: E731
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 1.0
    assert m.mrr == pytest.approx(0.5)              # first relevant at rank 2


# --- the live wiring -----------------------------------------------------------------


def test_run_eval_scores_the_real_pipeline(mem):
    """run_eval wires evaluate_recall to the LIVE memory.recall — proven on the
    full pipeline over fakes, returning the plain-dict shape the deck consumes."""
    mem.embedder.register("Michael lives in Utah", basis(0))
    mem.embedder.register("where does Michael live", blend(basis(0), basis(1), 0.8))
    memory.store("Michael lives in Utah", source="fact")
    out = recall_eval.run_eval(
        [{"query": "where does Michael live", "relevant": ["Utah"]}], k=3)
    assert out["queries"] == 1 and out["hit_rate"] == 1.0 and out["k"] == 3
    assert set(out) == {"queries", "precision_at_k", "recall_at_k",
                        "hit_rate", "mrr", "k"}


def test_run_eval_with_no_labeled_set_is_all_zero(mem):
    out = recall_eval.run_eval(k=5)
    assert out["queries"] == 0 and out["hit_rate"] == 0.0
