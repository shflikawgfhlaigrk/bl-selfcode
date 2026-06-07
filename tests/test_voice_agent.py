"""Voice turn handler: a transcript -> wake gate -> SAME brain pipeline as chat
(core.tell_stream) -> speak the answer SENTENCE BY SENTENCE as it streams (low
latency) -> publish the turn so it shows in the deck chat box. No wake => ignored
(the mic never acts on un-addressed speech)."""
from __future__ import annotations

from utah.voice import agent


def _boom(*a, **k):
    raise AssertionError("must not be called")


def _collect(spoken):
    """A fake speak_stream that drives the brain generator and records the text it
    was asked to speak (joining the streamed chunks, like the real pipeline)."""
    def fake(chunks, on_start=None):
        text = "".join(chunks)
        if on_start is not None and text.strip():
            on_start()
        if text.strip():
            spoken.append(text)
        return text
    return fake


def test_no_wake_is_ignored():
    assert agent.handle_utterance("tell me the weather", tell_stream=_boom,
                                  speak_stream=_boom, publish=_boom) is None


def test_wake_runs_brain_speaks_and_publishes():
    spoken, published = [], []
    def fake_tell(cmd):
        assert cmd == "what is utah"
        return iter([("source", "brain"), ("thinking", "reasoning"),
                     ("answer", "Utah is the rebuild."), ("done", "Utah is the rebuild.")])
    r = agent.handle_utterance(
        "ace what is utah",
        tell_stream=fake_tell, speak_stream=_collect(spoken),
        publish=lambda ch, ev: published.append((ch, ev)),
    )
    assert r["command"] == "what is utah"
    assert r["answer"] == "Utah is the rebuild."
    assert r["source"] == "brain"
    assert spoken == ["Utah is the rebuild."]            # spoke the (streamed) answer
    assert any(ev.get("q") == "what is utah" for ch, ev in published)  # shown on deck


def test_only_answer_text_is_spoken_not_thinking():
    """The speaker receives ONLY answer text — the brain's thinking is shown on the
    deck but never read aloud."""
    spoken = []
    def fake_tell(cmd):
        return iter([("source", "brain"),
                     ("thinking", "let me reason about this at length"),
                     ("answer", "The answer is 42.")])
    agent.handle_utterance("ace the question", tell_stream=fake_tell,
                           speak_stream=_collect(spoken), publish=lambda *a: None)
    assert spoken == ["The answer is 42."]               # no thinking leaked to TTS


def test_on_speaking_fires_when_audio_begins():
    """on_speaking flips the deck state thinking → speaking exactly when the first
    real sentence is sent to the speaker."""
    fired = []
    def fake_tell(cmd):
        return iter([("answer", "Hello there.")])
    def fake_speak(chunks, on_start=None):
        text = "".join(chunks)
        if on_start and text.strip():
            on_start()
        return text
    agent.handle_utterance("ace hi", tell_stream=fake_tell, speak_stream=fake_speak,
                           publish=lambda *a: None, on_speaking=lambda: fired.append(1))
    assert fired == [1]


def test_wake_publishes_immediate_wake_event_before_brain():
    """The instant the wake word fires, a 'wake' event is published BEFORE the slow
    brain turn — so the deck orb emits its wave immediately and Michael sees it heard
    'ace' (like old Ace), not 14s later when the answer lands."""
    published, order = [], []
    def fake_tell(cmd):
        order.append("brain")
        return iter([("answer", "ok.")])
    def fake_speak(chunks, on_start=None):
        return "".join(chunks)  # consume the generator → drives the brain
    def pub(ch, ev):
        published.append((ch, ev))
        order.append(ch)
    agent.handle_utterance("ace what is utah", tell_stream=fake_tell,
                           speak_stream=fake_speak, publish=pub)
    assert any(ch == "wake" for ch, _ in published)   # orb gets its pulse
    assert order[0] == "wake"                          # and BEFORE the brain runs


def test_bare_wake_still_pulses_the_orb():
    published = []
    agent.handle_utterance("ace", tell_stream=_boom, speak_stream=_boom,
                           publish=lambda ch, ev: published.append((ch, ev)))
    assert any(ch == "wake" for ch, _ in published)


def test_bare_wake_does_not_call_brain():
    r = agent.handle_utterance("ace", tell_stream=_boom, speak_stream=_boom,
                               publish=lambda *a: None)
    assert r is not None and r["command"] == "" and not r.get("answer")


def test_empty_answer_is_not_spoken():
    spoken = []
    r = agent.handle_utterance(
        "ace hello",
        tell_stream=lambda c: iter([("source", "brain"), ("answer", "  ")]),
        speak_stream=_collect(spoken), publish=lambda *a: None,
    )
    assert spoken == []  # nothing meaningful to say -> stays silent
    assert r["answer"] == ""


def test_handler_never_raises_if_speak_or_publish_fail():
    # a TTS/publish hiccup must not crash the voice loop
    def boom_speak(chunks, on_start=None):
        list(chunks)                              # drain the brain (like the real pipeline)
        raise RuntimeError("audio device gone")
    r = agent.handle_utterance(
        "ace hi",
        tell_stream=lambda c: iter([("answer", "hey")]),
        speak_stream=boom_speak,
        publish=lambda *a: (_ for _ in ()).throw(RuntimeError("bus down")),
    )
    assert r["answer"] == "hey"  # turn still completes (answer captured from the stream)
