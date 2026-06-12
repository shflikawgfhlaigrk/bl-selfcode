"""STT worker subprocess — keeps the (Metal) Whisper model warm and transcribes
wav paths fed on stdin, writing ``{"text": ...}`` JSON lines to stdout.

Why a separate process: MLX Whisper runs on the Metal GPU, which can DEADLOCK
under contention. In-process that hangs the mic loop's main thread forever (it
holds the echo-mute while transcribing) → the mic goes permanently deaf. Isolated
here, a hang is recovered by the parent simply KILLING this process — the loop
never blocks past its timeout, and a fresh worker has clean Metal state. The model
stays loaded across turns, so steady-state latency matches in-process.

Protocol (newline-delimited, one per line):
  worker → ``{"ready": true}``      once the model is loaded AND warm
  parent → ``<wav_path>``           a file to transcribe
  worker → ``{"text": "..."}``      the transcript ("" on any failure)

"Ready" is honest: it is emitted only after a REAL warm-up transcribe of a tiny
silent clip. MLX loads/compiles on the *first transcribe call*, so a bare engine
construction proves nothing — a worker that claimed ready cold pushed the multi-
second Metal compile into the first live turn, which the parent then killed at
``STT_HANG_TIMEOUT_S`` and respawned, cold again, forever. The warm-up runs inside
the parent's boot budget (``STT_WORKER_BOOT_S``), where that cost belongs. An
engine that cannot transcribe even silence is dead → ``{"ready": false}`` + rc 1,
never a fake ready that fails every turn after.

``main`` takes injectable ``stdin``/``stdout``/``make_engine`` seams so the REAL
protocol loop is testable without loading a model (tests fake only the engine).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import wave
from typing import IO, Callable

#: Warm-up clip: 0.5 s of 16 kHz mono silence — long enough that every engine
#: (Moonshine / MLX Whisper) accepts it, short enough to add nothing to boot.
_WARMUP_SAMPLES = 8_000
_WARMUP_RATE = 16_000


def _engine():
    # Run the REAL model in-process here; the parent's SubprocessSTT is what makes
    # it killable. Never SubprocessSTT (that would recurse: workers spawning workers).
    from utah import config
    from utah.voice import stt

    return stt.MoonshineSTT() if config.STT_ENGINE == "moonshine" else stt.MLXWhisperSTT()


def _write_silent_wav(path: str) -> str:
    """Write the tiny silent warm-up WAV (16 kHz / mono / int16) and return *path*."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_WARMUP_RATE)
        wf.writeframes(b"\x00\x00" * _WARMUP_SAMPLES)
    return path


def _warm_up(engine) -> None:
    """Force the model load + first-transcribe compile NOW, inside the boot budget.
    Raises if the engine cannot transcribe even silence (it is dead — be honest)."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="utah_stt_warm_")
    os.close(fd)
    try:
        _write_silent_wav(path)
        engine.transcribe(path)  # output is irrelevant; the load/compile is the point
    finally:
        try:
            os.remove(path)
        except OSError:
            pass  # best-effort temp cleanup; never mask a warm-up verdict


def main(stdin: IO[str] | None = None, stdout: IO[str] | None = None,
         make_engine: Callable[[], object] | None = None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    make_engine = make_engine or _engine

    def emit(obj: dict) -> None:
        stdout.write(json.dumps(obj) + "\n")
        stdout.flush()  # line-buffered protocol: the parent select()s per line

    try:
        engine = make_engine()
        _warm_up(engine)
    except Exception as exc:  # noqa: BLE001 — any boot failure → honest not-ready
        emit({"ready": False, "error": str(exc)})
        return 1
    emit({"ready": True})

    for line in stdin:
        path = line.strip()
        if not path:
            continue
        try:
            text = engine.transcribe(path)
        except Exception:  # noqa: BLE001 — never crash the worker on one bad clip
            text = ""
        emit({"text": text})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
