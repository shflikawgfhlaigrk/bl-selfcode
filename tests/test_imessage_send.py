"""iMessage send boundary — hourly rate cap, kill switch, honest gates, bounded osascript.

The relay texts from Michael's PERSONAL number, so a runaway loop is a real-world
incident (carrier spam flag / contact burn), not a log line. These tests lock the
in-module hourly cap (file-backed, prunes itself, never raises on bad state) plus
the send() result contract every caller (utah.sms fallback) relies on.
"""
from __future__ import annotations

import json
import time

from utah import failures
from utah.integrations import imessage
from tests.fakes import FakeFailureStore


def _wire_real_path(monkeypatch, tmp_path, sent):
    """Point the rate file at tmp and fake the osascript hop so the REAL send()
    path (send_fn=None) runs without touching Messages.app."""
    monkeypatch.setattr(imessage, "_RATE_FILE", tmp_path / "imessage_sends.json")
    monkeypatch.setattr(imessage, "_osascript_send",
                        lambda to, body: sent.append((to, body)) or "sms")


def test_real_sends_are_rate_capped_per_hour(monkeypatch, tmp_path):
    store = FakeFailureStore(); failures.set_store(store)
    sent: list = []
    _wire_real_path(monkeypatch, tmp_path, sent)
    monkeypatch.setattr(imessage, "_MAX_PER_HOUR", 3)

    for i in range(3):
        r = imessage.send(f"+1555000{i:04d}", "hi")
        assert r["sent"] is True and r["channel"] == "sms"
    over = imessage.send("+15550009999", "hi")
    assert over["sent"] is False and over["gated"] is True
    assert "rate" in over["reason"].lower()
    assert len(sent) == 3                                   # the 4th never reached osascript
    assert any("rate_limited" in row[2] for row in store.rows)  # rows = (seq, source, kind, detail)


def test_rate_window_prunes_old_sends(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    sent: list = []
    _wire_real_path(monkeypatch, tmp_path, sent)
    monkeypatch.setattr(imessage, "_MAX_PER_HOUR", 2)
    # two sends over an hour ago must NOT count against the cap
    stale = [time.time() - 4000, time.time() - 7200]
    (tmp_path / "imessage_sends.json").write_text(json.dumps(stale))
    r = imessage.send("+15550001111", "hi")
    assert r["sent"] is True and sent


def test_garbled_rate_state_never_blocks_a_send(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    sent: list = []
    _wire_real_path(monkeypatch, tmp_path, sent)
    (tmp_path / "imessage_sends.json").write_text("{not json !!")
    r = imessage.send("+15550001111", "hi")
    assert r["sent"] is True                                # bad state = empty, not a crash


def test_injected_send_fn_bypasses_the_rate_file(monkeypatch, tmp_path):
    """Unit tests and capability probes inject send_fn — they must never touch
    (or be blocked by) the live machine's rate state."""
    failures.set_store(FakeFailureStore())
    rate = tmp_path / "imessage_sends.json"
    monkeypatch.setattr(imessage, "_RATE_FILE", rate)
    monkeypatch.setattr(imessage, "_MAX_PER_HOUR", 0)       # real path would gate instantly
    r = imessage.send("+15550001111", "hello", send_fn=lambda to, body: None)
    assert r["sent"] is True
    assert not rate.exists()                                # injected sends leave no state


def test_empty_body_is_an_honest_gate(monkeypatch):
    failures.set_store(FakeFailureStore())
    called = []
    r = imessage.send("+15550001111", "   ", send_fn=lambda to, body: called.append(1))
    assert r["sent"] is False and r["gated"] is True
    assert "body" in r["reason"]
    assert called == []                                     # nothing was dispatched


def test_kill_switch_flag_gates_real_sends(monkeypatch, tmp_path):
    store = FakeFailureStore(); failures.set_store(store)
    flag = tmp_path / "imessage.disabled"
    flag.touch()
    monkeypatch.setattr(imessage, "DISABLED_FLAG", flag)
    r = imessage.send("+15550001111", "hi")
    assert r["sent"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)  # rows = (seq, source, kind, detail)


def test_osascript_nonzero_exit_raises_with_stderr(monkeypatch):
    def fake_run(cmd, **kwargs):
        class P:
            returncode = 1
            stdout = ""
            stderr = "Messages got an error: not authorized"
        return P()
    monkeypatch.setattr(imessage.subprocess, "run", fake_run)
    try:
        imessage._osascript_send("+15550001111", "hi")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not authorized" in str(exc)


def test_osascript_unexpected_stdout_raises(monkeypatch):
    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "something weird"
            stderr = ""
        return P()
    monkeypatch.setattr(imessage.subprocess, "run", fake_run)
    try:
        imessage._osascript_send("+15550001111", "hi")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "unexpected" in str(exc)


def test_osascript_call_is_time_bounded(monkeypatch):
    seen = {}
    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        class P:
            returncode = 0
            stdout = "sent:sms"
            stderr = ""
        return P()
    monkeypatch.setattr(imessage.subprocess, "run", fake_run)
    imessage._osascript_send("+15550001111", "hi")
    assert 0 < seen["timeout"] <= 60                        # bounded, never a hang


def test_real_send_records_rate_state(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    sent: list = []
    _wire_real_path(monkeypatch, tmp_path, sent)
    imessage.send("+15550001111", "hi")
    rows = json.loads((tmp_path / "imessage_sends.json").read_text())
    assert len(rows) == 1 and abs(rows[0] - time.time()) < 5
