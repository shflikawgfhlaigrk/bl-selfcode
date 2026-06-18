#!/usr/bin/env python3
"""stepswitch.py — cut the demo background to the step you're talking about.

Given one short clip per demo step + the timestamps you SAY each step (from
narration_sync --steps), assemble a single background video that switches clips
on your words. The demo literally follows your narration: say "the order book"
and the background cuts to the order book.

  python3 stepswitch.py --dur 20 --out assets/demo_stepped.mp4 \
      --steps chart=clips/chart.mp4 orderbook=clips/ob.mp4 signal=clips/sig.mp4 \
      --cues cues/trading.json --pre chart

--cues is narration_sync output; its "steps" array ({t, step}) drives the cuts.
Feed the result to compose.py --demo.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

CW, CH, FPS = 1600, 1000, 30


def segment(clip: Path, dur: float, dst: Path) -> None:
    """Loop+trim a step clip to exactly `dur`, normalized to a common size."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
         "-i", str(clip), "-t", f"{dur:.3f}",
         "-vf", f"scale={CW}:{CH}:force_original_aspect_ratio=increase,"
                f"crop={CW}:{CH},setsar=1,fps={FPS}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-y", str(dst)],
        check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", nargs="+", required=True, help="id=clip.mp4 …")
    ap.add_argument("--cues", required=True, help="narration_sync json (uses .steps)")
    ap.add_argument("--dur", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pre", help="step shown before the first spoken cue")
    a = ap.parse_args()

    clips = {s.split("=", 1)[0]: Path(s.split("=", 1)[1]) for s in a.steps}
    cues = json.loads(Path(a.cues).read_text()).get("steps", [])
    cues = sorted([c for c in cues if c["step"] in clips and c["t"] < a.dur],
                  key=lambda c: c["t"])

    pre = a.pre or next(iter(clips))
    # build [t0,t1,step] windows: pre-roll until first cue, then each cue to the next
    windows, cur, t0 = [], pre, 0.0
    for c in cues:
        if c["t"] > t0:
            windows.append((t0, c["t"], cur))
        cur, t0 = c["step"], c["t"]
    windows.append((t0, a.dur, cur))

    tmp = Path(tempfile.mkdtemp())
    segs = []
    for i, (s, e, step) in enumerate(windows):
        seg = tmp / f"seg{i}.mp4"
        segment(clips[step], max(0.2, e - s), seg)
        segs.append(seg)
        print(f"  [{s:5.1f}-{e:5.1f}] {step}")

    concat = tmp / "list.txt"
    concat.write_text("\n".join(f"file '{s}'" for s in segs))
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
         "-r", str(FPS), "-y", str(out)],
        check=True)
    print(f"stepped demo ({len(windows)} cuts) → {out}")


if __name__ == "__main__":
    main()
