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
from utah.voice import tts, wake

log = logging.getLogger("utah.voice.agent")


def _default_publish(channel: str, event: dict) -> None:
    """Publish the voice turn onto the daemon bus so the deck can render it."""
    from utah.daemon import client as ctl

    ctl.call_sync("publish", {"channel": channel, "event": event}, timeout=3.0)


def handle_utterance(transcript, *, tell_stream=None, speak_stream=None,
                     publish=None, on_speaking=None) -> dict | None:
    """Handle one heard utterance. Returns ``None`` if the wake word is absent
    (utterance ignored). Otherwise runs the brain, speaks the answer SENTENCE BY
    SENTENCE as it streams (low latency — the first sentence plays while the brain
    is still generating the rest), publishes the turn, and returns
    ``{wake, command, answer, source}``.

    ``on_speaking`` (optional) fires once, the instant the first sentence is sent
    to the speaker, so the loop can flip the deck state thinking → speaking exactly
    when audio begins (not before)."""
    tell_stream = tell_stream or core.tell_stream
    speak_stream = speak_stream or tts.speak_stream
    publish = publish or _default_publish

    command = wake.extract_command(transcript)
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

    # Tee the brain stream: capture the source + full answer for the return value /
    # deck publish, while yielding ONLY answer text on to the sentence-pipelined
    # speaker. The brain turn runs lazily inside this generator (so the wake pulse
    # above is published first) and a failing turn is logged, never raised.
    parts: list[str] = []
    captured = {"source": "brain"}

    def _answer_chunks():
        try:
            for channel, chunk in tell_stream(command):
                if channel == "answer":
                    parts.append(chunk)
                    yield chunk
                elif channel == "source":
                    captured["source"] = chunk
        except Exception as exc:  # noqa: BLE001 — a turn failing must not crash the loop
            log.warning("voice brain turn failed: %s", exc)

    try:
        speak_stream(_answer_chunks(), on_start=on_speaking)
    except Exception as exc:  # noqa: BLE001 — speech is best-effort, never fatal
        log.warning("voice speak failed: %s", exc)

    answer = "".join(parts).strip()
    try:
        publish("voice", {"q": command, "answer": answer, "source": captured["source"]})
    except Exception as exc:  # noqa: BLE001
        log.warning("voice publish failed: %s", exc)

    return {"wake": True, "command": command, "answer": answer, "source": captured["source"]}


__all__ = ["handle_utterance"]
