"""macapp hardening that needs no framework stub: every codesign subprocess is
TIME-BOUNDED (a wedged codesign daemon must not hang the supervisor's voice-child
spec forever), a timeout/verify failure degrades to the honest fallback (ensure()
→ None, supervisor uses sys.executable), and the Info.plist carries the exact
keys TCC needs."""
from __future__ import annotations

import hashlib
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

from utah.voice import macapp


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    app = tmp_path / "UtahVoice.app"
    monkeypatch.setattr(macapp, "APP_PATH", app)
    monkeypatch.setattr(macapp, "_marker_path", lambda: tmp_path / "voiceapp.source")
    return app


@pytest.fixture
def fake_stub(tmp_path):
    stub = tmp_path / "stub-binary"
    stub.write_bytes(b"\xfe\xed\xfa\xcefake-mach-o")
    return stub


def _plant_valid_bundle(app, stub, marker_path):
    """A bundle that passes every static _needs_rebuild check, so the codesign
    verify step is the deciding one."""
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / macapp.EXEC_NAME).write_bytes(b"exe")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(macapp._info_plist()))
    marker_path.write_text(hashlib.sha256(stub.read_bytes()).hexdigest())


def test_build_codesign_is_time_bounded(sandbox, fake_stub, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(macapp.subprocess, "run", fake_run)
    macapp._build(str(fake_stub))
    assert seen["cmd"][0] == "codesign"
    assert seen["timeout"] is not None and 0 < seen["timeout"] <= 600


def test_needs_rebuild_verify_is_time_bounded(sandbox, fake_stub, tmp_path, monkeypatch):
    _plant_valid_bundle(sandbox, fake_stub, tmp_path / "voiceapp.source")
    seen = {}

    def fake_run(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(macapp.subprocess, "run", fake_run)
    assert macapp._needs_rebuild(str(fake_stub)) is False
    assert seen["timeout"] is not None and 0 < seen["timeout"] <= 600


def test_needs_rebuild_true_when_codesign_hangs(sandbox, fake_stub, tmp_path, monkeypatch):
    """An unverifiable signature (codesign wedged → TimeoutExpired) is treated as
    stale — rebuild — never an unhandled exception into the supervisor."""
    _plant_valid_bundle(sandbox, fake_stub, tmp_path / "voiceapp.source")

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 1)

    monkeypatch.setattr(macapp.subprocess, "run", hang)
    assert macapp._needs_rebuild(str(fake_stub)) is True


def test_needs_rebuild_true_when_bundle_missing(sandbox, fake_stub):
    assert macapp._needs_rebuild(str(fake_stub)) is True


def test_build_raises_on_codesign_failure(sandbox, fake_stub, monkeypatch):
    monkeypatch.setattr(
        macapp.subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="codesign failed"):
        macapp._build(str(fake_stub))


def test_ensure_none_when_stub_unavailable(monkeypatch):
    monkeypatch.setattr(macapp, "_source_stub_and_home", lambda: None)
    assert macapp.ensure() is None


def test_ensure_none_when_build_fails(sandbox, monkeypatch):
    """A failed build (including a codesign hang) falls back honestly: None, so the
    supervisor launches via sys.executable instead of crashing."""
    monkeypatch.setattr(macapp, "_source_stub_and_home", lambda: ("/x/stub", "/x"))
    monkeypatch.setattr(macapp, "_needs_rebuild", lambda src: True)

    def boom(src):
        raise subprocess.TimeoutExpired(["codesign"], 1)

    monkeypatch.setattr(macapp, "_build", boom)
    assert macapp.ensure() is None


def test_info_plist_carries_the_tcc_contract():
    plist = macapp._info_plist()
    assert plist["CFBundleIdentifier"] == "com.utah.voice"
    assert plist["CFBundleExecutable"] == macapp.EXEC_NAME
    assert plist["NSMicrophoneUsageDescription"]   # non-empty consent string
    assert plist["LSUIElement"] is True            # background agent, no Dock icon
