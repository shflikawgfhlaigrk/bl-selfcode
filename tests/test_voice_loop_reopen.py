"""In-process mic-stream self-heal (the voice restart-storm fix).

Root cause (2026-06-14): a WEDGED CoreAudio handle keeps delivering zero-filled
buffers — PortAudio never raises — so the ``with sd.RawInputStream`` block never
exits and the ONLY recovery was the supervisor killing+respawning the whole voice
app (~60-75 s of dead voice + a full model/brain cold-start every episode). The
loop must instead REOPEN the stream in-process on sustained device-zeros (cheap,
~100 ms), escalating to the supervisor's heavier restart only after a bounded
number of in-process attempts fail. ``_should_reopen_stream`` is that decision,
kept pure so it is unit-tested here.
"""
from __future__ import annotations

from utah.voice import loop


def test_no_reopen_below_threshold():
    # Device quiet briefly (or a normal silent room) — never reopen.
    assert loop._should_reopen_stream(quiet_for_s=2.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is False
    assert loop._should_reopen_stream(quiet_for_s=8.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is False


def test_reopen_when_deaf_past_threshold_with_budget():
    # Sustained device-zeros past the in-process window, attempts remaining → reopen.
    assert loop._should_reopen_stream(quiet_for_s=12.0, reopen_after_s=8.0,
                                      reopen_count=0, max_reopens=3) is True
    assert loop._should_reopen_stream(quiet_for_s=30.0, reopen_after_s=8.0,
                                      reopen_count=2, max_reopens=3) is True


def test_no_reopen_when_budget_exhausted():
    # In-process reopens didn't recover the mic → STOP; let the deaf heartbeat reach
    # the supervisor so its restart (the correct heavier recovery) takes over.
    assert loop._should_reopen_stream(quiet_for_s=40.0, reopen_after_s=8.0,
                                      reopen_count=3, max_reopens=3) is False
    assert loop._should_reopen_stream(quiet_for_s=40.0, reopen_after_s=8.0,
                                      reopen_count=9, max_reopens=3) is False


def test_disabled_when_window_non_positive():
    # UTAH_VOICE_STREAM_REOPEN_S=0 disables in-process reopen entirely.
    assert loop._should_reopen_stream(quiet_for_s=999.0, reopen_after_s=0.0,
                                      reopen_count=0, max_reopens=3) is False
    assert loop._should_reopen_stream(quiet_for_s=999.0, reopen_after_s=-1.0,
                                      reopen_count=0, max_reopens=3) is False


def test_set_app_nap_default_writes_user_default(monkeypatch):
    """App Nap is disabled via the bundle's USER DEFAULTS — never by rebuilding the
    adhoc bundle (a rebuild changes the cdhash and orphans the cdhash-only TCC mic
    grant → deaf under launchd). Assert the safe `defaults write` path, not a rebuild."""
    calls = {}

    class _R:
        returncode = 0

    def _fake_run(argv, **kw):
        calls["argv"] = argv
        return _R()

    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert loop._set_app_nap_default() is True
    assert calls["argv"][:2] == ["defaults", "write"]
    assert "com.utah.voice" in calls["argv"]
    assert "NSAppSleepDisabled" in calls["argv"]


def test_set_app_nap_default_never_raises(monkeypatch):
    """A defaults failure must never crash voice startup — App Nap mitigation is
    best-effort; the in-process stream reopen is the real safety net."""
    import subprocess

    def _boom(*a, **k):
        raise OSError("defaults missing")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert loop._set_app_nap_default() is False
