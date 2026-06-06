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


def handle_utterance(transcript, *, tell_stream=None, speak=None, publish=None) -> dict | None:
    """Handle one heard utterance. Returns ``None`` if the wake word is absent
    (utterance ignored). Otherwise runs the brain, speaks, publishes, and returns
    ``{wake, command, answer, source}``."""
    tell_stream = tell_stream or core.tell_stream
    speak = speak or tts.speak
    publish = publish or _default_publish

    command = wake.extract_command(transcript)
    if command is None:
        return None  # not addressed to Utah
    if not command:
        return {"wake": True, "command": "", "answer": ""}  # bare "ace"

    parts: list[str] = []
    source = "brain"
    try:
        for channel, chunk in tell_stream(command):
            if channel == "answer":
                parts.append(chunk)
            elif channel == "source":
                source = chunk
    except Exception as exc:  # noqa: BLE001 — a turn failing must not crash the loop
        log.warning("voice brain turn failed: %s", exc)

    answer = "".join(parts).strip()
    if answer:
        try:
            speak(answer)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice speak failed: %s", exc)
    try:
        publish("voice", {"q": command, "answer": answer, "source": source})
    except Exception as exc:  # noqa: BLE001
        log.warning("voice publish failed: %s", exc)

    return {"wake": True, "command": command, "answer": answer, "source": source}


__all__ = ["handle_utterance"]
