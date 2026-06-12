"""Daemon boot seams: a typo'd env override must NEVER make the daemon
unbootable (the supervisor would crash-loop it forever — worse than a wrong
limit), floors keep the seams in their sane ranges (pool 0 = a daemon that
accepts work and never runs it), and re-entering logging setup must not
duplicate every line in the rotated log."""
from __future__ import annotations

import logging
import logging.handlers

import pytest

from utah.daemon import daemon


# -- env seam parsing -----------------------------------------------------------

def test_env_int_reads_a_valid_override(monkeypatch):
    monkeypatch.setenv("UTAH_TEST_SEAM", "32")
    assert daemon._env_int("UTAH_TEST_SEAM", 16, floor=1) == 32


def test_env_int_unset_uses_default(monkeypatch):
    monkeypatch.delenv("UTAH_TEST_SEAM", raising=False)
    assert daemon._env_int("UTAH_TEST_SEAM", 16, floor=1) == 16


def test_env_int_garbage_falls_back_to_default_not_a_boot_crash(monkeypatch):
    """`UTAH_POOL_LIMIT=sixteen` used to raise ValueError AT IMPORT — the
    daemon could not even boot, and the supervisor crash-looped it."""
    monkeypatch.setenv("UTAH_TEST_SEAM", "sixteen")
    assert daemon._env_int("UTAH_TEST_SEAM", 16, floor=1) == 16


def test_env_int_clamps_to_floor(monkeypatch):
    monkeypatch.setenv("UTAH_TEST_SEAM", "0")
    # pool=0 would mean a daemon that accepts work and never runs any of it
    assert daemon._env_int("UTAH_TEST_SEAM", 16, floor=1) == 1
    monkeypatch.setenv("UTAH_TEST_SEAM", "-5")
    assert daemon._env_int("UTAH_TEST_SEAM", 16, floor=1) == 1


def test_env_float_valid_garbage_and_floor(monkeypatch):
    monkeypatch.setenv("UTAH_TEST_SEAM", "2.5")
    assert daemon._env_float("UTAH_TEST_SEAM", 1.5, floor=0.1) == 2.5
    monkeypatch.setenv("UTAH_TEST_SEAM", "lots")
    assert daemon._env_float("UTAH_TEST_SEAM", 1.5, floor=0.1) == 1.5
    monkeypatch.setenv("UTAH_TEST_SEAM", "-1.0")
    # load/core <= 0 would shed EVERY call — the governor as a deadbolt
    assert daemon._env_float("UTAH_TEST_SEAM", 1.5, floor=0.1) == pytest.approx(0.1)


def test_env_float_rejects_non_finite(monkeypatch):
    """float('nan')/'inf' parse fine but poison every load comparison — the
    governor would never (nan) or always (inf…) shed. Fall back instead."""
    for bad in ("nan", "inf", "-inf"):
        monkeypatch.setenv("UTAH_TEST_SEAM", bad)
        assert daemon._env_float("UTAH_TEST_SEAM", 1.5, floor=0.1) == 1.5


def test_live_seam_constants_are_in_sane_ranges():
    """Whatever the environment said at import, the daemon's actual knobs must
    be runnable: a violated floor here means the boot DAG wires a broken pool
    or a governor that sheds everything."""
    assert daemon.POOL_LIMIT >= 1
    assert daemon.GOV_MAX_INFLIGHT >= 1
    assert daemon.GOV_MAX_LOAD_PER_CORE > 0
    assert daemon.DRAIN_TIMEOUT_S >= 0
    assert daemon.SHUTDOWN_GRACE_S >= 0


# -- logging idempotency -----------------------------------------------------------

def test_setup_logging_is_idempotent(monkeypatch, tmp_path):
    """A re-entry (restart-in-process, a second import path) must not add a
    second handler pair — duplicated handlers double every log line and the
    rotated file fills at twice the rate."""
    monkeypatch.setattr(daemon.runtime, "ensure_runtime", lambda: None)
    monkeypatch.setattr(daemon.runtime, "LOG_PATH", tmp_path / "utahd.log")
    root = logging.getLogger("utah")
    before = list(root.handlers)
    try:
        daemon._setup_logging()
        after_first = len(root.handlers)
        daemon._setup_logging()
        assert len(root.handlers) == after_first  # second call added nothing
    finally:
        for h in root.handlers[len(before):]:
            root.removeHandler(h)
            h.close()
        assert root.handlers == before
