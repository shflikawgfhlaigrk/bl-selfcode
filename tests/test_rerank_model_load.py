"""Rerank PRODUCTION-default boundary: the lazy fastembed load itself failing (missing
dep, unreachable cache dir) must degrade through rerank() to neutral zeros — recall must
survive a machine where the model can never come up. Plus the count-mismatch direction
the basic suite doesn't cover (too MANY scores) and single-load under concurrency."""
from __future__ import annotations

import threading

from utah import config, rerank


def test_too_many_scores_degrade_to_zeros():
    class LongReranker:
        def rerank(self, query, docs):
            return [1.0] * (len(docs) + 3)              # wrong length, surplus direction

    rerank.set_reranker(LongReranker())
    try:
        assert rerank.rerank("q", ["a", "b"]) == [0.0, 0.0]
    finally:
        rerank.set_reranker(None)


def test_default_reranker_load_failure_degrades_to_zeros(monkeypatch):
    """The PRODUCTION path: FastEmbedReranker._load blowing up (model/dep missing) must
    be caught by the rerank() boundary — zeros out, recall alive, warning logged."""
    def boom(self):
        raise ImportError("fastembed is not installed on this box")

    monkeypatch.setattr(rerank.FastEmbedReranker, "_load", boom)
    rerank.set_reranker(None)                           # force the real default
    try:
        assert rerank.rerank("q", ["a", "b", "c"]) == [0.0, 0.0, 0.0]
    finally:
        rerank.set_reranker(None)


def test_unwritable_cache_dir_degrades_not_raises(tmp_path, monkeypatch):
    """A cache dir that cannot be created (here: a path UNDER a regular file) makes
    _load raise OSError — the boundary must still hand recall neutral zeros."""
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    rr = rerank.FastEmbedReranker(cache_dir=str(blocker / "sub"))
    rerank.set_reranker(rr)
    try:
        assert rerank.rerank("q", ["doc"]) == [0.0]
    finally:
        rerank.set_reranker(None)


def test_fastembed_model_is_loaded_exactly_once_across_threads(monkeypatch):
    """The instance lock must serialize the lazy load: two concurrent first-touches
    constructing the cross-encoder twice would double model RAM."""
    import fastembed.rerank.cross_encoder as cc

    made = []

    class _FakeTCE:
        def __init__(self, model_name, cache_dir=None, **kw):
            made.append(model_name)

        def rerank(self, query, docs):
            return [0.0] * len(docs)

    monkeypatch.setattr(cc, "TextCrossEncoder", _FakeTCE)
    rr = rerank.FastEmbedReranker(model_name=config.RERANK_MODEL)
    threads = [threading.Thread(target=lambda: rr.rerank("q", ["d"])) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert made == [config.RERANK_MODEL]                # exactly one construction
