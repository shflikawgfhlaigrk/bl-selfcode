"""Contacts lookup — argv-passed AppleScript (injection-proof), robust parsing, honest gates.

Same incident class the iMessage relay already fixed (2026-06-09): interpolating user
text into AppleScript source via Python ``!r`` repr produces single-quoted strings that
AppleScript rejects — every real lookup died at compile time, and a crafted name could
inject script. The query must ride ``osascript`` argv, never the script source.
"""
from __future__ import annotations

import pytest

from utah import failures
from utah.integrations import contacts
from tests.fakes import FakeFailureStore

NASTY_NAMES = [
    "plain ascii",
    "it's O'Brien",
    'a "quoted" name',
    'tell application "Finder" to delete every file',   # injection attempt
    "Renée — café owner",
]


def _fake_run(captured, stdout="", returncode=0, stderr=""):
    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input", "")
        captured["kwargs"] = kwargs

        class P:
            pass
        P.returncode = returncode
        P.stdout = stdout
        P.stderr = stderr
        return P()
    return run


@pytest.mark.parametrize("name", NASTY_NAMES)
def test_query_rides_argv_never_script_source(name, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(contacts.subprocess, "run", _fake_run(captured, stdout="Jane Doe"))
    contacts._osascript_lookup(name)
    assert captured["cmd"][-1] == name                  # argv carries the query…
    assert name not in captured["input"]                # …the compiled script never does
    assert "on run argv" in captured["input"]


def test_lookup_parses_newline_separated_names(monkeypatch):
    """Names containing commas (suffixes, 'Doe, Jane' listings) must survive — the
    old comma-split sheared them apart."""
    failures.set_store(FakeFailureStore())
    captured: dict = {}
    monkeypatch.setattr(contacts.subprocess, "run",
                        _fake_run(captured, stdout="Jane Doe\nJohn Smith, Jr.\n"))
    monkeypatch.setattr(contacts, "perms_available", lambda: True)
    r = contacts.lookup("J")
    assert r["found"] is True and r["gated"] is False
    assert [x["name"] for x in r["results"]] == ["Jane Doe", "John Smith, Jr."]


def test_lookup_empty_output_is_honest_not_found(monkeypatch):
    failures.set_store(FakeFailureStore())
    captured: dict = {}
    monkeypatch.setattr(contacts.subprocess, "run", _fake_run(captured, stdout="\n"))
    monkeypatch.setattr(contacts, "perms_available", lambda: True)
    r = contacts.lookup("Nobody Realword")
    assert r["found"] is False and r["results"] == [] and r["gated"] is False


def test_lookup_nonzero_exit_is_recorded_failure(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    captured: dict = {}
    monkeypatch.setattr(contacts.subprocess, "run",
                        _fake_run(captured, returncode=1, stderr="Not authorized (-1743)"))
    monkeypatch.setattr(contacts, "perms_available", lambda: True)
    r = contacts.lookup("Jane")
    assert r["found"] is False and r["gated"] is False
    assert "1743" in r["error"]
    assert any("lookup_failed" in row[2] for row in store.rows)  # rows = (seq, source, kind, detail)


def test_lookup_empty_name_never_reaches_osascript(monkeypatch):
    failures.set_store(FakeFailureStore())
    called = []
    monkeypatch.setattr(contacts, "_osascript_lookup", lambda n: called.append(n) or [])
    monkeypatch.setattr(contacts, "perms_available", lambda: True)
    r = contacts.lookup("   ")
    assert r["found"] is False and r["gated"] is True
    assert called == []


def test_osascript_call_is_time_bounded(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(contacts.subprocess, "run", _fake_run(captured, stdout="Jane"))
    contacts._osascript_lookup("Jane")
    assert 0 < captured["kwargs"]["timeout"] <= 60
