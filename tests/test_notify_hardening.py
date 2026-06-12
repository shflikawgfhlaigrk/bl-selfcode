"""Notify hardening — the real osascript argv is built by a PURE helper (testable
without macOS), failure paths are documented not raised, and the AppleScript
escaping contract holds at the argv level."""
from __future__ import annotations

from utah import failures
from utah.integrations import notify
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


def test_osascript_args_builds_escaped_display_notification():
    argv = notify._osascript_args("Trade", 'MEANREV "SHORT" @ 28676.25')
    assert argv[0] == "osascript" and argv[1] == "-e"
    script = argv[2]
    assert script.startswith("display notification ")
    assert '"MEANREV \\"SHORT\\" @ 28676.25"' in script
    assert 'with title "Trade"' in script


def test_osascript_args_survive_apostrophes_and_backslashes():
    script = notify._osascript_args("Utah", "it's a back\\slash")[2]
    assert '"it\'s a back\\\\slash"' in script


def test_runner_failure_documented_not_raised():
    store = _store()

    def boom(t, m):
        raise OSError("osascript: not allowed")

    r = notify.notify("hi", run_fn=boom)
    assert r["sent"] is False and r["gated"] is False
    assert "not allowed" in r["error"]
    assert any(row[2] == "send_failed" for row in store.rows)


def test_title_default_and_passthrough():
    _store()
    seen = []
    notify.notify("m", run_fn=lambda t, m: seen.append((t, m)))
    notify.notify("m2", title="Custom", run_fn=lambda t, m: seen.append((t, m)))
    assert seen == [("Utah", "m"), ("Custom", "m2")]


def test_perms_available_reflects_flag_file(tmp_path, monkeypatch):
    flag = tmp_path / "macos.json"
    monkeypatch.setattr(notify, "MACOS_FLAG", flag)
    assert notify.perms_available() is False
    flag.write_text("{}")
    assert notify.perms_available() is True
