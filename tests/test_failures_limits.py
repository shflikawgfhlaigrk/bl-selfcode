"""failures.recent limit coercion: a non-numeric limit (an RPC handing a dict
through) degrades to the deck's default page size — never an exception, never
an unbounded scan."""
from __future__ import annotations

import pytest

from utah import failures
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _fresh_store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


def test_garbage_limit_degrades_to_the_default_page_size():
    captured: list[int] = []

    class Capturing(FakeFailureStore):
        def recent(self, limit):
            captured.append(limit)
            return []

    failures.set_store(Capturing())
    assert failures.recent("not-a-number") == []  # type: ignore[arg-type]
    assert failures.recent(None) == []            # type: ignore[arg-type]
    assert captured == [20, 20]


def test_float_limit_is_truncated_not_rejected():
    captured: list[int] = []

    class Capturing(FakeFailureStore):
        def recent(self, limit):
            captured.append(limit)
            return []

    failures.set_store(Capturing())
    failures.recent(7.9)  # type: ignore[arg-type]
    assert captured == [7]
