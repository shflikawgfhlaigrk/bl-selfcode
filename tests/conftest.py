"""Shared fixtures: every injectable boundary gets a deterministic fake,
and every boundary is restored after each test (no cross-test bleed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from utah import brain as brain_mod
from utah import embed as embed_mod
from utah import failures as failures_mod
from utah import memory as memory_mod
from utah import rerank as rerank_mod
from tests.fakes import FakeEmbedder, FakeFailureStore, FakeStore, ScriptedRunner, ZeroReranker


@pytest.fixture(autouse=True)
def _restore_boundaries():
    """Restore all injectable boundaries after every test. Also pin a fake failure
    store for EVERY test so code paths that record failures (core.tell_stream etc.)
    never write to the real Postgres failures table (no test pollution)."""
    failures_mod.set_store(FakeFailureStore())
    yield
    embed_mod.set_embedder(None)
    rerank_mod.set_reranker(None)
    brain_mod.set_runner(None)
    brain_mod.set_stream_runner(None)
    memory_mod.set_backend(None)
    failures_mod.set_store(None)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    embedder = FakeEmbedder()
    embed_mod.set_embedder(embedder)
    return embedder


@pytest.fixture
def fake_store() -> FakeStore:
    store = FakeStore()
    memory_mod.set_backend(store)
    return store


@pytest.fixture
def zero_reranker() -> ZeroReranker:
    reranker = ZeroReranker()
    rerank_mod.set_reranker(reranker)
    return reranker


@pytest.fixture
def mem(fake_embedder, fake_store, zero_reranker) -> SimpleNamespace:
    """The full pipeline on fakes: embedder + store + neutral reranker."""
    return SimpleNamespace(
        embedder=fake_embedder, store=fake_store, reranker=zero_reranker
    )


@pytest.fixture
def fake_brain():
    """Inject a scripted brain runner; tests set ``runner.respond``."""
    runner = ScriptedRunner(respond="")
    brain_mod.set_runner(runner)
    return runner
