"""Pipeline boundary hardening on fakes: backend lifecycle (set/close/init),
the off-path touch write that must never break a read, reranker-down degradation,
core_recall's never-raises contract, and the reinforce-invariant guard that
replaced a strippable `assert` (python -O removes asserts; the guard must not go
with them)."""
from __future__ import annotations

import pytest

from utah import memory
from utah.memory import MemoryUnavailable
from utah.memory import pipeline
from utah.objects import WriteAction, WriteDecision
from tests.fakes import FakeStore, ScriptedReranker, basis, blend


# --- backend lifecycle ---------------------------------------------------------------


def test_set_backend_closes_the_previous_backend():
    old = FakeStore()
    memory.set_backend(old)
    memory.set_backend(FakeStore())
    assert old.closed is True


def test_set_backend_survives_a_close_that_raises():
    class BadClose(FakeStore):
        def close(self):
            raise RuntimeError("close exploded")

    memory.set_backend(BadClose())
    replacement = FakeStore()
    memory.set_backend(replacement)            # must not raise
    assert memory.get_backend() is replacement


def test_close_is_idempotent_and_releases_the_backend():
    store = FakeStore()
    memory.set_backend(store)
    memory.close()
    assert store.closed is True
    memory.close()                              # second close: no-op, no raise


def test_init_runs_schema_creation(fake_store):
    memory.init()
    assert fake_store.schema_inits == 1


# --- the off-read-path touch ----------------------------------------------------------


def test_touch_failure_never_breaks_recall(mem, monkeypatch):
    """The frequency/recency bump is best-effort: a store that dies on touch()
    must not surface into the read. Run the touch thread synchronously so the
    exception (if unhandled) would propagate into this test."""
    class SyncThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(pipeline.threading, "Thread", SyncThread)

    def explode(ids):
        raise MemoryUnavailable("store died on touch")

    monkeypatch.setattr(mem.store, "touch", explode)
    memory.store("Michael prefers tea", source="fact")
    hits = memory.recall("Michael prefers tea")
    assert [h.content for h in hits] == ["Michael prefers tea"]


# --- reranker degradation --------------------------------------------------------------


def test_recall_survives_a_crashing_reranker(mem):
    from utah import rerank as rerank_mod

    memory.store("the vault passphrase is kiwi-canyon", source="fact")
    rerank_mod.set_reranker(ScriptedReranker(error=RuntimeError("model OOM")))
    try:
        hits = memory.recall("vault passphrase")
        assert [h.content for h in hits] == ["the vault passphrase is kiwi-canyon"]
    finally:
        rerank_mod.set_reranker(mem.reranker)


# --- core_recall: never raises ----------------------------------------------------------


def test_core_recall_returns_core_rows_as_hits(mem):
    memory.store("Ace and Michael are one; us vs the world.", source="core")
    memory.store("an ordinary fact", source="fact")
    hits = memory.core_recall()
    assert [h.source for h in hits] == ["core"]
    assert hits[0].score == 1.0 and hits[0].sim == 1.0


def test_core_recall_degrades_to_empty_when_store_down(mem):
    mem.store.fail = True
    assert memory.core_recall() == []           # never raises — the brain loop survives


# --- the reinforce invariant guard -------------------------------------------------------


def test_reinforce_decision_without_target_raises_explicitly(mem, monkeypatch):
    """decide_write guarantees reinforce_id on REINFORCED; if that invariant ever
    breaks, store() must fail LOUDLY — even under python -O where a bare assert
    would have been stripped and backend.reinforce(None) would corrupt the row."""
    monkeypatch.setattr(
        pipeline, "decide_write",
        lambda *_a, **_k: WriteDecision(action=WriteAction.REINFORCED,
                                        reinforce_id=None),
    )
    with pytest.raises(RuntimeError, match="reinforce"):
        memory.store("any fact", source="fact")


def test_store_propagates_memory_unavailable_from_reinforce(mem):
    """An outage mid-reinforce is infrastructure: MemoryUnavailable propagates
    (the caller's retry contract), never a silent success."""
    memory.store("Michael prefers tea", source="fact")

    def explode(mem_id):
        raise MemoryUnavailable("store died")

    mem.store.reinforce = explode
    with pytest.raises(MemoryUnavailable):
        memory.store("Michael prefers tea", source="fact")   # dup -> reinforce path
