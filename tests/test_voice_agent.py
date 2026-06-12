"""Voice turn handler: a transcript -> wake gate -> SAME brain pipeline as chat
(core.tell_stream) -> speak the answer SENTENCE BY SENTENCE as it streams (low
latency) -> publish the turn so it shows in the deck chat box. No wake => ignored
(the mic never acts on un-addressed speech)."""
from __future__ import annotations

from utah.objects import Reply, ReplySource
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
        return Reply(text="", source=ReplySource.BRAIN)
    def fake_stream(cmd, want_thinking=True, voice=False):
        assert cmd == "what is utah"
        assert want_thinking is False
        assert voice is True
        return iter([("source", "brain"), ("thinking", "reasoning"),
                     ("answer", "Utah is the rebuild."), ("done", "Utah is the rebuild.")])
    r = agent.handle_utterance(
        "ace what is utah",
        tell=fake_tell, tell_stream=fake_stream, speak_stream=_collect(spoken),
        publish=lambda ch, ev: published.append((ch, ev)),
    )
    assert r["command"] == "what is utah"
    assert r["answer"] == "Utah is the rebuild."
    assert r["source"] == "brain"
    assert spoken == ["Utah is the rebuild."]            # spoke the (streamed) answer
    assert any(ev.get("q") == "what is utah" for ch, ev in published)  # shown on deck


def test_nonempty_brain_answer_is_spoken_without_a_second_brain_call():
    """LATENCY: when tell() already produced a full BRAIN answer, voice must SPEAK that
    answer — not throw it away and run tell_stream(), a SECOND full brain round-trip
    (the measured ~1.4-5s of dead air). The duplicate call is the bug; one brain call
    per turn is the fix. tell_stream must NOT be invoked here."""
    spoken = []
    def fake_tell(cmd):
        return Reply(text="Utah is the clean-room rebuild.", source=ReplySource.BRAIN)
    r = agent.handle_utterance(
        "ace what is utah",
        tell=fake_tell, tell_stream=_boom,            # _boom = a second brain call is forbidden
        speak_stream=_collect(spoken), publish=lambda *a: None,
    )
    assert r["answer"] == "Utah is the clean-room rebuild."
    assert r["source"] == "brain"
    assert spoken == ["Utah is the clean-room rebuild."]   # the tell() answer was spoken


def test_empty_brain_answer_falls_back_to_stream():
    """If tell() comes back EMPTY (no answer yet), voice still streams the brain — the
    stream is the fallback that actually produces the answer."""
    spoken = []
    def fake_tell(cmd):
        return Reply(text="", source=ReplySource.BRAIN)
    def fake_stream(cmd, want_thinking=True, voice=False):
        return iter([("source", "brain"), ("answer", "Streamed answer.")])
    r = agent.handle_utterance(
        "ace what is utah",
        tell=fake_tell, tell_stream=fake_stream,
        speak_stream=_collect(spoken), publish=lambda *a: None,
    )
    assert r["answer"] == "Streamed answer."
    assert spoken == ["Streamed answer."]


def test_instant_capability_skips_tell_stream():
    """Weather/time/social go through tell() only — no stream generator on the hot path."""
    spoken = []
    stream_called = []
    def fake_tell(cmd):
        return Reply(text="Gulf Shores: sunny, 75°F.", source=ReplySource.CAPABILITY)
    def fake_stream(*a, **k):
        stream_called.append(1)
        return iter([])
    r = agent.handle_utterance(
        "ace what's the weather",
        tell=fake_tell, tell_stream=fake_stream, speak_stream=_collect(spoken),
        publish=lambda *a: None,
    )
    assert r["answer"] == "Gulf Shores: sunny, 75°F."
    assert r["source"] == "capability"
    assert spoken == ["Gulf Shores: sunny, 75°F."]
    assert stream_called == []


def test_voice_skips_the_thinking_block_for_latency():
    """Voice never speaks the <thinking> block, so it must tell the brain to skip it —
    otherwise the model generates (and we discard) a whole reasoning pass before the
    first spoken word. handle_utterance must call tell_stream with want_thinking=False."""
    captured = {}
    def fake_tell(cmd):
        return Reply(text="", source=ReplySource.BRAIN)
    def fake_stream(cmd, want_thinking=True, voice=False):
        captured["want_thinking"] = want_thinking
        captured["voice"] = voice
        return iter([("source", "brain"), ("answer", "Hi.")])
    agent.handle_utterance("ace hi there", tell=fake_tell, tell_stream=fake_stream,
                           speak_stream=_collect([]), publish=lambda *a: None)
    assert captured.get("want_thinking") is False
    assert captured.get("voice") is True


def test_only_answer_text_is_spoken_not_thinking():
    """The speaker receives ONLY answer text — the brain's thinking is shown on the
    deck but never read aloud."""
    spoken = []
    def fake_tell(cmd):
        return Reply(text="", source=ReplySource.BRAIN)
    def fake_stream(cmd, want_thinking=True, voice=False):
        return iter([("source", "brain"),
                     ("thinking", "let me reason about this at length"),
                     ("answer", "The answer is 42.")])
    agent.handle_utterance("ace the question", tell=fake_tell, tell_stream=fake_stream,
                           speak_stream=_collect(spoken), publish=lambda *a: None)
    assert spoken == ["The answer is 42."]               # no thinking leaked to TTS


def test_on_speaking_fires_when_audio_begins():
    """on_speaking flips the deck state thinking → speaking exactly when the first
    real sentence is sent to the speaker."""
    fired = []
    def fake_tell(cmd):
        return Reply(text="Hello there.", source=ReplySource.SOCIAL)
    def fake_speak(chunks, on_start=None):
        text = "".join(chunks)
        if on_start and text.strip():
            on_start()
        return text
    agent.handle_utterance("ace hi", tell=fake_tell, speak_stream=fake_speak,
                           publish=lambda *a: None, on_speaking=lambda: fired.append(1))
    assert fired == [1]


def test_wake_publishes_immediate_wake_event_before_brain():
    """The instant the wake word fires, a 'wake' event is published BEFORE the slow
    brain turn — so the deck orb emits its wave immediately and Michael sees it heard
    'ace' (like old Ace), not 14s later when the answer lands."""
    published, order = [], []
    def fake_tell(cmd):
        order.append("tell")
        return Reply(text="", source=ReplySource.BRAIN)
    def fake_stream(cmd, want_thinking=True, voice=False):
        order.append("brain")
        return iter([("answer", "ok.")])
    def fake_speak(chunks, on_start=None):
        return "".join(chunks)  # consume the generator → drives the brain
    def pub(ch, ev):
        published.append((ch, ev))
        order.append(ch)
    agent.handle_utterance("ace what is utah", tell=fake_tell, tell_stream=fake_stream,
                           speak_stream=fake_speak, publish=pub)
    assert any(ch == "wake" for ch, _ in published)   # orb gets its pulse
    assert order[0] == "wake"                          # and BEFORE the brain runs


def test_bare_wake_still_pulses_the_orb():
    published = []
    agent.handle_utterance("ace", tell_stream=_boom, speak_stream=_boom,
                           publish=lambda ch, ev: published.append((ch, ev)))
    assert any(ch == "wake" for ch, _ in published)


def test_bare_wake_does_not_call_brain():
    """Bare TRANSCRIBED 'ace' speaks one local ack ('Yeah?') but NEVER reaches the
    brain — tell_stream stays _boom. Repeats inside the ack cooldown stay SILENT
    (the 2026-06-10 self-ack loop: Ace answered his own echo every ~25s)."""
    agent._last_ack_at = 0.0
    spoken = []
    r = agent.handle_utterance("ace", tell_stream=_boom,
                               speak_stream=_collect(spoken),
                               publish=lambda *a: None)
    assert r is not None and r["command"] == ""
    assert r["answer"] == "Yeah?" and spoken == ["Yeah?"]
    spoken2 = []
    r2 = agent.handle_utterance("ace", tell_stream=_boom,
                                speak_stream=_collect(spoken2),
                                publish=lambda *a: None)
    assert r2["answer"] == "" and spoken2 == []   # cooldown: no chatter


def test_audio_wake_empty_transcript_is_silent():
    """The self-ack loop fix (2026-06-10): openWakeWord fired on Ace's own 'Yeah?',
    Whisper transcribed NOTHING — an empty transcript must never speak, ever."""
    agent._last_ack_at = 0.0
    spoken = []
    r = agent.handle_utterance("", audio_wake=True, tell_stream=_boom,
                               speak_stream=_collect(spoken),
                               publish=lambda *a: None)
    assert spoken == []
    assert r is None or r.get("answer") == ""


def test_empty_answer_is_not_spoken():
    spoken = []
    r = agent.handle_utterance(
        "ace hello",
        tell=lambda c: Reply(text="", source=ReplySource.BRAIN),
        tell_stream=lambda c, want_thinking=True, voice=False: iter(
            [("source", "brain"), ("answer", "  ")]),
        speak_stream=_collect(spoken), publish=lambda *a: None,
    )
    assert spoken == []  # nothing meaningful to say -> stays silent
    assert r["answer"] == ""


def test_audio_wake_accepts_stt_when_text_wake_missing():
    """Stage A armed + Moonshine dropped 'ace' — Stage B still answers (capability fast path)."""
    spoken = []
    def fake_tell(cmd):
        assert cmd == "what's the weather right now buddy?"
        return Reply(text="Clear and mild.", source=ReplySource.CAPABILITY)
    r = agent.handle_utterance(
        "Is what's the weather right now buddy?",
        audio_wake=True,
        tell=fake_tell,
        speak_stream=_collect(spoken),
        publish=lambda *a: None,
    )
    assert r["command"] == "what's the weather right now buddy?"
    assert spoken == ["Clear and mild."]


def test_handler_never_raises_if_speak_or_publish_fail():
    # a TTS/publish hiccup must not crash the voice loop
    def boom_speak(chunks, on_start=None):
        list(chunks)                              # drain the brain (like the real pipeline)
        raise RuntimeError("audio device gone")
    r = agent.handle_utterance(
        "ace hi",
        tell=lambda c: Reply(text="hey", source=ReplySource.BRAIN),
        tell_stream=lambda c, want_thinking=True, voice=False: iter([("answer", "hey")]),
        speak_stream=boom_speak,
        publish=lambda *a: (_ for _ in ()).throw(RuntimeError("bus down")),
    )
    assert r["answer"] == "hey"  # turn still completes (answer captured from the stream)


def test_speakable_shortens_and_flattens_a_long_list_answer():
    """A recalled chat-formatted list must become a short, plain SPOKEN form — not an
    85s monologue with markdown and '1.'/'2.' read aloud (the live regression)."""
    from utah.voice.agent import _speakable

    long_ans = ("Mark Douglas's rules (from Trading in the Zone):\n\n"
                "1. Anything can happen.\n2. You don't need to know what's next.\n"
                "3. Wins and losses are random.\n4. An edge is a probability.\n"
                "5. Every moment is unique.")
    spoken = _speakable(long_ans)
    assert "\n" not in spoken
    assert "1." not in spoken and "2." not in spoken      # list markers stripped
    assert spoken.endswith("Want the rest?")              # capped + offers continuation
    assert len(spoken.split()) < len(long_ans.split())    # genuinely shorter

    short = "Gulf Shores, Alabama."
    assert _speakable(short) == short                     # short answers pass through
