"""Voice state must be REAL on the deck — the live loop's actual listening/thinking/
speaking state, never a hardcoded {idle,false}. The loop writes a heartbeat'd state
file; the deck reads it; a stale file (loop dead) reads as 'down', not a frozen lie."""
from __future__ import annotations

from utah.voice import state as vstate


def test_write_then_read_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="listening", listening=True, speaking=False, segments=3)
    got = vstate.read()
    assert got["status"] == "listening" and got["listening"] is True and got["segments"] == 3
    assert "ts" in got


def test_status_reflects_fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="speaking", listening=False, speaking=True)
    s = vstate.status(now=None)
    assert s["status"] == "speaking" and s["speaking"] is True and s["listening"] is False


def test_status_is_down_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "nope.json")
    s = vstate.status()
    assert s["status"] == "down" and s["listening"] is False


def test_status_is_down_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="listening", listening=True)
    # read back, then ask for status far in the future -> heartbeat is stale -> down
    ts = vstate.read()["ts"]
    s = vstate.status(now=ts + vstate.STALE_S + 5)
    assert s["status"] == "down" and s["listening"] is False
