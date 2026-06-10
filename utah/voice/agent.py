"""Voice turn handler. A transcript goes through the wake gate, then the SAME
brain pipeline as the chat box (``core.tell_stream`` — recall+ground+reason+
remember), the answer is spoken (Piper), and the turn is published so it shows in
the deck chat box. Voice and chat are the same brain; only the I/O differs.

Pure orchestration with injectable deps (tell_stream/speak/publish) — fully unit
tested. Speak/publish failures never crash the loop.
"""
from __future__ import annotations

import logging
import re

from utah import core
from utah.objects import ReplySource
from utah.voice import tts, wake

log = logging.getLogger("utah.voice.agent")

#: A SPOKEN answer caps at this many sentences. A recalled/stored answer can be a
#: chat-length, markdown-formatted list; read aloud verbatim that's a 60-90s garbled
#: monologue (live: "Mark Douglas's 5 rules" → an 85s essay). The deck chat box still
#: gets the FULL answer; only the spoken form is shortened.
_VOICE_MAX_SENTENCES = 3
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _speakable(text: str) -> str:
    """Voice-friendly form of any answer: strip markdown/line breaks and cap to a few
    sentences so long recalled/brain answers don't get read aloud for a minute."""
    flat = re.sub(r"[*#`_>|]+", "", text or "").replace("\n", " ")
    flat = re.sub(r"(?:^|\s)\d{1,2}[.)]\s+", " ", flat)  # strip "1. " / "2) " list markers
    flat = re.sub(r"\s+", " ", flat).strip()
    if not flat:
        return flat
    sentences = _SENTENCE_SPLIT.split(flat)
    if len(sentences) <= _VOICE_MAX_SENTENCES:
        return flat
    return " ".join(sentences[:_VOICE_MAX_SENTENCES]).strip() + " Want the rest?"

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


#: Voice → coding-agent bridge (Michael 2026-06-10: "the voice and the coding agent
#: don't use each other to fix itself"). "ace, code <task>" / "ace, fix your <x>"
#: dispatches a REAL propose-only self-code job via the deck console lane.
_CODE_PREFIXES = ("code ", "self code ", "selfcode ")
_FIX_PREFIXES = ("fix your", "fix the", "improve your", "debug your", "repair your")


def coding_task(command: str) -> str | None:
    """The self-code task a spoken command carries, or None (normal brain turn)."""
    low = (command or "").lower().strip()
    for t in _CODE_PREFIXES:
        if low.startswith(t):
            task = command.strip()[len(t):].strip()
            return task or None
    if low.startswith(_FIX_PREFIXES):
        return command.strip()
    return None


def _selfcode_dispatch(task: str, *, http_post=None) -> str | None:
    """POST the task into the deck console's /code lane (the web process owns the
    job runner + live transcript). Returns the job id or None. Never raises."""
    import json as _json
    import urllib.request

    try:
        if http_post is None:
            def http_post(url, data):
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.read().decode("utf-8", "replace")
        raw = http_post("http://127.0.0.1:8766/api/console",
                        _json.dumps({"line": "/code " + task}).encode())
        return (_json.loads(raw) or {}).get("job")
    except Exception as exc:  # noqa: BLE001 — voice must never crash on a dead web layer
        log.warning("voice selfcode dispatch failed: %s", exc)
        return None


def handle_utterance(transcript, *, audio_wake: bool = False, tell=None, tell_stream=None,
                     speak_stream=None, publish=None, on_speaking=None,
                     dispatch=None) -> dict | None:
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

    # voice → coding agent: "code <task>" / "fix your <x>" runs a REAL propose-only
    # self-code job; Ace acknowledges aloud and the console shows his live transcript.
    task = coding_task(command)
    if task is not None:
        job = (dispatch or _selfcode_dispatch)(task)
        answer = (("On it — coding that now, propose-only on my isolated clone. "
                   "Open the Self-Code console to watch me work.") if job else
                  "I couldn't reach my coding bay — the deck web layer looks down.")
        try:
            speak_stream(iter([answer]), on_start=on_speaking)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice speak failed: %s", exc)
        try:
            publish("voice", {"q": command, "answer": answer, "source": "selfcode"})
        except Exception as exc:  # noqa: BLE001
            log.warning("voice publish failed: %s", exc)
        return {"wake": True, "command": command, "answer": answer,
                "source": "selfcode", "job": job}

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
                # Speak a SHORT form (the deck still gets the full `answer`); a recalled
                # list read aloud verbatim is the 85s monologue Michael hit.
                speak_stream(iter([_speakable(answer)]), on_start=on_speaking)
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


__all__ = ["handle_utterance", "pulse_wake", "coding_task"]
