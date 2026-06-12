"""voice.state heartbeat-integrity contracts: the writer stamps its OWN clock (a
caller-supplied ts can never forge freshness), concurrent writers never leave a
torn file for the /voice route to trip on, and status() fills the deck's keys
with safe defaults when the loop wrote a sparse heartbeat."""
from __future__ import annotations

import json
import threading

from utah.voice import state as vstate


def test_write_stamps_its_own_ts_caller_cannot_forge(tmp_path, monkeypatch):
    """A buggy/old caller passing ts must be overridden — otherwise a stale loop
    could replay a fresh-looking heartbeat and the deck would show a live mic
    that is actually dead."""
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="listening", ts=12345.0)
    got = vstate.read()
    assert got["ts"] != 12345.0
    assert vstate.status()["status"] == "listening"   # fresh by the WRITER's clock


def test_concurrent_writers_never_tear_the_file(tmp_path, monkeypatch):
    """The loop + monitor threads both heartbeat. Readers during the storm must
    only ever see a complete JSON dict (atomic replace) — never a partial write."""
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    stop = threading.Event()
    bad: list[str] = []

    def writer(n):
        for i in range(200):
            vstate.write(status="listening", segments=i, writer=n)

    def reader():
        while not stop.is_set():
            raw = None
            try:
                raw = (tmp_path / "voice.json").read_text()
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    bad.append(raw)
            except FileNotFoundError:
                continue
            except ValueError:
                bad.append(raw or "")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    rt = threading.Thread(target=reader)
    rt.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    stop.set()
    rt.join(timeout=10)
    assert bad == []
    final = vstate.read()
    assert isinstance(final, dict) and final["status"] == "listening"
    litter = [p.name for p in tmp_path.iterdir() if p.name.startswith(".voice")]
    assert litter == []                                # no temp-file leaks under load


def test_status_defaults_for_sparse_heartbeat(tmp_path, monkeypatch):
    """A minimal heartbeat (just status+ts) still yields the deck's full shape with
    safe defaults — the /voice consumers .get() these keys."""
    monkeypatch.setattr(vstate, "STATE_PATH", tmp_path / "voice.json")
    vstate.write(status="listening")
    s = vstate.status()
    assert s["listening"] is False and s["speaking"] is False
    assert s["segments"] == 0
    assert s["last_transcript"] is None and s["last_wake"] is None
