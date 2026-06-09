"""B2: the heavy-cron governor in foundation.gate_cron — load-defer + cross-process
mutex so the launchd cron fleet can't co-spike load (the AceOS-killer storm), while
light revenue senders are never skipped for moderate load."""
from __future__ import annotations

import pytest

from utah import config, failures, foundation
from tests.fakes import FakeFailureStore

_GREEN = {"state": "green", "ok": True}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(foundation, "_cron_slot_fh", None, raising=False)
    # tests are not the daemon process by pid, but make it explicit + deterministic
    monkeypatch.setattr(foundation, "_is_daemon_process", lambda: False)
    yield


def test_light_sender_is_never_load_gated(monkeypatch):
    """A revenue send (outreach/marketer/brief) runs on its hour even under high load."""
    monkeypatch.setattr("os.getloadavg", lambda: (999.0, 999.0, 999.0))
    assert foundation.gate_cron("outreach", status=_GREEN) is None
    assert foundation.gate_cron("marketer", status=_GREEN) is None


def test_heavy_cron_defers_under_high_load(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 4)
    monkeypatch.setattr("os.getloadavg", lambda: (100.0, 100.0, 100.0))  # 25/core >> 2.5
    skip = foundation.gate_cron("leads", status=_GREEN)
    assert skip and skip["status"] == "load_high" and skip["capability"] == "leads"


def test_heavy_cron_proceeds_under_low_load_and_takes_the_slot(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.setattr("os.getloadavg", lambda: (1.0, 1.0, 1.0))  # 0.06/core < 2.5
    acquired = {"n": 0}
    monkeypatch.setattr(foundation, "_acquire_cron_slot",
                        lambda: (acquired.__setitem__("n", acquired["n"] + 1) or True))
    assert foundation.gate_cron("probate", status=_GREEN) is None
    assert acquired["n"] == 1            # the slot was taken


def test_heavy_cron_defers_when_another_holds_the_slot(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.setattr("os.getloadavg", lambda: (1.0, 1.0, 1.0))
    monkeypatch.setattr(foundation, "_acquire_cron_slot", lambda: False)  # busy
    skip = foundation.gate_cron("consolidate", status=_GREEN)
    assert skip and skip["status"] == "cron_busy"


def test_in_daemon_heavy_call_is_not_mutexed(monkeypatch):
    """An in-daemon run_scheduled (worker pool) must NOT grab the cron mutex — that would
    starve the real crons. The daemon process is exempt from the heavy-cron governor."""
    monkeypatch.setattr(foundation, "_is_daemon_process", lambda: True)
    monkeypatch.setattr("os.getloadavg", lambda: (999.0, 999.0, 999.0))
    called = {"slot": False}
    monkeypatch.setattr(foundation, "_acquire_cron_slot",
                        lambda: called.__setitem__("slot", True) or True)
    assert foundation.gate_cron("leads", status=_GREEN) is None
    assert called["slot"] is False        # never touched the mutex in-daemon


def test_substrate_red_still_skips_first(monkeypatch):
    skip = foundation.gate_cron("leads", status={"state": "red", "ok": False})
    assert skip["status"] == "substrate_red"


def test_real_flock_mutex_serializes(monkeypatch, tmp_path):
    """The real flock-based slot: first acquire wins, and (held by THIS process) a repeat
    returns True; the lock auto-releases on process exit (covered by the OS)."""
    monkeypatch.setattr(foundation.runtime, "RUN_DIR", tmp_path)
    monkeypatch.setattr(foundation, "_cron_slot_fh", None, raising=False)
    assert foundation._acquire_cron_slot() is True
    assert foundation._acquire_cron_slot() is True   # idempotent for the holding process
