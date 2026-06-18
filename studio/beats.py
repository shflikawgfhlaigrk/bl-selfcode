#!/usr/bin/env python3
"""beats.py — detect beats/onsets in audio, locally (numpy only, no librosa).

Replicates what Apple's Beat Detection does for Final Cut: find the rhythmic hits
in a track so cuts and caption-pops can land ON the beat. Spectral-flux onset
detection + adaptive peak picking, plus an autocorrelation tempo estimate.

  python3 beats.py --audio bed.mp3 --out cues/beats.json
  # → {"beats":[0.51, 0.98, ...], "bpm": 122.0}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 22050
HOP = 512
WIN = 1024


def load_audio(src: Path) -> np.ndarray:
    wav = Path(tempfile.mkdtemp()) / "a.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", "-y", str(wav)],
                   check=True)
    with wave.open(str(wav), "rb") as w:
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return x


def onset_envelope(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - WIN) // HOP if len(x) >= WIN else 0
    if n <= 1:
        return np.zeros(1)
    window = np.hanning(WIN)
    mags = np.empty((n, WIN // 2 + 1), dtype=np.float32)
    for i in range(n):
        frame = x[i * HOP: i * HOP + WIN] * window
        mags[i] = np.abs(np.fft.rfft(frame))
    flux = np.diff(mags, axis=0)
    flux[flux < 0] = 0
    env = flux.sum(axis=1)
    env = np.concatenate([[0.0], env])
    return env / (env.max() + 1e-9)


def pick_peaks(env: np.ndarray, min_gap_s: float = 0.28) -> list[float]:
    min_gap = int(min_gap_s * SR / HOP)
    # adaptive threshold: local mean + margin
    k = 16
    pad = np.pad(env, (k, k), mode="edge")
    local_mean = np.array([pad[i:i + 2 * k + 1].mean() for i in range(len(env))])
    thr = local_mean + 0.10
    beats, last = [], -min_gap
    for i in range(1, len(env) - 1):
        if env[i] > thr[i] and env[i] >= env[i - 1] and env[i] > env[i + 1] and i - last >= min_gap:
            beats.append(i * HOP / SR)
            last = i
    return beats


def estimate_bpm(env: np.ndarray) -> float:
    if len(env) < 4:
        return 0.0
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lo = int(60 / 200 * SR / HOP)   # 200 BPM
    hi = int(60 / 50 * SR / HOP)    # 50 BPM
    hi = min(hi, len(ac) - 1)
    if hi <= lo:
        return 0.0
    lag = lo + int(np.argmax(ac[lo:hi]))
    return round(60.0 / (lag * HOP / SR), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bpm", type=float, help="skip detection, use a fixed grid")
    ap.add_argument("--dur", type=float, default=60.0, help="grid length when --bpm")
    a = ap.parse_args()
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    if a.bpm:
        step = 60.0 / a.bpm
        beats = list(np.arange(0, a.dur, step))
        bpm = a.bpm
    else:
        x = load_audio(Path(a.audio))
        env = onset_envelope(x)
        beats = pick_peaks(env)
        bpm = estimate_bpm(env)
    out.write_text(json.dumps({"beats": [round(float(b), 3) for b in beats], "bpm": bpm}))
    print(f"{len(beats)} beats, ~{bpm} BPM → {out}")


if __name__ == "__main__":
    main()
