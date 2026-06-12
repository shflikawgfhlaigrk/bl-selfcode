"""Dispatcher mechanics — typed unknown-method, boot-time handler validation,
registry isolation, and the live Context gauge.

The dispatcher is the single seam every RPC rides through; a sync function
accidentally registered as a handler used to surface only at first call (an
``await`` on a dict → INTERNAL_ERROR at 3am). These pin the fail-at-boot
contract instead.
"""
from __future__ import annotations

import time

import anyio
import pytest

from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.pool import WorkerPool
from utah.daemon.rpc import METHOD_NOT_FOUND, Request, RpcError


def _ctx() -> Context:
    return Context(
        pool=WorkerPool(limit=2),
        governor=Governor(max_load_per_core=1e9, max_inflight=1000),
        bus=Bus(),
        shutdown=anyio.Event(),
        started_monotonic=time.monotonic(),
        version="test",
    )


async def _echo(ctx: Context, params: object) -> object:
    return {"ctx_version": ctx.version, "params": params}


def test_unknown_method_is_typed_method_not_found():
    d = Dispatcher(_ctx(), {"echo": _echo})

    async def go():
        with pytest.raises(RpcError) as ei:
            await d.dispatch(Request(method="nope", id=1))
        assert ei.value.code == METHOD_NOT_FOUND
        assert "nope" in str(ei.value)

    anyio.run(go)


def test_dispatch_passes_context_and_params_through():
    d = Dispatcher(_ctx(), {"echo": _echo})

    async def go():
        out = await d.dispatch(Request(method="echo", params={"a": 1}, id=2))
        assert out == {"ctx_version": "test", "params": {"a": 1}}

    anyio.run(go)


def test_methods_lists_sorted_names():
    d = Dispatcher(_ctx(), {"zeta": _echo, "alpha": _echo, "mid": _echo})
    assert d.methods == ["alpha", "mid", "zeta"]


def test_registry_snapshot_is_isolated_from_caller_mutation():
    reg = {"echo": _echo}
    d = Dispatcher(_ctx(), reg)
    reg["sneaky"] = _echo  # mutating the source dict after construction
    assert d.methods == ["echo"]


def test_constructor_rejects_sync_handler_at_boot():
    # Fail loud when the daemon WIRES a bad handler, not at the first 3am call.
    def sync_handler(ctx, params):  # not async — awaiting its result would crash
        return {}

    with pytest.raises(TypeError) as ei:
        Dispatcher(_ctx(), {"ok": _echo, "broken": sync_handler})
    assert "broken" in str(ei.value)


def test_constructor_rejects_non_callable_handler():
    with pytest.raises(TypeError):
        Dispatcher(_ctx(), {"bad": "not a function"})


def test_context_uptime_is_live_and_monotonic():
    ctx = _ctx()
    first = ctx.uptime_s
    assert first >= 0.0
    time.sleep(0.01)
    assert ctx.uptime_s >= first
