"""RAG recall-quality eval (precision@k / recall@k / MRR) + the injectable-NER upgrade
path — makes memory recall a MEASURED claim, not just a no-fab intention."""
from __future__ import annotations

from utah import entities
from utah.memory import eval as recall_eval


class _Hit:
    def __init__(self, content): self.content = content


def _fake_recall(mapping):
    return lambda q, k: [_Hit(c) for c in mapping.get(q, [])][:k]


def test_perfect_recall_scores_one():
    labeled = [{"query": "where does Michael live", "relevant": ["Utah"]}]
    rf = _fake_recall({"where does Michael live": ["Michael lives in Utah", "noise"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 1.0 and m.mrr == 1.0 and m.recall_at_k == 1.0


def test_miss_scores_zero_hit_rate():
    labeled = [{"query": "capital of France", "relevant": ["Paris"]}]
    rf = _fake_recall({"capital of France": ["unrelated fact", "another"]})
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert m.hit_rate == 0.0 and m.mrr == 0.0


def test_mrr_reflects_rank_of_first_relevant_hit():
    labeled = [{"query": "q", "relevant": ["GOLD"]}]
    rf = _fake_recall({"q": ["x", "y", "this has GOLD in it"]})  # first relevant at rank 3
    m = recall_eval.evaluate_recall(labeled, rf, k=5)
    assert abs(m.mrr - (1 / 3)) < 1e-3   # metrics are rounded to 4 places


def test_empty_labeled_set_is_all_zero():
    m = recall_eval.evaluate_recall([], _fake_recall({}), k=5)
    assert m.queries == 0 and m.precision_at_k == 0.0


def test_recall_failure_for_one_query_does_not_abort():
    def boom(q, k): raise RuntimeError("store down")
    m = recall_eval.evaluate_recall([{"query": "q", "relevant": ["x"]}], boom, k=5)
    assert m.queries == 1 and m.hit_rate == 0.0   # scored 0, never raised


# --- injectable NER upgrade path (entities) ---------------------------------

def test_entities_uses_injected_extractor_then_falls_back(monkeypatch):
    entities.set_extractor(lambda text: ["Acme Corp", "x"])   # 'x' dropped (len<=1)
    try:
        assert entities.extract("anything") == ["Acme Corp"]
        # a throwing NER falls back to the regex pass (graph never breaks)
        entities.set_extractor(lambda text: (_ for _ in ()).throw(RuntimeError("ner down")))
        assert "Michael" in entities.extract("Remember Michael lives in Utah")
    finally:
        entities.set_extractor(None)   # restore the regex default
    assert "Michael" in entities.extract("Michael")
