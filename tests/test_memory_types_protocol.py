"""StoreBackend as a runtime-checkable protocol + the remaining row-shape contracts.

test_memory_types.py pins signature parity by introspection; THIS file pins that the
protocol is usable for ``isinstance`` gating (so a wiring bug — handing the pipeline a
non-store — fails at the boundary, not three calls deep) and that every recall row type
is an immutable tuple whose positional order survives a round-trip.
"""
from __future__ import annotations

import pytest

from tests.fakes import FakeStore
from utah.memory import types
from utah.memory.store import PostgresStore


def test_protocol_is_runtime_checkable_against_both_backends():
    assert isinstance(PostgresStore("dbname=hermetic"), types.StoreBackend)
    assert isinstance(FakeStore(), types.StoreBackend)


def test_protocol_rejects_a_non_store():
    class NotAStore:
        def init_schema(self) -> None: ...
        # missing the rest of the surface

    assert not isinstance(NotAStore(), types.StoreBackend)
    assert not isinstance(object(), types.StoreBackend)


def test_dense_and_sparse_rows_are_immutable():
    d = types.DenseRow(1, "c", "fact", 0.5)
    s = types.SparseRow(2, "c", "turn")
    with pytest.raises(AttributeError):
        d.sim = 0.9
    with pytest.raises(AttributeError):
        s.content = "mutated"


def test_rows_round_trip_positionally():
    # Positional construction (SQL) -> tuple unpack (pipeline) must be lossless.
    n = types.Neighbor(*tuple(types.Neighbor(3, "x", 0.7, "core")))
    assert n == types.Neighbor(3, "x", 0.7, "core")
