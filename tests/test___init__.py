"""The utah package root: version + the UtahError failure taxonomy.

The contract is small but load-bearing: every structured boundary failure
(brain subprocess, memory store, embedding) subclasses :class:`UtahError`, so a
caller can catch ONE base class and degrade gracefully instead of guessing
which module blew up.
"""
from __future__ import annotations

import re

import pytest

import utah


def test_version_is_semver_shaped():
    assert isinstance(utah.__version__, str)
    assert re.fullmatch(r"\d+\.\d+\.\d+", utah.__version__)


def test_public_surface_is_declared():
    assert set(utah.__all__) == {"UtahError", "__version__"}


def test_utah_error_is_an_exception_with_a_message():
    err = utah.UtahError("embedding lane down")
    assert isinstance(err, Exception)
    assert "embedding lane down" in str(err)


def test_brain_unavailable_is_catchable_as_utah_error():
    """The documented degrade path: catch the base, not N module-specific types."""
    from utah.brain import BrainUnavailable

    assert issubclass(BrainUnavailable, utah.UtahError)
    with pytest.raises(utah.UtahError):
        raise BrainUnavailable("cli gone")


def test_memory_unavailable_is_catchable_as_utah_error():
    from utah.memory import MemoryUnavailable

    assert issubclass(MemoryUnavailable, utah.UtahError)


def test_utah_error_does_not_catch_foreign_exceptions():
    """The taxonomy must stay PRECISE: a ValueError is not a Utah boundary failure,
    so `except UtahError` must never swallow it."""
    with pytest.raises(ValueError):
        try:
            raise ValueError("not a boundary failure")
        except utah.UtahError:  # pragma: no cover — must not happen
            pytest.fail("UtahError caught a foreign exception")
