"""Voice turn handler. A transcript goes through the wake gate, then the SAME
brain pipeline as the chat box (``core.tell_stream`` — recall+ground+reason+
remember), the answer is spoken (Piper), and the turn is published so it shows in
the deck chat box. Voice and chat are the same brain; only the I/O differs.

Pure orchestration with injectable deps (tell_stream/speak/publish) — fully unit
tested. Speak/publish failures never crash the loop.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from utah import core
from utah.objects import ReplySource
from utah.voice import tts, wake

#: Bare-wake ack cooldown — a real transcribed "ace" earns ONE "Yeah?"; repeats inside
#: the window stay silent (the 2026-06-10 self-ack loop: 689 wakes in 15 min). 0 keeps
#: every deliberate bare wake audible; raise it if the ack ever chatters again.
_ACK_COOLDOWN_S = float(os.environ.get("UTAH_VOICE_ACK_COOLDOWN_S", "12"))
_last_ack_at = 0.0
#: Guards the check-then-set on ``_last_ack_at``: STT thrash can hand two identical
#: bare wakes to two threads at once; without the lock both clear the cooldown check
#: and Ace says "Yeah?" twice.
_ack_lock = threading.Lock()

#: Where the deck web layer (the /code console lane) lives. Deployment config, not a
#: constant buried in the function body — override with UTAH_VOICE_CONSOLE_URL.
_CONSOLE_URL = os.environ.get("UTAH_VOICE_CONSOLE_URL", "http://127.0.0.1:8766").rstrip("/")

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


def _default_publish(channel: str, event: dict) -> None:
    """Publish the voice turn onto the daemon bus so the deck can render it."""
    from utah.daemon import client as ctl

    ctl.call_sync("publish", {"channel": channel, "event": event}, timeout=3.0)


def _speak_safe(speak_stream, chunks, on_speaking) -> None:
    """Speak, swallowing speaker failures — a dead audio device must not kill the
    turn (the answer still publishes to the deck)."""
    try:
        speak_stream(chunks, on_start=on_speaking)
    except Exception as exc:  # noqa: BLE001
        log.warning("voice speak failed: %s", exc)


def _publish_safe(publish, channel: str, event: dict) -> None:
    """Publish, swallowing bus failures — a dead daemon bus must not kill the turn."""
    try:
        publish(channel, event)
    except Exception as exc:  # noqa: BLE001
        log.warning("voice publish failed (%s): %s", channel, exc)


def pulse_wake(command: str = "", *, publish=None) -> None:
    """Deck orb pulse the instant audio wake fires — before the slow STT/brain path."""
    _publish_safe(publish or _default_publish, "wake", {"command": command})


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


def _console_post(url: str, data: bytes) -> str:
    """Default HTTP poster for the deck console — bounded, JSON body."""
    import urllib.request

    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def _selfcode_dispatch(task: str, *, http_post=None) -> str | None:
    """POST the task into the deck console's /code lane (the web process owns the
    job runner + live transcript). Returns the job id or None. Never raises."""
    import json as _json

    try:
        post = http_post or _console_post
        raw = post(_CONSOLE_URL + "/api/console",
                   _json.dumps({"line": "/code " + task}).encode())
        return (_json.loads(raw) or {}).get("job")
    except Exception as exc:  # noqa: BLE001 — voice must never crash on a dead web layer
        log.warning("voice selfcode dispatch failed: %s", exc)
        return None


def _ack_bare_wake(transcript, speak_stream, publish, on_speaking) -> dict:
    """A wake with no command. Ack ONLY a deliberate, TRANSCRIBED bare "ace" — and
    at most once per cooldown. An audio wake with an EMPTY transcript is noise/echo,
    not Michael: live 2026-06-10, Ace's own "Yeah?" re-triggered the wake model every
    ~25s (689 wake events in 15 min) — empty MUST stay silent or the ack feeds
    itself forever."""
    global _last_ack_at
    if not (transcript or "").strip():
        return {"wake": True, "command": "", "answer": ""}
    with _ack_lock:  # atomic check-then-set — two thrashed wakes must yield ONE ack
        now = time.monotonic()
        if now - _last_ack_at < _ACK_COOLDOWN_S:
            return {"wake": True, "command": "", "answer": ""}
        _last_ack_at = now
    answer = "Yeah?"
    _speak_safe(speak_stream, iter([answer]), on_speaking)
    _publish_safe(publish, "voice", {"q": "", "answer": answer, "source": "wake"})
    return {"wake": True, "command": "", "answer": answer}


def _selfcode_turn(command, task, dispatch, speak_stream, publish, on_speaking) -> dict:
    """voice → coding agent: dispatch a REAL propose-only self-code job and speak an
    HONEST ack — "on it" only when the console accepted the job, never a fake-ok
    when the deck web layer is down."""
    job = (dispatch or _selfcode_dispatch)(task)
    answer = (("On it — coding that now, propose-only on my isolated clone. "
               "Open the Self-Code console to watch me work.") if job else
              "I couldn't reach my coding bay — the deck web layer looks down.")
    _speak_safe(speak_stream, iter([answer]), on_speaking)
    _publish_safe(publish, "voice", {"q": command, "answer": answer, "source": "selfcode"})
    return {"wake": True, "command": command, "answer": answer,
            "source": "selfcode", "job": job}


def handle_utterance(transcript, *, audio_wake: bool = False, wake_confidence=None,
                     button_barge: bool = False,
                     tell=None, tell_stream=None,
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

    command = wake.resolve_command(transcript, audio_wake=audio_wake,
                                   wake_confidence=wake_confidence,
                                   button_barge=button_barge)
    if command is None:
        return None  # not addressed to Utah

    # Wake fired — pulse the deck orb IMMEDIATELY, before the (slow) brain turn, so
    # Michael sees Utah heard "ace" at once (the wave the old Ace orb emitted), not
    # 14s later when the answer lands. Never let a publish hiccup crash the loop.
    _publish_safe(publish, "wake", {"command": command})

    if not command:
        return _ack_bare_wake(transcript, speak_stream, publish, on_speaking)

    # voice → coding agent: "code <task>" / "fix your <x>" runs a REAL propose-only
    # self-code job; Ace acknowledges aloud and the console shows his live transcript.
    task = coding_task(command)
    if task is not None:
        return _selfcode_turn(command, task, dispatch, speak_stream, publish, on_speaking)

    tell_fn = tell or core.tell
    try:
        instant = tell_fn(command)
    except Exception as exc:  # noqa: BLE001
        log.warning("voice tell failed: %s", exc)
        instant = None

    parts: list[str] = []
    captured = {"source": "brain"}

    # LATENCY: tell() already ran the FULL turn (recall → ground → reason). If it
    # produced an answer — for ANY source, including BRAIN/LEARNED — SPEAK THAT ANSWER
    # NOW. The old code threw a non-instant tell() answer away and ran tell_stream(), a
    # SECOND full brain round-trip (measured ~1.4-5s of dead air before the first word).
    # Piper synth of the first sentence of an already-complete answer is ~0.1s, so this
    # is the fastest path to first audio AND one brain call per turn, not two. tell_stream
    # remains the fallback ONLY when tell() came back empty/unavailable (e.g. an empty
    # BRAIN turn). The deck still gets the full answer; only the spoken form is shortened
    # (a recalled list read verbatim is the 85s monologue Michael hit).
    instant_answer = (instant.text or "").strip() if instant is not None else ""
    fast_ok = (instant is not None and instant_answer
               and instant.source is not ReplySource.UNAVAILABLE)

    if fast_ok:
        # One Piper synth of the already-computed answer — no stream, no 2nd brain call.
        answer = instant_answer
        captured["source"] = instant.source.value
        _speak_safe(speak_stream, iter([_speakable(answer)]), on_speaking)
    else:
        # Fallback — tell() gave us nothing usable (empty/unavailable): stream the brain
        # answer chunks so we still get low time-to-first-audio rather than a dead turn.
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

        _speak_safe(speak_stream, _answer_chunks(), on_speaking)
        answer = "".join(parts).strip()
        if instant is not None and instant.text and not answer:
            answer = instant.text.strip()
            captured["source"] = instant.source.value
    _publish_safe(publish, "voice",
                  {"q": command, "answer": answer, "source": captured["source"]})

    return {"wake": True, "command": command, "answer": answer, "source": captured["source"]}


__all__ = ["handle_utterance", "pulse_wake", "coding_task"]
