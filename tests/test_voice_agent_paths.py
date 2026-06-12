"""The voice agent's failure-path contracts that test_voice_agent[_hardening]
left uncovered: tell() RAISING (not just empty) falls back to the stream; an
UNAVAILABLE instant answer is never spoken as-is but is the last-resort text when
the stream also produces nothing; a stream that dies mid-generation still
completes the turn; the selfcode ack survives a dead speaker; and the spoken-form
sentence cap sits exactly at the documented boundary."""
from __future__ import annotations

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


def test_tell_raising_falls_back_to_stream():
    """A crashed tell() must not kill the turn — the stream is the fallback brain."""
    spoken = []

    def boom_tell(cmd):
        raise RuntimeError("recall layer down")

    def fake_stream(cmd, want_thinking=True, voice=False):
        return iter([("source", "brain"), ("answer", "Recovered answer.")])

    r = agent.handle_utterance(
        "ace what is utah", tell=boom_tell, tell_stream=fake_stream,
        speak_stream=_speak(spoken), publish=lambda *a: None,
    )
    assert r["answer"] == "Recovered answer."
    assert spoken == ["Recovered answer."]


def test_unavailable_instant_is_not_spoken_directly():
    """source=UNAVAILABLE means the brain lane was down — that text is an apology,
    not an answer; the stream must run instead."""
    spoken = []
    streamed = []

    def fake_tell(cmd):
        return Reply(text="Brain unavailable right now.", source=ReplySource.UNAVAILABLE)

    def fake_stream(cmd, want_thinking=True, voice=False):
        streamed.append(cmd)
        return iter([("source", "brain"), ("answer", "Live answer.")])

    r = agent.handle_utterance(
        "ace what is utah", tell=fake_tell, tell_stream=fake_stream,
        speak_stream=_speak(spoken), publish=lambda *a: None,
    )
    assert streamed == ["what is utah"]
    assert r["answer"] == "Live answer."


def test_unavailable_text_is_last_resort_when_stream_is_empty():
    """If the stream ALSO yields nothing, the instant text (even unavailable) beats
    a silent dead turn — and its source is reported honestly."""
    def fake_tell(cmd):
        return Reply(text="The brain lane is down.", source=ReplySource.UNAVAILABLE)

    r = agent.handle_utterance(
        "ace what is utah", tell=fake_tell,
        tell_stream=lambda c, want_thinking=True, voice=False: iter([]),
        speak_stream=_speak([]), publish=lambda *a: None,
    )
    assert r["answer"] == "The brain lane is down."
    assert r["source"] == ReplySource.UNAVAILABLE.value


def test_stream_dying_mid_generation_still_completes_turn():
    """A generator that raises after the first chunk: the spoken/published answer is
    the partial text, and handle_utterance never raises."""
    published = []

    def dying_stream(cmd, want_thinking=True, voice=False):
        yield ("answer", "Partial ")
        raise RuntimeError("brain pipe broke")

    r = agent.handle_utterance(
        "ace tell me", tell=lambda c: Reply(text="", source=ReplySource.BRAIN),
        tell_stream=dying_stream, speak_stream=_speak([]),
        publish=lambda ch, ev: published.append((ch, ev)),
    )
    assert r["answer"] == "Partial"
    assert any(ch == "voice" and ev.get("answer") == "Partial" for ch, ev in published)


def test_selfcode_ack_survives_dead_speaker_and_publishes():
    """TTS down during a selfcode ack: the job still dispatched, the turn still
    publishes, the handler never raises."""
    published = []

    def dead_speak(chunks, on_start=None):
        list(chunks)
        raise RuntimeError("audio device gone")

    r = agent.handle_utterance(
        "ace code fix the gate", dispatch=lambda task: "job-9",
        speak_stream=dead_speak,
        publish=lambda ch, ev: published.append((ch, ev)),
    )
    assert r["job"] == "job-9" and r["source"] == "selfcode"
    assert any(ch == "voice" and ev.get("source") == "selfcode" for ch, ev in published)


def test_speakable_cap_boundary_exact():
    """Exactly _VOICE_MAX_SENTENCES passes whole; one more gets capped + the offer."""
    three = "One. Two. Three."
    four = "One. Two. Three. Four."
    assert agent._speakable(three) == three
    capped = agent._speakable(four)
    assert capped.endswith("Want the rest?")
    assert "Four" not in capped


def test_default_dispatch_is_used_for_code_commands(monkeypatch):
    """No dispatch injected → the module's HTTP dispatcher runs (and its failure is
    reported honestly as the no-job ack)."""
    spoken = []
    monkeypatch.setattr(agent, "_selfcode_dispatch", lambda task: None)
    r = agent.handle_utterance(
        "ace code anything", speak_stream=_speak(spoken), publish=lambda *a: None,
    )
    assert r["job"] is None
    assert spoken and "couldn't" in spoken[0].lower()
