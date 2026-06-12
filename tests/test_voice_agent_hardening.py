"""Hardening for the voice agent's untested paths: the voice→coding-agent bridge
("ace, code <task>"), the dispatch HTTP boundary (bounded, never-raises, honest
when the web layer is down), pulse_wake resilience, and the ack-cooldown's
thread-safety under concurrent utterances."""
from __future__ import annotations

import json
import threading

import pytest

from utah.objects import Reply, ReplySource
from utah.voice import agent


def _speak(spoken):
    def fake(chunks, on_start=None):
        text = "".join(chunks)
        if on_start is not None and text.strip():
            on_start()
        if text.strip():
            spoken.append(text)
        return text
    return fake


# ── coding_task: the spoken-command → self-code-task parser ─────────────────

@pytest.mark.parametrize("cmd,task", [
    ("code fix the flaky probate test", "fix the flaky probate test"),
    ("self code tighten the echo gate", "tighten the echo gate"),
    ("selfcode add a retry", "add a retry"),
    ("CODE Fix The Tests", "Fix The Tests"),            # case-insensitive prefix match
    ("fix your wake word detector", "fix your wake word detector"),
    ("improve your latency", "improve your latency"),
    ("debug your mic loop", "debug your mic loop"),
])
def test_coding_task_extracts_the_task(cmd, task):
    assert agent.coding_task(cmd) == task


@pytest.mark.parametrize("cmd", [
    "what's the weather",        # a normal brain turn
    "code",                      # bare prefix, no task
    "code   ",                   # whitespace-only task
    "",                          # empty
    None,                        # defensive: never raises on None
    "decode this message",       # 'code' inside a word must not trigger
])
def test_coding_task_none_for_non_coding_commands(cmd):
    assert agent.coding_task(cmd) is None


# ── the voice→selfcode turn ──────────────────────────────────────────────────

def test_code_command_dispatches_and_acknowledges():
    spoken, published, dispatched = [], [], []

    def fake_dispatch(task):
        dispatched.append(task)
        return "job-42"

    r = agent.handle_utterance(
        "ace code fix the echo gate",
        dispatch=fake_dispatch, speak_stream=_speak(spoken),
        publish=lambda ch, ev: published.append((ch, ev)),
        tell=lambda c: (_ for _ in ()).throw(AssertionError("brain must not run")),
        tell_stream=lambda *a, **k: (_ for _ in ()).throw(AssertionError("brain must not run")),
    )
    assert dispatched == ["fix the echo gate"]
    assert r["source"] == "selfcode" and r["job"] == "job-42"
    assert spoken and "coding" in spoken[0].lower()
    assert any(ch == "voice" and ev.get("source") == "selfcode" for ch, ev in published)


def test_code_command_speaks_honest_failure_when_web_layer_down():
    """dispatch=None (deck web layer dead) must NOT claim the job started."""
    spoken = []
    r = agent.handle_utterance(
        "ace code fix the echo gate",
        dispatch=lambda task: None, speak_stream=_speak(spoken),
        publish=lambda *a: None,
    )
    assert r["job"] is None
    assert spoken and "couldn't" in spoken[0].lower()    # honest, not fake-ok


# ── the dispatch HTTP boundary ───────────────────────────────────────────────

def test_selfcode_dispatch_posts_console_line_and_returns_job():
    seen = {}

    def fake_post(url, data):
        seen["url"], seen["body"] = url, json.loads(data.decode())
        return json.dumps({"job": "j-7"})

    job = agent._selfcode_dispatch("fix the tests", http_post=fake_post)
    assert job == "j-7"
    assert seen["body"] == {"line": "/code fix the tests"}
    assert seen["url"].endswith("/api/console")


@pytest.mark.parametrize("bad", [
    lambda url, data: (_ for _ in ()).throw(OSError("connection refused")),
    lambda url, data: "not json{",
    lambda url, data: "null",
])
def test_selfcode_dispatch_returns_none_on_any_boundary_failure(bad):
    assert agent._selfcode_dispatch("task", http_post=bad) is None


def test_console_url_is_env_configurable(monkeypatch):
    """The deck web port is deployment config, not a hardcoded constant buried in
    the function body — module reads UTAH_VOICE_CONSOLE_URL."""
    assert agent._CONSOLE_URL.startswith("http://127.0.0.1")  # safe localhost default
    seen = {}

    def fake_post(url, data):
        seen["url"] = url
        return json.dumps({"job": "x"})

    monkeypatch.setattr(agent, "_CONSOLE_URL", "http://127.0.0.1:9999")
    agent._selfcode_dispatch("t", http_post=fake_post)
    assert seen["url"] == "http://127.0.0.1:9999/api/console"


# ── pulse_wake resilience ────────────────────────────────────────────────────

def test_pulse_wake_publishes_and_swallows_failures():
    got = []
    agent.pulse_wake("hello", publish=lambda ch, ev: got.append((ch, ev)))
    assert got == [("wake", {"command": "hello"})]
    agent.pulse_wake("x", publish=lambda *a: (_ for _ in ()).throw(RuntimeError("bus down")))


# ── ack cooldown is race-free ────────────────────────────────────────────────

def test_bare_wake_ack_fires_exactly_once_under_concurrency():
    """Two simultaneous bare 'ace' utterances (STT thrash) must produce ONE 'Yeah?'
    — the check-then-set on the cooldown clock has to be atomic or both pass it."""
    agent._last_ack_at = 0.0
    start = threading.Barrier(2)
    spoken: list[str] = []
    lock = threading.Lock()

    def speak(chunks, on_start=None):
        text = "".join(chunks)
        with lock:
            if text.strip():
                spoken.append(text)
        return text

    def turn():
        start.wait(timeout=5)
        agent.handle_utterance("ace", speak_stream=speak, publish=lambda *a: None)

    threads = [threading.Thread(target=turn) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert spoken == ["Yeah?"]


def test_speakable_handles_empty_and_markdown_only():
    assert agent._speakable("") == ""
    assert agent._speakable("***``###") == ""
    assert agent._speakable(None) == ""
