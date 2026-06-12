"""Memory type contracts — row shapes + StoreBackend protocol drift detection.

The row NamedTuples are built POSITIONALLY from SQL SELECTs (store.py) and consumed
by name (pipeline.py), so their field ORDER is load-bearing: reordering a field would
silently swap content/source in every recall. The protocol test pins both backends
(PostgresStore prod, FakeStore tests) to the same method surface so a signature change
in one can't drift past the other.
"""
from __future__ import annotations

import inspect

from tests.fakes import FakeStore
from utah.memory import types
from utah.memory.store import PostgresStore

#: Every public method the StoreBackend protocol declares.
_PROTO_METHODS = sorted(
    name for name, member in vars(types.StoreBackend).items()
    if not name.startswith("_") and callable(member)
)


def test_protocol_declares_the_full_storage_surface():
    # The protocol is the contract: if a method is dropped, both backends go unchecked.
    assert {"init_schema", "reset_schema", "nearest", "insert", "reinforce",
            "dense_search", "curated_search", "sparse_search", "entity_names",
            "touch", "apply_decay", "unpromoted_turns", "mark_promoted",
            "core_rows", "close"} <= set(_PROTO_METHODS)


def test_postgres_store_implements_every_protocol_method():
    missing = [m for m in _PROTO_METHODS if not callable(getattr(PostgresStore, m, None))]
    assert missing == [], f"PostgresStore drifted from StoreBackend: {missing}"


def test_fake_store_implements_every_protocol_method():
    missing = [m for m in _PROTO_METHODS if not callable(getattr(FakeStore, m, None))]
    assert missing == [], f"FakeStore drifted from StoreBackend: {missing}"


def test_backend_signatures_match_the_protocol_parameter_names():
    # Same parameter names in the same order (ignoring self / keyword-only extras the
    # protocol doesn't declare) — so a renamed/reordered arg fails HERE, not in prod.
    for name in _PROTO_METHODS:
        proto_params = [p for p in inspect.signature(
            getattr(types.StoreBackend, name)).parameters if p != "self"]
        for impl in (PostgresStore, FakeStore):
            impl_params = [p for p in inspect.signature(
                getattr(impl, name)).parameters if p != "self"]
            assert impl_params[:len(proto_params)] == proto_params, (
                f"{impl.__name__}.{name} signature drifted: "
                f"{impl_params} != protocol {proto_params}")


# --- row shapes: field order is load-bearing (positional construction from SQL) ---

def test_neighbor_field_order_and_default_source():
    n = types.Neighbor(1, "content", 0.9)
    assert (n.id, n.content, n.sim, n.source) == (1, "content", 0.9, "")
    assert tuple(n) == (1, "content", 0.9, "")


def test_dense_row_field_order_matches_select_list():
    # store.dense_search SELECTs id, content, source, sim — in that order.
    r = types.DenseRow(7, "text", "fact", 0.5)
    assert (r.id, r.content, r.source, r.sim) == (7, "text", "fact", 0.5)


def test_sparse_row_field_order_matches_select_list():
    r = types.SparseRow(3, "text", "core")
    assert (r.id, r.content, r.source) == (3, "text", "core")


def test_rows_are_immutable_tuples():
    n = types.Neighbor(1, "c", 0.5, "fact")
    try:
        n.sim = 0.9
        raise AssertionError("Neighbor must be immutable")
    except AttributeError:
        pass
