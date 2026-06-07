"""TTS boundary — speak text aloud (Piper). Injectable; degrades (logs, no-op) if
the engine/model/audio device is missing so the voice loop never crashes. F5-TTS/
StyleTTS2 (natural voice) drop into this same boundary later.

``speak`` synthesizes the PROVEN-clean WAV (the same ``synthesize_wav`` bytes the
round-trip proof checks) and plays it through the macOS reference player (``afplay``).
The old ``sd.play(int16_array)`` path was the static the live test surfaced: a
module-global stream that's fragile on dtype, on output-device selection (it followed
the PortAudio default, not the system output), and — fatally — on concurrency, since
voice and the chat-speak thread share that one stream and corrupt each other. afplay
fed a clean WAV, under a global lock, removes all three failure modes at once.

LATENCY — ``speak_stream`` is the low-latency path. The old loop buffered the WHOLE
answer, synthesized it in one Piper call, then played: time-to-first-audio = synth(
ENTIRE answer). ``speak_stream`` is a 3-stage pipeline — split → synth → play — on two
background threads, so the FIRST sentence starts playing as soon as it is formed while
later sentences are still being generated/synthesized. Time-to-first-audio drops to
synth(first sentence). ``speak`` is now just ``speak_stream`` over a one-item stream,
so chat answers get the same pipelining for free.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import queue
import subprocess
import tempfile
import threading
import wave
from typing import Callable, Iterable

from utah import config
from utah.daemon import runtime

log = logging.getLogger("utah.voice.tts")

# Serialize ALL playback. A threading.Lock alone only covers ONE process — but the
# voice loop and the web server are SEPARATE processes, each with its own lock, so two
# players ran at the same time = overlapping ("multiple") voices. So we ALSO take an
# flock on a shared file: one voice at a time, machine-wide, across every process.
_PLAY_LOCK = threading.Lock()
_PLAY_LOCKFILE = str(runtime.RUN_DIR / "tts-play.lock")


@contextlib.contextmanager
def _system_play_lock():
    """Exclusive cross-process playback lock (flock). Blocks until no other process is
    playing, so the voice loop and chat-speak never overlap into garble."""
    runtime.RUN_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(_PLAY_LOCKFILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

#: Closing punctuation that may trail a sentence terminator (".", "!", "?").
_CLOSERS = "\"')]}»”’"


def _drain_sentences(buf: str) -> tuple[list[str], str]:
    """Split *buf* into complete sentences + the trailing remainder.

    A sentence ends at ``. ! ?`` (plus any closing quote/bracket) FOLLOWED by
    whitespace, or at a newline. A terminator at the very END of the buffer is
    LEFT in the remainder — it may be mid-token (``3.14``) or the sentence may
    continue in the next chunk — so it is only spoken once known-complete (or on
    the final flush). This is what lets the first sentence go to synth the instant
    it closes, without ever clipping a number or an abbreviation across chunks."""
    out: list[str] = []
    start = i = 0
    n = len(buf)
    while i < n:
        ch = buf[i]
        if ch in ".!?":
            j = i + 1
            while j < n and buf[j] in _CLOSERS:
                j += 1
            if j < n and buf[j].isspace():   # terminator + space → real boundary
                out.append(buf[start:j])
                start = i = j
                continue
        elif ch == "\n":                     # a line break is a speakable boundary
            out.append(buf[start:i])
            start = i = i + 1
            continue
        i += 1
    sentences = [s.strip() for s in out if s.strip()]
    return sentences, buf[start:]


def _afplay(path: str) -> None:
    """Play a WAV via the macOS reference player — honours the system default output
    device and handles the sample rate itself (no dtype/rate fragility)."""
    subprocess.run(["afplay", path], check=True)


class TTS:
    def speak(self, text: str) -> None: ...  # pragma: no cover
    def speak_stream(self, chunks, on_start=None) -> str: ...  # pragma: no cover
    def synth_wav(self, text: str, path: str) -> None: ...  # pragma: no cover


class PiperTTS:
    """Piper voice (lazy-loaded). ``synth_wav`` writes a WAV; ``speak`` synthesizes
    that same clean WAV and plays it via ``player`` (default: macOS ``afplay``).
    ``player`` is injectable so tests prove the wiring without blasting audio."""

    def __init__(self, model_path: str | None = None, player=None) -> None:
        self._model_path = model_path or config.PIPER_MODEL
        self._voice = None
        self._lock = threading.Lock()
        self._player = player or _afplay

    def _load(self):
        with self._lock:
            if self._voice is None:
                from piper import PiperVoice

                self._voice = PiperVoice.load(self._model_path, self._model_path + ".json")
            return self._voice

    def synth_wav(self, text: str, path: str) -> None:
        voice = self._load()
        with wave.open(path, "wb") as wf:
            voice.synthesize_wav(text, wf)

    def speak_stream(self, chunks: Iterable[str], on_start: Callable[[], None] | None = None) -> str:
        """Speak an INCREMENTAL text stream with sentence-level pipelining.

        Three stages on two background threads — split (this thread) → synth →
        play — so the first sentence reaches the speaker as soon as it closes
        while later text is still being generated. Ordering is preserved (both
        queues are FIFO). BLOCKS until every sentence has finished playing — the
        voice loop relies on this to keep the mic muted through playback (no
        self-capture). ``on_start`` fires once, just before the first sentence is
        queued, so the caller can flip UI/voice state to "speaking". Returns the
        full spoken text. Synth/play failures are logged, never raised (one bad
        clip must not crash the voice loop)."""
        sent_q: "queue.Queue[str | None]" = queue.Queue()
        play_q: "queue.Queue[str | None]" = queue.Queue()
        spoken: list[str] = []

        def _synth_loop() -> None:
            while True:
                sentence = sent_q.get()
                if sentence is None:
                    play_q.put(None)          # FIFO: lands after every WAV path
                    return
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="utah_tts_")
                os.close(fd)
                try:
                    self.synth_wav(sentence, path)   # PROVEN-clean bytes
                except Exception as exc:  # noqa: BLE001 — skip a bad clip, keep speaking
                    log.warning("TTS synth failed (%r): %s", sentence[:40], exc)
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                play_q.put(path)

        def _play_loop() -> None:
            while True:
                path = play_q.get()
                if path is None:
                    return
                try:
                    with _PLAY_LOCK, _system_play_lock():   # one voice at a time, machine-wide
                        self._player(path)    # macOS reference player
                except Exception as exc:  # noqa: BLE001
                    log.warning("TTS playback failed: %s", exc)
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        synth_t = threading.Thread(target=_synth_loop, daemon=True)
        play_t = threading.Thread(target=_play_loop, daemon=True)
        synth_t.start()
        play_t.start()

        def _flush(sentences: list[str]) -> None:
            nonlocal on_start
            for s in sentences:
                if on_start is not None:
                    try:
                        on_start()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("TTS on_start hook failed: %s", exc)
                    on_start = None           # fire once, at the first real sentence
                spoken.append(s)
                sent_q.put(s)

        buf = ""
        try:
            for chunk in chunks:
                buf += chunk or ""
                sentences, buf = _drain_sentences(buf)
                _flush(sentences)
            tail = buf.strip()
            if tail:
                _flush([tail])
        finally:
            sent_q.put(None)                  # close the pipeline; join both stages
            synth_t.join()
            play_t.join()
        return " ".join(spoken)

    def speak(self, text: str) -> None:
        """Speak one whole string. Thin wrapper over :meth:`speak_stream` so a
        single-shot caller (chat reply) gets the same sentence pipelining."""
        text = (text or "").strip()
        if not text:
            return
        self.speak_stream([text])


_tts: TTS | None = None


def get_tts() -> TTS:
    global _tts
    if _tts is None:
        _tts = PiperTTS()
    return _tts


def set_tts(tts: TTS | None) -> None:
    global _tts
    _tts = tts


def speak(text: str) -> None:
    """Speak text aloud; never raises (logs + no-ops on any failure)."""
    try:
        get_tts().speak(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS speak failed: %s", exc)


def speak_stream(chunks: Iterable[str], on_start: Callable[[], None] | None = None) -> str:
    """Speak an incremental text stream with sentence pipelining; never raises.

    Returns the full spoken text ("" on any failure, logged). This is the
    low-latency voice path — the first sentence plays while the rest streams in."""
    try:
        return get_tts().speak_stream(chunks, on_start=on_start)
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS speak_stream failed: %s", exc)
        return ""


__all__ = ["TTS", "PiperTTS", "get_tts", "set_tts", "speak", "speak_stream"]
