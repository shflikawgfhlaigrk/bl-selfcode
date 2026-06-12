"""The memory error taxonomy IS the contract: callers branch on the type
(policy rejection vs store outage), so the hierarchy must never silently change."""
from __future__ import annotations

import pytest

from utah import UtahError
from utah.memory.exceptions import AdmissionDenied, MemoryUnavailable


def test_both_are_utah_errors():
    """One daemon-boundary clause (`except UtahError`) catches the whole family."""
    assert issubclass(AdmissionDenied, UtahError)
    assert issubclass(MemoryUnavailable, UtahError)
    for exc_type in (AdmissionDenied, MemoryUnavailable):
        with pytest.raises(UtahError):
            raise exc_type("boom")


def test_policy_and_infrastructure_are_disjoint():
    """An admission rejection must never be retried as an outage (and vice versa):
    neither type may be a subclass of the other."""
    assert not issubclass(AdmissionDenied, MemoryUnavailable)
    assert not issubclass(MemoryUnavailable, AdmissionDenied)


def test_catching_one_does_not_swallow_the_other():
    with pytest.raises(MemoryUnavailable):
        try:
            raise MemoryUnavailable("store down")
        except AdmissionDenied:  # pragma: no cover — must NOT match
            pytest.fail("MemoryUnavailable was caught as AdmissionDenied")


def test_message_round_trips():
    err = AdmissionDenied("admission denied: empty content")
    assert str(err) == "admission denied: empty content"
    err = MemoryUnavailable("connection refused")
    assert "connection refused" in str(err)
