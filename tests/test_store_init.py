"""utah.store package surface — lazy tier access without paying import cost.

The package init exposes its tiers (currently the DuckDB OLAP module) via
PEP 562 lazy attributes, so ``import utah.store`` stays free for the many
callers that never touch analytics, while ``utah.store.olap`` still resolves
to the real module.
"""
from __future__ import annotations

import importlib

import pytest


def test_lazy_olap_attribute_resolves_to_the_real_module():
    import utah.store as store
    olap = store.olap
    assert olap is importlib.import_module("utah.store.olap")
    assert hasattr(olap, "query")        # the real tier, not a stub


def test_classic_from_import_still_works():
    from utah.store import olap
    assert olap.__name__ == "utah.store.olap"


def test_unknown_attribute_raises_attribute_error():
    import utah.store as store
    with pytest.raises(AttributeError, match="utah.store"):
        store.no_such_tier


def test_dir_advertises_the_tiers():
    import utah.store as store
    assert "olap" in dir(store)
    assert "olap" in store.__all__
