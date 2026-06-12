"""macOS ops capability — full boundary coverage: honest gates per op, injected
runners, failure paths documented (never raised), input validation that refuses
empty targets BEFORE spawning a subprocess, and shortcut output surfaced."""
from __future__ import annotations

from utah import failures
from utah.integrations import macos
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


# ── honest gates: every op documents the gate and returns done=False ─────────

def test_each_op_gates_without_perms(monkeypatch):
    store = _store()
    monkeypatch.setattr(macos, "perms_available", lambda: False)
    assert macos.read_clipboard() == {"ok": False, "gated": True}
    assert macos.reveal_file("/tmp/x")["gated"] is True
    assert macos.run_shortcut("Morning")["gated"] is True
    gated = [r for r in store.rows if r[2] == "gated"]
    assert len(gated) == 3                       # one documented gate per op


def test_perms_available_reflects_flag_file(tmp_path, monkeypatch):
    flag = tmp_path / "macos.json"
    monkeypatch.setattr(macos, "MACOS_FLAG", flag)
    assert macos.perms_available() is False
    flag.write_text("{}")
    assert macos.perms_available() is True


# ── injected runners: happy paths ─────────────────────────────────────────────

def test_reveal_file_passes_path_to_runner():
    _store()
    seen = []
    r = macos.reveal_file("/tmp/some file.txt", run_fn=lambda p: seen.append(p))
    assert r["ok"] is True and r["gated"] is False and r["path"] == "/tmp/some file.txt"
    assert seen == ["/tmp/some file.txt"]


def test_run_shortcut_surfaces_runner_stdout():
    """Shortcut output is real data (e.g. a Shortcuts automation returning text) —
    it must reach the caller, not vanish in a CompletedProcess."""
    _store()

    class _Res:
        stdout = "42\n"

    r = macos.run_shortcut("Calc", run_fn=lambda n: _Res())
    assert r["ok"] is True and r["shortcut"] == "Calc"
    assert r["output"] == "42"


def test_run_shortcut_without_stdout_still_ok():
    _store()
    r = macos.run_shortcut("Morning", run_fn=lambda n: None)
    assert r["ok"] is True and r["output"] is None


# ── failure paths: documented, never raised ───────────────────────────────────

def test_clipboard_runner_failure_documented_not_raised():
    store = _store()

    def boom():
        raise OSError("pbpaste missing")

    r = macos.read_clipboard(run_fn=boom)
    assert r["ok"] is False and r["gated"] is False and "pbpaste" in r["error"]
    assert any(row[2] == "clipboard_failed" for row in store.rows)


def test_reveal_runner_failure_documented_not_raised():
    store = _store()

    def boom(p):
        raise RuntimeError("open failed")

    r = macos.reveal_file("/tmp/x", run_fn=boom)
    assert r["ok"] is False and "open failed" in r["error"]
    assert any(row[2] == "reveal_failed" for row in store.rows)


def test_shortcut_runner_failure_documented_not_raised():
    store = _store()

    def boom(n):
        raise RuntimeError("no such shortcut")

    r = macos.run_shortcut("Ghost", run_fn=boom)
    assert r["ok"] is False and "no such shortcut" in r["error"]
    assert any(row[2] == "shortcut_failed" for row in store.rows)


# ── input validation: refuse empty targets BEFORE any subprocess ──────────────

def test_reveal_file_rejects_empty_path_without_running():
    _store()
    called = []
    r = macos.reveal_file("", run_fn=lambda p: called.append(p))
    assert r["ok"] is False and r["gated"] is False and "path" in r["error"]
    assert called == []                          # runner never invoked


def test_run_shortcut_rejects_blank_name_without_running():
    _store()
    called = []
    r = macos.run_shortcut("   ", run_fn=lambda n: called.append(n))
    assert r["ok"] is False and "name" in r["error"]
    assert called == []
