"""Shared fixtures: every injectable boundary gets a deterministic fake,
and every boundary is restored after each test (no cross-test bleed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from utah import alerts as alerts_mod
from utah import brain as brain_mod
from utah import core as core_mod
from utah import embed as embed_mod
from utah import failures as failures_mod
from utah import local as local_mod
from utah import memory as memory_mod
from utah import rerank as rerank_mod
from utah import sica as sica_mod
from utah.product import signals as signals_mod
from tests.fakes import FakeEmbedder, FakeFailureStore, FakeStore, ScriptedRunner, ZeroReranker


def _alerts_off(*_a, **_k):
    """Default test push sender: never touches the network or pages a real phone.
    Tests that exercise alerts inject their own capturing sender over this."""
    return {"sent": False, "gated": True, "fake": True}


def _mail_off(*_a, **_k):
    """Default test mail transport for signals delivery: gated, never SMTP."""
    return {"sent": False, "gated": True, "fake": True}


def _discord_off(*_a, **_k):
    """Default test Discord post for signals delivery: gated, never a webhook."""
    return {"posted": False, "gated": True, "fake": True}


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
    # Pin the push sender OFF for EVERY test so no code path (failures.record's critical
    # hook, trade fires, brief/leads/probate crons) ever hits the network or pages a real
    # phone — even though tests run against the real ~/.utah/secrets. Also clear the
    # in-memory dedup so a prior test's alert can't suppress a later test's.
    alerts_mod.set_sender(_alerts_off)
    # Isolate the FILE-BACKED dedup to a per-test tmp file so tests never read or
    # write the live ~/.utah/run/alerts_seen.json (and never see each other's state).
    import tempfile, os as _os
    _seen_tmp = tempfile.mkdtemp(prefix="utah-alerts-seen-")
    alerts_mod.set_seen_path(__import__("pathlib").Path(_seen_tmp) / "alerts_seen.json")
    alerts_mod._reset_seen_for_tests()
    # SITE-12: pin the signals delivery lane OFF for EVERY test — record_fire spawns a
    # background delivery thread, and without this a ledger test would read the real
    # signals_subscribers table (and could email real subscribers). The empty-subscriber
    # pin is the lane's own honest gate: zero network, zero DB, zero sends. Tests that
    # exercise delivery inject their own fakes per call (they override these defaults).
    signals_mod.set_transports(mail_send=_mail_off, discord_post=_discord_off,
                               subscribers_fn=lambda: [])
    signals_mod.set_seen_path(__import__("pathlib").Path(_seen_tmp) / "signals_seen.json")
    signals_mod._reset_seen_for_tests()
    # Pin a fake memory backend for EVERY test so no test ever reads, writes, or resets
    # the real Postgres memory (that pollution added rows to prod; a stray reset once
    # wiped it). Tests that need a configured store override via the `mem`/`fake_store`
    # fixtures, which run after this and replace it.
    memory_mod.set_backend(FakeStore())
    # Pin an in-memory SICA archive so no test reads/writes the real Postgres
    # selfcode_archive (Archive() with no path now defaults to PG in production).
    sica_mod.set_archive_backend(sica_mod._MemArchive())
    # Clear the trading edge-gate cache so a verdict from one test (or a real-DB backtest)
    # never leaks into another's fire decisions (the gate is module-level + TTL-cached).
    try:
        from utah.product import trading as _trading_mod
        _trading_mod._EDGE_CACHE.clear()
    except Exception:  # noqa: BLE001 — trading import optional in minimal test subsets
        pass
    # Clear the kill-switch smoke cache so a cached verdict from one test never serves
    # another (the smoke is TTL-cached to keep the deck panel off the full propose() path).
    try:
        from utah import selfcode as _selfcode_mod
        _selfcode_mod._SMOKE_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass
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
    alerts_mod.set_sender(None)
    alerts_mod.set_seen_path(None)
    alerts_mod._reset_seen_for_tests()
    signals_mod.set_transports()
    signals_mod.set_seen_path(None)
    signals_mod._reset_seen_for_tests()


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
