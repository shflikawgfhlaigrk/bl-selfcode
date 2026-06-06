"""The rerank boundary: injectable; degrades to neutral scores, never raises."""
from __future__ import annotations

import os

from utah import config, rerank
from tests.fakes import ScriptedReranker, ZeroReranker


def test_empty_docs_short_circuit():
    rerank.set_reranker(ScriptedReranker(error=AssertionError("must not be called")))
    assert rerank.rerank("q", []) == []


def test_scores_pass_through():
    rerank.set_reranker(ScriptedReranker(score_fn=lambda q, d: len(d)))
    assert rerank.rerank("q", ["ab", "abcd"]) == [2.0, 4.0]


def test_model_failure_degrades_to_zeros_not_an_exception():
    rerank.set_reranker(ScriptedReranker(error=RuntimeError("model exploded")))
    assert rerank.rerank("q", ["a", "b", "c"]) == [0.0, 0.0, 0.0]


def test_malformed_score_count_degrades_to_zeros():
    class ShortReranker:
        def rerank(self, query, docs):
            return [1.0]  # wrong length

    rerank.set_reranker(ShortReranker())
    assert rerank.rerank("q", ["a", "b"]) == [0.0, 0.0]


def test_set_reranker_none_restores_default():
    fake = ZeroReranker()
    rerank.set_reranker(fake)
    assert rerank.get_reranker() is fake
    rerank.set_reranker(None)
    assert isinstance(rerank.get_reranker(), rerank.FastEmbedReranker)


def test_rerank_cache_dir_is_durable_under_utah_not_temp():
    """The reranker model must persist under ~/.utah (every other Utah model
    lives there), NOT macOS temp (/var/folders/.../T) which gets purged →
    rerank would silently revert to degraded zeros."""
    cache = str(config.RERANK_CACHE_DIR)
    assert cache.startswith(os.path.expanduser("~/.utah"))
    assert "/var/folders/" not in cache and "/T/" not in cache


def test_fastembed_reranker_passes_durable_cache_dir_to_model(monkeypatch):
    import fastembed.rerank.cross_encoder as cc

    captured = {}

    class _FakeTCE:
        def __init__(self, model_name, cache_dir=None, **kw):
            captured["model_name"] = model_name
            captured["cache_dir"] = cache_dir

        def rerank(self, query, docs):
            return [0.0] * len(docs)

    monkeypatch.setattr(cc, "TextCrossEncoder", _FakeTCE)
    rerank.FastEmbedReranker().rerank("q", ["d"])
    assert captured["model_name"] == config.RERANK_MODEL
    assert captured["cache_dir"] == str(config.RERANK_CACHE_DIR)
