"""STT worker subprocess — keeps the (Metal) Whisper model warm and transcribes
wav paths fed on stdin, writing ``{"text": ...}`` JSON lines to stdout.

Why a separate process: MLX Whisper runs on the Metal GPU, which can DEADLOCK
under contention. In-process that hangs the mic loop's main thread forever (it
holds the echo-mute while transcribing) → the mic goes permanently deaf. Isolated
here, a hang is recovered by the parent simply KILLING this process — the loop
never blocks past its timeout, and a fresh worker has clean Metal state. The model
stays loaded across turns, so steady-state latency matches in-process.

Protocol (newline-delimited, one per line):
  worker → ``{"ready": true}``      once the model is loaded
  parent → ``<wav_path>``           a file to transcribe
  worker → ``{"text": "..."}``      the transcript ("" on any failure)
"""
from __future__ import annotations

import json
import sys


def _engine():
    # Run the REAL model in-process here; the parent's SubprocessSTT is what makes
    # it killable. Never SubprocessSTT (that would recurse).
    from utah import config
    from utah.voice import stt

    return stt.MoonshineSTT() if config.STT_ENGINE == "moonshine" else stt.MLXWhisperSTT()


def main() -> int:
    try:
        engine = _engine()
        # Compile/load now (MLX compiles on first transcribe) so the parent's boot
        # wait covers it and the first real turn is warm.
        engine.transcribe  # noqa: B018 - attribute touch keeps engine referenced
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"ready": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
        return 1
    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            text = engine.transcribe(path)
        except Exception:  # noqa: BLE001 - never crash the worker on one bad clip
            text = ""
        sys.stdout.write(json.dumps({"text": text}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
