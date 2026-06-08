"""Voice turn handler. A transcript goes through the wake gate, then the SAME
brain pipeline as the chat box (``core.tell_stream`` — recall+ground+reason+
remember), the answer is spoken (Piper), and the turn is published so it shows in
the deck chat box. Voice and chat are the same brain; only the I/O differs.

Pure orchestration with injectable deps (tell_stream/speak/publish) — fully unit
tested. Speak/publish failures never crash the loop.
"""
from __future__ import annotations

import logging

from utah import core
from utah.objects import ReplySource
from utah.voice import tts, wake

log = logging.getLogger("utah.voice.agent")

#: Voice speaks these via one-shot :func:`core.tell` — no stream generator, no recall
#: before capability, time-to-first-audio = Piper only. Brain/learned still stream.
_VOICE_INSTANT = frozenset({
    ReplySource.SOCIAL,
    ReplySource.CAPABILITY,
    ReplySource.MEMORY,
    ReplySource.LOCAL,
    ReplySource.UNAVAILABLE,
})


def _default_publish(channel: str, event: dict) -> None:
    """Publish the voice turn onto the daemon bus so the deck can render it."""
    from utah.daemon import client as ctl

    ctl.call_sync("publish", {"channel": channel, "event": event}, timeout=3.0)


def pulse_wake(command: str = "", *, publish=None) -> None:
    """Deck orb pulse the instant audio wake fires — before the slow STT/brain path."""
    publish = publish or _default_publish
    try:
        publish("wake", {"command": command})
    except Exception as exc:  # noqa: BLE001
        log.warning("voice wake publish failed: %s", exc)


def handle_utterance(transcript, *, audio_wake: bool = False, tell=None, tell_stream=None,
                     speak_stream=None, publish=None, on_speaking=None) -> dict | None:
    """Handle one heard utterance. Returns ``None`` if the wake word is absent
    (utterance ignored). Otherwise runs the brain, speaks the answer SENTENCE BY
    SENTENCE as it streams (low latency — the first sentence plays while the brain
    is still generating the rest), publishes the turn, and returns
    ``{wake, command, answer, source}``.

    ``audio_wake=True`` means openWakeWord armed this segment (Stage A); the
    transcript gate (Stage B) may accept the STT text even when Moonshine dropped
    the short "ace" syllable.

    ``on_speaking`` (optional) fires once, the instant the first sentence is sent
    to the speaker, so the loop can flip the deck state thinking → speaking exactly
    when audio begins (not before)."""
    tell_stream = tell_stream or core.tell_stream
    speak_stream = speak_stream or tts.speak_stream
    publish = publish or _default_publish

    command = wake.resolve_command(transcript, audio_wake=audio_wake)
    if command is None:
        return None  # not addressed to Utah

    # Wake fired — pulse the deck orb IMMEDIATELY, before the (slow) brain turn, so
    # Michael sees Utah heard "ace" at once (the wave the old Ace orb emitted), not
    # 14s later when the answer lands. Never let a publish hiccup crash the loop.
    try:
        publish("wake", {"command": command})
    except Exception as exc:  # noqa: BLE001
        log.warning("voice wake publish failed: %s", exc)

    if not command:
        return {"wake": True, "command": "", "answer": ""}  # bare "ace"

    tell_fn = tell or core.tell
    try:
        instant = tell_fn(command)
    except Exception as exc:  # noqa: BLE001
        log.warning("voice tell failed: %s", exc)
        instant = None

    parts: list[str] = []
    captured = {"source": "brain"}

    if instant is not None and instant.source in _VOICE_INSTANT:
        # Fast path — weather/time/social/memory/local: one Piper synth, no stream overhead.
        answer = (instant.text or "").strip()
        captured["source"] = instant.source.value
        if answer:
            try:
                speak_stream(iter([answer]), on_start=on_speaking)
            except Exception as exc:  # noqa: BLE001
                log.warning("voice speak failed: %s", exc)
    else:
        # Slow path — brain / learned: stream answer chunks for low time-to-first-audio.
        def _answer_chunks():
            try:
                for channel, chunk in tell_stream(command, want_thinking=False, voice=True):
                    if channel == "answer":
                        parts.append(chunk)
                        yield chunk
                    elif channel == "source":
                        captured["source"] = chunk
            except Exception as exc:  # noqa: BLE001
                log.warning("voice brain turn failed: %s", exc)

        try:
            speak_stream(_answer_chunks(), on_start=on_speaking)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice speak failed: %s", exc)
        answer = "".join(parts).strip()
        if instant is not None and instant.text and not answer:
            answer = instant.text.strip()
            captured["source"] = instant.source.value
    try:
        publish("voice", {"q": command, "answer": answer, "source": captured["source"]})
    except Exception as exc:  # noqa: BLE001
        log.warning("voice publish failed: %s", exc)

    return {"wake": True, "command": command, "answer": answer, "source": captured["source"]}


__all__ = ["handle_utterance", "pulse_wake"]
