"""The voice loop must run from a signed ``com.utah.voice`` .app so macOS will
grant it the microphone under launchd (a bare framework-python child is deaf —
TCC attributes consent to the ungranted launchd responsible process).

These tests prove the builder produces a *valid, signed, idempotent* bundle with
the mic usage string, and that the supervisor merges the per-child env the
bundle needs. Mac-only where a real framework stub / ``codesign`` is required.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys

import pytest

from utah.voice import macapp

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="bundle/codesign are macOS-only")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect the bundle + marker into a tmp dir so tests never touch ~/.utah."""
    app = tmp_path / "UtahVoice.app"
    monkeypatch.setattr(macapp, "APP_PATH", app)
    monkeypatch.setattr(macapp, "_marker_path", lambda: tmp_path / "voiceapp.source")
    return app


@darwin_only
def test_ensure_builds_signed_bundle_with_mic_string(sandbox):
    info = macapp.ensure(force=True)
    assert info is not None
    exe = sandbox / "Contents" / "MacOS" / "UtahVoice"
    assert exe.exists() and os.access(exe, os.X_OK)

    plist = plistlib.loads((sandbox / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == "com.utah.voice"
    assert plist["NSMicrophoneUsageDescription"]  # non-empty consent string
    assert plist["LSUIElement"] is True  # background agent, still allowed to prompt

    # Signed, adhoc, with our stable identifier — and the seal verifies.
    assert subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(sandbox)]
    ).returncode == 0
    out = subprocess.run(["codesign", "-dvv", str(sandbox)], capture_output=True, text=True).stderr
    assert "Identifier=com.utah.voice" in out


@darwin_only
def test_ensure_is_idempotent(sandbox):
    macapp.ensure(force=True)
    exe = sandbox / "Contents" / "MacOS" / "UtahVoice"
    before = exe.stat().st_mtime_ns
    macapp.ensure()  # not forced — nothing changed, must not rebuild
    assert exe.stat().st_mtime_ns == before


@darwin_only
def test_ensure_rebuilds_when_marker_missing(sandbox, tmp_path):
    macapp.ensure(force=True)
    (tmp_path / "voiceapp.source").unlink()  # marker gone → stale
    assert macapp._needs_rebuild(macapp._source_stub_and_home()[0]) is True


@darwin_only
def test_bundle_exec_keeps_identity_and_imports_voice_stack(sandbox):
    """The copied app stub must NOT relaunch into the shared framework Python
    (that would lose our bundle identity) and must import the real voice deps."""
    info = macapp.ensure(force=True)
    env = {**os.environ, **info.env}
    probe = (
        "import ctypes,sys;"
        "b=ctypes.create_string_buffer(4096);s=ctypes.c_uint32(4096);"
        "ctypes.CDLL(None)._NSGetExecutablePath(b,ctypes.byref(s));"
        "import sounddevice;from utah.voice import oww,stt,tts,vad,loop;"
        "print(b.value.decode())"
    )
    out = subprocess.run(
        [info.exec_path, "-c", probe], env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == info.exec_path  # stayed in our bundle, no relaunch


def test_bundle_info_env_has_pythonhome_and_pythonpath(sandbox):
    if sys.platform != "darwin":
        pytest.skip("macOS-only")
    info = macapp.ensure(force=True)
    assert info.env["PYTHONHOME"]
    assert "site-packages" in info.env["PYTHONPATH"]


def test_non_darwin_returns_none(monkeypatch):
    monkeypatch.setattr(macapp.sys, "platform", "linux")
    assert macapp.ensure() is None
