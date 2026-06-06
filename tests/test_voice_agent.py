"""Voice turn handler: a transcript -> wake gate -> SAME brain pipeline as chat
(core.tell_stream) -> speak the answer -> publish the turn so it shows in the deck
chat box. No wake => ignored (the mic never acts on un-addressed speech)."""
from __future__ import annotations

from utah.voice import agent


def _boom(*a, **k):
    raise AssertionError("must not be called")


def test_no_wake_is_ignored():
    assert agent.handle_utterance("tell me the weather", tell_stream=_boom,
                                  speak=_boom, publish=_boom) is None


def test_wake_runs_brain_speaks_and_publishes():
    spoken, published = [], []
    def fake_tell(cmd):
        assert cmd == "what is utah"
        return iter([("source", "brain"), ("thinking", "reasoning"),
                     ("answer", "Utah is the rebuild."), ("done", "Utah is the rebuild.")])
    r = agent.handle_utterance(
        "ace what is utah",
        tell_stream=fake_tell, speak=spoken.append,
        publish=lambda ch, ev: published.append((ch, ev)),
    )
    assert r["command"] == "what is utah"
    assert r["answer"] == "Utah is the rebuild."
    assert spoken == ["Utah is the rebuild."]            # spoke the answer once
    assert any(ev.get("q") == "what is utah" for ch, ev in published)  # shown on deck


def test_bare_wake_does_not_call_brain():
    r = agent.handle_utterance("ace", tell_stream=_boom, speak=lambda x: None,
                               publish=lambda *a: None)
    assert r is not None and r["command"] == "" and not r.get("answer")


def test_empty_answer_is_not_spoken():
    spoken = []
    r = agent.handle_utterance(
        "ace hello", tell_stream=lambda c: iter([("source", "brain"), ("answer", "  ")]),
        speak=spoken.append, publish=lambda *a: None,
    )
    assert spoken == []  # nothing meaningful to say -> stays silent
    assert r["answer"] == ""


def test_handler_never_raises_if_speak_or_publish_fail():
    # a TTS/publish hiccup must not crash the voice loop
    r = agent.handle_utterance(
        "ace hi",
        tell_stream=lambda c: iter([("answer", "hey")]),
        speak=lambda x: (_ for _ in ()).throw(RuntimeError("audio device gone")),
        publish=lambda *a: (_ for _ in ()).throw(RuntimeError("bus down")),
    )
    assert r["answer"] == "hey"  # turn still completes
