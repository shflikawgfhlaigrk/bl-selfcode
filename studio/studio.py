#!/usr/bin/env python3
"""studio.py — one command to make a Black Label "presenter-over-demo" short.

Chains the three local/free stages:
  1. capture_demo.py  → the app demo running behind you (or pass --demo)
  2. matte (Swift)    → removes your webcam background (Apple Vision)
  3. compose.py       → center-translucent composite + futuristic HUD, 9:16

One video per product — pick the product and it loads sensible hook/caption/deck
defaults you can override.

  # full pipeline from your raw webcam clip:
  python3 studio.py --product trading --footage ~/Desktop/me_trading.mov

  # see the layout before filming (placeholder presenter):
  python3 studio.py --product trading

  # override copy + bring your own demo + music:
  python3 studio.py --product leads --footage me.mov \
      --demo assets/demo_leads.mp4 --music bed.mp3 \
      --hook "I FOUND 76 LEADS IN 30s" --caption "any market. on autopilot."
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Per-product presets. `deck` is the live URL capture_demo.py screenshots when no
# --demo is given. Hook/caption follow the zero-hallucination rule: claims must be
# things the app actually does. Adjust freely.
_SCROLL = ["wait 1.5", "scroll 350", "wait 1.5", "scroll 350", "wait 1.5", "scroll -700", "wait 1"]
PRODUCTS = {
    "trading":    {"deck": "http://localhost:8100/", "hook": "MY AI CALLS THE TRADE",
                   "caption": "live signals · you stay in control", "script": _SCROLL},
    "realestate": {"deck": "http://localhost:8766/", "hook": "IT FINDS THE FLIP",
                   "caption": "probate + builder lots, mapped", "script": _SCROLL},
    "leads":      {"deck": "http://localhost:8766/", "hook": "LEADS ON AUTOPILOT",
                   "caption": "any market · emails from your inbox", "script": _SCROLL},
    "marketing":  {"deck": "http://localhost:8766/", "hook": "IT MAKES THE AD",
                   "caption": "apple-grade reels, automatically", "script": _SCROLL},
    "sovereign":  {"deck": "http://localhost:8766/", "hook": "MY AI RUNS ITSELF",
                   "caption": "voice · memory · self-coding", "script": _SCROLL},
}


def run(cmd: list[str]) -> None:
    print("·", " ".join(str(c) for c in cmd[:3]), "…")
    subprocess.run(cmd, check=True)


def has_audio(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def check_capture(footage: str) -> None:
    """Warn loudly if a recording came back with no person or no sound, so we
    never silently ship an empty take."""
    vd = subprocess.run(["ffmpeg", "-hide_banner", "-i", footage, "-af",
                         "volumedetect", "-f", "null", "-"],
                        capture_output=True, text=True)
    maxvol = next((l.split("max_volume:")[1].strip()
                   for l in vd.stderr.splitlines() if "max_volume" in l), "?")
    dbg = subprocess.run([str(ROOT / "matte"), footage, "/tmp/_chk.mov",
                          "--quality", "fast", "--start", "1", "--dur", "1"],
                         capture_output=True, text=True, env={**os.environ, "MATTE_DEBUG": "1"})
    person = next((s for s in dbg.stderr.split() if s.startswith("person=")), "person=?")
    print(f"  capture check: {person}, max_volume={maxvol}")
    if person in ("person=0%",):
        print("  ⚠️  NO PERSON detected in frame — sit centered in the camera before recording.")
    if maxvol not in ("?",) and maxvol.replace("-", "").replace(" dB", "").replace(".", "").isdigit():
        if float(maxvol.split()[0]) < -60:
            print("  ⚠️  MIC IS SILENT — grant Microphone access to your terminal in "
                  "System Settings → Privacy & Security → Microphone, then unmute.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, choices=sorted(PRODUCTS))
    ap.add_argument("--footage", help="your raw webcam clip; omit for placeholder")
    ap.add_argument("--demo", help="demo bg (image/video); omit to capture the live deck")
    ap.add_argument("--still", action="store_true",
                    help="cinematic still bg instead of self-driving capture")
    ap.add_argument("--music", help="audio bed; defaults to your footage's narration")
    ap.add_argument("--narration", help="narration audio for captions; defaults to footage")
    ap.add_argument("--steps", nargs="*", default=[], help="'spoken phrase=step_id' demo cues")
    ap.add_argument("--hook")
    ap.add_argument("--caption")
    ap.add_argument("--dur", type=float, default=10.0)
    ap.add_argument("--quality", default="accurate", help="matte quality")
    ap.add_argument("--start", type=float, help="trim footage start (s)")
    ap.add_argument("--rotate", type=int, default=0, help="rotate footage 0/90/180/270")
    ap.add_argument("--record", type=float,
                    help="capture this many seconds from the webcam as the footage")
    ap.add_argument("--beats", action="store_true", help="add beat-synced frame pulses")
    ap.add_argument("--queue", action="store_true",
                    help="drop the finished short into ~/Desktop/REELS-TO-POST for the reel queue")
    a = ap.parse_args()

    p = PRODUCTS[a.product]
    assets = ROOT / "assets"
    out = ROOT / "out" / f"{a.product}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    # 0. footage: capture from webcam if asked, then trim so video+audio+captions align
    footage = a.footage
    if a.record:
        footage = str(assets / f"cam_{a.product}.mov")
        print(f"get ready — recording {a.record}s from webcam + mic…")
        for n in (3, 2, 1):
            print(f"   {n}…", flush=True); time.sleep(1)
        print("   ● REC — look at the lens and talk now")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
             "-framerate", "30", "-pixel_format", "nv12", "-i", "0:0",
             "-t", str(a.record), "-c:a", "aac", "-y", footage])
        check_capture(footage)
    if footage and a.start is not None:
        trimmed = str(assets / f"trim_{a.product}.mov")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(a.start),
             "-t", str(a.dur), "-i", footage, "-c:v", "libx264", "-c:a", "aac",
             "-y", trimmed])
        footage = trimmed            # everything downstream starts at 0 → stays in sync

    # 1. demo background — self-driving capture by default, still or BYO override
    demo = a.demo
    if not demo and a.still:
        demo = str(assets / f"demo_{a.product}.png")
        run(["python3", str(ROOT / "capture_demo.py"), "--url", p["deck"], "--out", demo])
    elif not demo:
        demo = str(assets / f"demo_{a.product}.mp4")
        run(["python3", str(ROOT / "selfdrive_capture.py"), "--url", p["deck"],
             "--dur", str(a.dur), "--out", demo, "--script", *p["script"]])

    # 2. matte the presenter (Apple Vision) — skip → placeholder if no footage
    presenter = None
    if footage:
        presenter = str(assets / f"matte_{a.product}.mov")
        matte_cmd = [str(ROOT / "matte"), footage, presenter, "--quality", a.quality,
                     "--start", "0", "--dur", str(a.dur)]
        if a.rotate:
            matte_cmd += ["--rotate", str(a.rotate)]
        run(matte_cmd)

    # 3. narration → speech-synced captions + audio bed (your voice runs the show)
    narration = a.narration or footage
    if narration and not has_audio(narration):
        print(f"  (no audio track in {Path(narration).name} — skipping captions/sound)")
        narration = None
    captions_json = None
    if narration:
        captions_json = str(ROOT / "cues" / f"{a.product}.json")
        ns = ["python3", str(ROOT / "narration_sync.py"), "--audio", narration,
              "--out", captions_json]
        if a.steps:
            ns += ["--steps", *a.steps]
        run(ns)

    # 4. beat track (optional)
    beats_json = None
    music = a.music or narration            # your spoken track by default
    if a.beats and music:
        beats_json = str(ROOT / "cues" / f"{a.product}_beats.json")
        run(["python3", str(ROOT / "beats.py"), "--audio", music, "--out", beats_json])

    # 5. compose
    compose = ["python3", str(ROOT / "compose.py"), "--demo", demo,
               "--hook", a.hook or p["hook"], "--caption", a.caption or p["caption"],
               "--dur", str(a.dur), "--out", str(out)]
    if presenter:
        compose += ["--presenter", presenter]
    if captions_json:
        compose += ["--captions", captions_json]
    if beats_json:
        compose += ["--beats", beats_json]
    if music:
        compose += ["--music", music]
    run(compose)
    print(f"\n✓ {a.product} short → {out}")

    # 6. hand off to the reel queue (reel_queue.py scans ~/Desktop/REELS-TO-POST)
    if a.queue:
        qdir = Path.home() / "Desktop" / "REELS-TO-POST"
        qdir.mkdir(parents=True, exist_ok=True)
        import shutil
        dst = qdir / f"blacklabel_{a.product}.mp4"
        shutil.copy(str(out), dst)
        (qdir / f"blacklabel_{a.product}.txt").write_text(
            f"{(a.hook or p['hook'])} — {(a.caption or p['caption'])} #blacklabel #{a.product}")
        print(f"  → queued for posting: {dst}")


if __name__ == "__main__":
    main()
