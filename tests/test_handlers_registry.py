"""Registry wiring — the control plane's method table is validated at import.

A handler that isn't an async function would only explode at its first call
(``await`` on a plain return value → INTERNAL_ERROR in production). The
registry module must therefore validate shape at import time, and the wired
table must contain the spine methods the deck/CLI/web depend on.
"""
from __future__ import annotations

import inspect

import pytest

from utah.daemon import handlers
from utah.daemon.handlers import REGISTRY, validated_registry

#: Methods the live surfaces (deck pills, CLI, web /api) are wired to call.
_SPINE_METHODS = {
    "ping", "status", "tell", "agent", "publish", "shutdown",
    "panel_detail", "memory_stats", "ledger_snapshot", "speak_stop",
}


def test_registry_exports_the_spine_methods():
    missing = _SPINE_METHODS - set(REGISTRY)
    assert not missing, f"spine methods missing from REGISTRY: {sorted(missing)}"


def test_every_registered_handler_is_an_async_function():
    bad = sorted(
        name for name, fn in REGISTRY.items()
        if not inspect.iscoroutinefunction(fn)
    )
    assert not bad, f"non-async handlers registered: {bad}"


def test_registry_names_are_nonempty_strings():
    assert all(isinstance(k, str) and k.strip() for k in REGISTRY)


def test_validated_registry_rejects_sync_handler():
    async def ok(ctx, params):
        return {}

    def broken(ctx, params):  # sync — awaiting its result would crash at runtime
        return {}

    with pytest.raises(TypeError) as ei:
        validated_registry({"ok": ok, "broken": broken})
    assert "broken" in str(ei.value)


def test_validated_registry_rejects_blank_method_name():
    async def ok(ctx, params):
        return {}

    with pytest.raises(TypeError):
        validated_registry({"  ": ok})


def test_validated_registry_passes_the_live_table():
    # The shipped REGISTRY itself must clear its own gate.
    assert validated_registry(REGISTRY) is REGISTRY


def test_module_all_exports_match():
    assert "REGISTRY" in handlers.__all__
