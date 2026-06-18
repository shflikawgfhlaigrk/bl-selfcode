#!/usr/bin/env python3
"""capture_demo.py — grab the "self-running demo" background, autonomously.

Headless Chrome screenshots a live product deck (no Screen-Recording permission,
no clutter — just the app UI). Pass several URLs/states and they're stitched into
a moving multi-shot background with cross-dissolves, so the demo appears to walk
through itself behind you.

  python3 capture_demo.py --url http://localhost:8100/ --out assets/demo_trading.png
  python3 capture_demo.py --shots http://localhost:8100/ http://localhost:8766/ \
      --hold 2.5 --out assets/demo_trading.mp4   # → moving background video

For a fully authentic "it clicks through itself" capture, drive the real app and
screen-record it (see README → autonomous demo, option C).
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIN = "1600,1000"


def shot(url: str, out: Path) -> Path:
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={WIN}", f"--screenshot={out}", url],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.exists():
        raise SystemExit(f"capture failed for {url} (is the deck up?)")
    return out


def stitch(pngs: list[Path], out: Path, hold: float, fps: int = 30) -> Path:
    """Cross-dissolve a sequence of stills into one moving background video."""
    xf = 0.6
    parts, filt, n = [], "", len(pngs)
    for i, p in enumerate(pngs):
        parts += ["-loop", "1", "-t", str(hold), "-i", str(p)]
    # scale each, then chain xfades
    for i in range(n):
        filt += f"[{i}:v]scale=1600:1000,setsar=1,fps={fps},format=yuv420p[s{i}];"
    if n == 1:
        filt += "[s0]null[v]"
    else:
        prev = "s0"
        t = hold
        for i in range(1, n):
            lbl = f"x{i}" if i < n - 1 else "v"
            off = round(t - xf, 3)
            filt += f"[{prev}][s{i}]xfade=transition=fade:duration={xf}:offset={off}[{lbl}];"
            prev = lbl
            t += hold - xf
        filt = filt.rstrip(";")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *parts,
           "-filter_complex", filt, "-map", "[v]", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-crf", "18", "-y", str(out)]
    subprocess.run(cmd, check=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="single URL → PNG")
    ap.add_argument("--shots", nargs="+", help="multiple URLs → moving .mp4")
    ap.add_argument("--hold", type=float, default=2.5, help="seconds per shot")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.url:
        shot(a.url, out)
        print(f"captured → {out}")
    elif a.shots:
        tmp = [out.parent / f"_shot{i}.png" for i in range(len(a.shots))]
        for u, t in zip(a.shots, tmp):
            shot(u, t)
        stitch(tmp, out, a.hold)
        print(f"captured {len(a.shots)} shots → {out}")
    else:
        raise SystemExit("pass --url or --shots")


if __name__ == "__main__":
    main()
