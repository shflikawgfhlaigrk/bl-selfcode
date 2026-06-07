"""Shared fixtures: every injectable boundary gets a deterministic fake,
and every boundary is restored after each test (no cross-test bleed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from utah import brain as brain_mod
from utah import core as core_mod
from utah import embed as embed_mod
from utah import failures as failures_mod
from utah import local as local_mod
from utah import memory as memory_mod
from utah import rerank as rerank_mod
from utah import sica as sica_mod
from tests.fakes import FakeEmbedder, FakeFailureStore, FakeStore, ScriptedRunner, ZeroReranker


def _local_down(*_a, **_k):
    """A local lane that is always unavailable — the test default so the router's
    L1 routing escalates to the (injected) brain instead of hitting real Ollama.
    Tests that exercise the local tier inject their own runner over this."""
    raise local_mod.LocalUnavailable("local lane disabled in tests")


@pytest.fixture(autouse=True)
def _restore_boundaries():
    """Restore all injectable boundaries after every test. Also pin a fake failure
    store for EVERY test so code paths that record failures (core.tell_stream etc.)
    never write to the real Postgres failures table (no test pollution), pin the
    local lane OFF (so router L1 escalates to the injected brain, never real Ollama),
    and clear the in-memory conversation thread so it never bleeds across tests."""
    failures_mod.set_store(FakeFailureStore())
    # Pin a fake memory backend for EVERY test so no test ever reads, writes, or resets
    # the real Postgres memory (that pollution added rows to prod; a stray reset once
    # wiped it). Tests that need a configured store override via the `mem`/`fake_store`
    # fixtures, which run after this and replace it.
    memory_mod.set_backend(FakeStore())
    # Pin an in-memory SICA archive so no test reads/writes the real Postgres
    # selfcode_archive (Archive() with no path now defaults to PG in production).
    sica_mod.set_archive_backend(sica_mod._MemArchive())
    local_mod.set_runner(_local_down)
    local_mod.set_stream_runner(_local_down)
    local_mod.set_load_probe(lambda: 0.0)  # never read the host's real load in tests
    core_mod.reset_conversation()
    yield
    core_mod.reset_conversation()
    embed_mod.set_embedder(None)
    rerank_mod.set_reranker(None)
    brain_mod.set_runner(None)
    brain_mod.set_stream_runner(None)
    local_mod.set_runner(None)
    local_mod.set_stream_runner(None)
    local_mod.set_load_probe(None)
    memory_mod.set_backend(None)
    sica_mod.set_archive_backend(None)
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
