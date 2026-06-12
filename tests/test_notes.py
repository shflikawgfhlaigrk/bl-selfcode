"""Notes capability — AppleScript literal correctness (the ``!r`` single-quote repr
was an AppleScript syntax error on EVERY call; same bug class already proven live in
notify on 2026-06-10), honest gate, and never-raises failure paths."""
from __future__ import annotations

from utah import failures
from utah.integrations import notes
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


# ── AppleScript literal correctness ───────────────────────────────────────────

def test_script_uses_double_quoted_applescript_literals():
    """AppleScript accepts ONLY double-quoted strings. Python ``!r`` emits single
    quotes for plain text — a guaranteed osascript syntax error."""
    s = notes._script("My Note", "plain body")
    assert 'name:"My Note"' in s and 'body:"plain body"' in s
    assert "'" not in s.replace("Notes", "")     # no single-quoted literals anywhere


def test_script_escapes_quotes_and_backslashes():
    s = notes._script('He said "go"', "back\\slash")
    assert 'name:"He said \\"go\\""' in s
    assert 'body:"back\\\\slash"' in s


def test_as_str_matches_applescript_rules():
    assert notes._as_str("it's fine") == '"it\'s fine"'
    assert notes._as_str('say "hi"') == '"say \\"hi\\""'
    assert notes._as_str("a\\b") == '"a\\\\b"'


# ── boundary behavior ─────────────────────────────────────────────────────────

def test_add_note_injected_runner_happy_path():
    _store()
    saved = []
    r = notes.add_note("Groceries", "milk", run_fn=lambda t, b: saved.append((t, b)))
    assert r == {"saved": True, "gated": False, "title": "Groceries"}
    assert saved == [("Groceries", "milk")]


def test_add_note_gates_without_perms(monkeypatch):
    store = _store()
    monkeypatch.setattr(notes, "perms_available", lambda: False)
    r = notes.add_note("T", "B")
    assert r["saved"] is False and r["gated"] is True and r["title"] == "T"
    assert any(row[2] == "gated" for row in store.rows)


def test_add_note_runner_failure_documented_not_raised():
    store = _store()

    def boom(t, b):
        raise RuntimeError("Notes not running")

    r = notes.add_note("T", "B", run_fn=boom)
    assert r["saved"] is False and r["gated"] is False
    assert "Notes not running" in r["error"]
    assert any(row[2] == "save_failed" for row in store.rows)


def test_perms_available_reflects_flag_file(tmp_path, monkeypatch):
    flag = tmp_path / "macos.json"
    monkeypatch.setattr(notes, "MACOS_FLAG", flag)
    assert notes.perms_available() is False
    flag.write_text("{}")
    assert notes.perms_available() is True
