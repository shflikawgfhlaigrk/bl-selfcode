#!/usr/bin/env python3
"""narration_sync.py — turn your spoken narration into timed cues, locally.

Runs whisper-cli (whisper.cpp, on-device, free) over a clip's audio to get
word-level timestamps, then produces:
  • caption cues — short 4-7 word chunks with start/end times → burned-in captions
    that appear exactly as you say them (60%+ watch muted; this is the retention win)
  • step cues — when narration hits a keyword you registered for a demo step, emit
    the timestamp so compose.py can switch the demo background to that step on the beat

  python3 narration_sync.py --audio me.mov --out cues/trading.json \
      --steps "order book=orderbook" "the signal fires=signal"

Output JSON: {"words":[{t0,t1,w}], "captions":[{t0,t1,text}], "steps":[{t,step}]}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

MODELS = Path.home() / ".utah" / "models" / "whisper"
DEFAULT_MODEL = MODELS / "ggml-base.en-q5_1.bin"


def extract_wav(src: Path) -> Path:
    wav = Path(tempfile.mkdtemp()) / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(wav)],
        check=True)
    return wav


def transcribe_words(wav: Path, model: Path) -> list[dict]:
    """whisper.cpp with -ml 1 → one token per segment ≈ word-level timing."""
    of = wav.with_suffix("")          # whisper-cli appends .json
    subprocess.run(
        ["whisper-cli", "-m", str(model), "-f", str(wav),
         "-oj", "-ml", "1", "-sow", "-of", str(of)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data = json.loads(Path(str(of) + ".json").read_text())
    words = []
    for seg in data.get("transcription", []):
        w = seg.get("text", "").strip()
        off = seg.get("offsets", {})
        if w and "from" in off:
            words.append({"t0": off["from"] / 1000.0, "t1": off["to"] / 1000.0, "w": w})
    return words


def group_captions(words: list[dict], max_words: int = 5, max_gap: float = 0.6,
                   max_dur: float = 2.2) -> list[dict]:
    """Pack words into short, punchy caption chunks timed to speech."""
    caps, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words
                    or w["t0"] - cur[-1]["t1"] > max_gap
                    or w["t1"] - cur[0]["t0"] > max_dur):
            caps.append(cur); cur = []
        cur.append(w)
    if cur:
        caps.append(cur)
    return [{"t0": c[0]["t0"], "t1": c[-1]["t1"],
             "text": " ".join(x["w"] for x in c).strip()} for c in caps]


def find_steps(words: list[dict], steps: list[str]) -> list[dict]:
    """steps are 'spoken phrase=step_id'; emit the time the phrase completes."""
    out = []
    parsed = [(p.split("=", 1)[0].lower().split(), p.split("=", 1)[1]) for p in steps if "=" in p]
    seq = [w["w"].lower().strip(".,!?") for w in words]
    for phrase, sid in parsed:
        n = len(phrase)
        for i in range(len(seq) - n + 1):
            if seq[i:i + n] == phrase:
                out.append({"t": words[i + n - 1]["t1"], "step": sid})
                break
    return sorted(out, key=lambda x: x["t"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--steps", nargs="*", default=[])
    a = ap.parse_args()

    wav = extract_wav(Path(a.audio))
    words = transcribe_words(wav, Path(a.model))
    cues = {"words": words,
            "captions": group_captions(words),
            "steps": find_steps(words, a.steps)}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cues, indent=2))
    print(f"{len(words)} words, {len(cues['captions'])} caption cues, "
          f"{len(cues['steps'])} step cues → {out}")
    for c in cues["captions"][:6]:
        print(f"  [{c['t0']:5.1f}-{c['t1']:5.1f}] {c['text']}")


if __name__ == "__main__":
    main()
