"""Hardening for utah.voice.state — the deck's /voice route and the supervisor's
deaf-restart probe both consume this file. A corrupt/garbage state file must read
as ``down`` (the honest degraded answer), never crash the web layer; a failed
write must not litter the run dir with orphaned temp files."""
from __future__ import annotations

import json
import os

from utah.voice import state as vstate


def test_status_is_down_on_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "voice.json"
    p.write_text("{not json!!")
    monkeypatch.setattr(vstate, "STATE_PATH", p)
    s = vstate.status()
    assert s["status"] == "down" and s["listening"] is False and s["speaking"] is False


def test_status_is_down_on_non_numeric_ts(tmp_path, monkeypatch):
    """A garbage ``ts`` (hand-edited file, partial write) must NOT raise TypeError
    into the /voice route — it reads as down."""
    p = tmp_path / "voice.json"
    p.write_text(json.dumps({"status": "listening", "listening": True, "ts": "garbage"}))
    monkeypatch.setattr(vstate, "STATE_PATH", p)
    s = vstate.status()
    assert s["status"] == "down" and s["listening"] is False


def test_read_returns_none_for_non_dict_payload(tmp_path, monkeypatch):
    """A JSON array/string is not a state dict — read() must say None, and status()
    must degrade to down instead of crashing on .get()."""
    p = tmp_path / "voice.json"
    p.write_text(json.dumps(["not", "a", "dict"]))
    monkeypatch.setattr(vstate, "STATE_PATH", p)
    assert vstate.read() is None
    assert vstate.status()["status"] == "down"


def test_status_treats_future_ts_as_fresh_not_crash(tmp_path, monkeypatch):
    """Clock skew (ts slightly in the future) is not staleness."""
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="listening", listening=True)
    ts = vstate.read()["ts"]
    s = vstate.status(now=ts - 2.0)  # reader's clock 2s behind the writer's
    assert s["status"] == "listening" and s["listening"] is True


def test_write_failure_never_raises_and_leaves_no_tmp_litter(tmp_path, monkeypatch):
    """If the serialize/replace step fails the temp file must be cleaned up —
    a leaked .voice*.json per failure would fill the run dir on a busy loop."""
    p = tmp_path / "voice.json"
    monkeypatch.setattr(vstate, "STATE_PATH", p)

    class Unserializable:
        pass

    vstate.write(status="listening", junk=Unserializable())  # json.dump raises inside
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".voice")]
    assert leftovers == []
    assert vstate.read() is None  # nothing half-written


def test_write_failure_is_silent_when_dir_uncreatable(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the dir should be")  # mkdir(parents) will fail
    monkeypatch.setattr(vstate, "STATE_PATH", blocked / "sub" / "voice.json")
    vstate.write(status="listening")  # must not raise


def test_status_shape_is_stable_for_the_deck(tmp_path, monkeypatch):
    """The deck JSON contract: fresh state always carries these keys."""
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="thinking", listening=False, speaking=False, segments=7,
                 last_transcript="hi", last_wake="ace")
    s = vstate.status()
    assert set(s) == {"status", "listening", "speaking", "segments",
                      "last_transcript", "last_wake", "age_s"}
    assert s["segments"] == 7 and s["last_wake"] == "ace"
