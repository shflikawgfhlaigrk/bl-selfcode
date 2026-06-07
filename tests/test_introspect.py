"""Introspection — Utah's self-model from live state (injectable)."""
from __future__ import annotations

from utah import introspect


def test_self_model_from_injected_state():
    m = introspect.self_model(
        status={"version": "1.0.0"},
        memory_counts={"total": 9000, "live": 1900, "entities": 9500},
    )
    assert m["daemon_up"] is True
    assert m["memory"]["live"] == 1900
    assert m["capability_count"] == len(introspect.CAPABILITIES)
    assert "leads" in m["capabilities"] and "selfcode" in m["capabilities"]


def test_self_model_daemon_down():
    m = introspect.self_model(status=None, memory_counts={})
    assert m["daemon_up"] is False
