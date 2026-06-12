"""Brain rate-limit signal — a Claude subscription session-limit hit is a TRANSIENT,
expected condition (the brain shells out to the `claude` CLI, which shares Michael's
subscription), not a system outage. It must be reported honestly: a distinct
``BrainRateLimited`` (still a ``BrainUnavailable`` so every caller keeps working) with
the reset window surfaced, recorded as a non-critical kind — never a generic outage."""
from __future__ import annotations

import sys

import pytest

from utah import brain


def test_session_limit_exit_raises_rate_limited_with_reset(monkeypatch):
    # The CLI's real exit-1 body when the subscription limit is hit.
    msg = "You've hit your session limit · resets 12pm (America/Chicago)"
    monkeypatch.setattr(sys, "argv", sys.argv)  # noop, keep import side-effect-free

    def limited_runner(argv, timeout):
        raise brain.BrainUnavailable(f"brain exited 1: {msg}")

    # The real signature classification is in _classify; assert it directly.
    err = brain.classify_brain_error(f"brain exited 1: {msg}")
    assert isinstance(err, brain.BrainRateLimited)
    assert "12pm" in str(err) and "America/Chicago" in str(err)
    # And it is STILL a BrainUnavailable — existing `except BrainUnavailable` keeps catching it.
    assert isinstance(err, brain.BrainUnavailable)


def test_ordinary_failure_is_plain_unavailable_not_rate_limited():
    err = brain.classify_brain_error("brain exited 7: some real crash traceback")
    assert isinstance(err, brain.BrainUnavailable)
    assert not isinstance(err, brain.BrainRateLimited)


def test_real_runner_classifies_session_limit(monkeypatch):
    # A real subprocess that prints the limit body to stderr and exits 1 must surface
    # as BrainRateLimited from the default runner.
    brain.set_runner(None)
    code = (
        "import sys; "
        "sys.stderr.write(\"You've hit your session limit · resets 12pm (America/Chicago)\"); "
        "sys.exit(1)"
    )
    with pytest.raises(brain.BrainRateLimited) as ei:
        brain._subprocess_runner([sys.executable, "-c", code], 10)
    assert "resets" in str(ei.value).lower()
