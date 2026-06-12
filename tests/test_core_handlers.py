"""Core control-plane handlers driven directly with a REAL Context.

Real WorkerPool (off-loop offload), real Governor (huge thresholds so nothing
sheds), real Bus. The live collaborators behind the lazy imports (memory
backend, outreach, researcher) are injected at their own seams — never the
handlers under test.

The headline contracts pinned here:
- every numeric param is validated: garbage → typed INVALID_PARAMS, never a
  raw ValueError that surfaces as INTERNAL_ERROR + a phantom audit row;
- every numeric param is clamped to its documented bounds;
- shutdown/status/publish/panel_detail behave per the deck's wire contract.
"""
from __future__ import annotations

import time

import anyio
import pytest

from utah import memory as memory_mod
from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context
from utah.daemon.governor import Governor
from utah.daemon.handlers import core_handlers as h
from utah.daemon.pool import WorkerPool
from utah.daemon.rpc import INVALID_PARAMS, RpcError


@pytest.fixture
def ctx() -> Context:
    return Context(
        pool=WorkerPool(limit=2),
        governor=Governor(max_load_per_core=1e9, max_inflight=1000),
        bus=Bus(),
        shutdown=anyio.Event(),
        started_monotonic=time.monotonic(),
        version="test",
    )


def run(coro_fn, ctx, params=None):
    return anyio.run(lambda: coro_fn(ctx, params))


# -- ping / status / shutdown -------------------------------------------------

def test_ping_answers_with_live_uptime(ctx):
    out = run(h.ping, ctx)
    assert out["pong"] is True and out["uptime_s"] >= 0.0


def test_status_reports_real_pool_governor_and_bus(ctx):
    out = run(h.status, ctx)
    assert out["version"] == "test"
    assert out["pool"] == {"limit": 2, "borrowed": 0, "available": 2}
    assert out["governor"]["max_inflight"] == 1000
    assert out["bus"] == {"subscribers": 0, "published": 0, "dropped": 0}
    assert out["draining"] is False


def test_shutdown_sets_the_event_and_status_reports_draining(ctx):
    out = run(h.shutdown, ctx)
    assert out == {"stopping": True}
    assert ctx.shutdown.is_set()
    assert run(h.status, ctx)["draining"] is True


# -- param validation: typed, never a raw ValueError --------------------------

def test_non_object_params_are_invalid_params(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.tell, ctx, [1, 2, 3])
    assert ei.value.code == INVALID_PARAMS


def test_tell_requires_nonempty_text(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.tell, ctx, {"text": "   "})
    assert ei.value.code == INVALID_PARAMS


@pytest.mark.parametrize("params", [
    {"limit": "lots"}, {"limit": None}, {"offset": "x"}, {"limit": [5]},
])
def test_memory_list_garbage_numbers_are_invalid_params(ctx, params):
    with pytest.raises(RpcError) as ei:
        run(h.memory_list, ctx, params)
    assert ei.value.code == INVALID_PARAMS


def test_memory_entities_garbage_limit_is_invalid_params(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.memory_entities, ctx, {"limit": "many"})
    assert ei.value.code == INVALID_PARAMS


def test_ledger_snapshot_garbage_limit_is_invalid_params(ctx):
    # Raised BEFORE any pool/ledger work — no Postgres is touched.
    with pytest.raises(RpcError) as ei:
        run(h.ledger_snapshot, ctx, {"limit": "big"})
    assert ei.value.code == INVALID_PARAMS


def test_agent_garbage_seconds_is_invalid_params(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.agent, ctx, {"seconds": "soon"})
    assert ei.value.code == INVALID_PARAMS


def test_work_leads_garbage_limit_is_invalid_params(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.work_leads, ctx, {"limit": "all of them"})
    assert ei.value.code == INVALID_PARAMS


def test_research_requires_query(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.research, ctx, {"k": 3})
    assert ei.value.code == INVALID_PARAMS


# -- clamping to documented bounds ---------------------------------------------

class _CapturingBackend:
    """A memory backend that records the args the handler actually used."""

    def __init__(self) -> None:
        self.calls: list = []

    def list_memories(self, limit: int, offset: int) -> list:
        self.calls.append(("list", limit, offset))
        return [{"id": 1}]

    def list_entities(self, limit: int) -> list:
        self.calls.append(("entities", limit))
        return []

    def live_counts(self) -> dict:
        return {"memories": 1}


def test_memory_list_clamps_limit_and_offset(ctx):
    backend = _CapturingBackend()
    memory_mod.set_backend(backend)
    out = run(h.memory_list, ctx, {"limit": 99999, "offset": -7})
    assert backend.calls == [("list", 200, 0)]  # hi-clamp 200, lo-clamp 0
    assert out == {"rows": [{"id": 1}]}


def test_memory_entities_clamps_limit(ctx):
    backend = _CapturingBackend()
    memory_mod.set_backend(backend)
    run(h.memory_entities, ctx, {"limit": 0})
    assert backend.calls == [("entities", 1)]  # lo-clamp to 1


def test_memory_stats_returns_live_counts_off_loop(ctx):
    memory_mod.set_backend(_CapturingBackend())
    assert run(h.memory_stats, ctx) == {"memories": 1}


def test_research_clamps_k_to_eight(ctx, monkeypatch):
    seen = {}

    def fake_research(query, k):
        seen["query"], seen["k"] = query, k
        return {"stored": 0}

    monkeypatch.setattr("utah.product.researcher.research", fake_research)
    run(h.research, ctx, {"query": "utah", "k": 999})
    assert seen == {"query": "utah", "k": 8}


def test_work_leads_clamps_limit_and_normalizes_channel(ctx, monkeypatch):
    seen = {}

    def fake_run_scheduled(campaign, *, limit, channel):
        seen.update(campaign=campaign, limit=limit, channel=channel)
        return {"sent": 0}

    monkeypatch.setattr("utah.product.outreach.run_scheduled", fake_run_scheduled)
    run(h.work_leads, ctx, {"limit": 9999, "channel": "  EMAIL "})
    assert seen["limit"] == 200          # hi-clamp
    assert seen["channel"] == "email"    # stripped + lowered


def test_agent_clamps_negative_seconds_to_zero_and_runs_off_loop(ctx):
    t0 = time.perf_counter()
    out = run(h.agent, ctx, {"seconds": -5})
    assert out["ran_s"] == 0.0
    assert time.perf_counter() - t0 < 1.0  # never slept a negative→huge value


def test_agent_really_runs_the_work(ctx):
    out = run(h.agent, ctx, {"seconds": 0.05})
    assert 0.04 <= out["ran_s"] < 1.0


# -- publish / panel_detail wire contracts -------------------------------------

def test_publish_requires_channel(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.publish, ctx, {"event": {"x": 1}})
    assert ei.value.code == INVALID_PARAMS


def test_publish_event_must_be_an_object(ctx):
    with pytest.raises(RpcError) as ei:
        run(h.publish, ctx, {"channel": "c", "event": [1]})
    assert ei.value.code == INVALID_PARAMS


def test_publish_delivers_to_a_live_subscriber(ctx):
    sub = ctx.bus.subscribe(["deck"])
    try:
        out = run(h.publish, ctx, {"channel": "deck", "event": {"hello": 1}})
        assert out == {"delivered": 1}
    finally:
        ctx.bus._remove(sub.id)


def test_panel_detail_unknown_panel_returns_empty_rows(ctx):
    out = run(h.panel_detail, ctx, {"panel": "no_such_panel"})
    assert out == {"panel": "no_such_panel", "rows": []}
