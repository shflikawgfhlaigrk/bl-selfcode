#!/usr/bin/env python3
"""selfdrive_capture.py — record the app driving its OWN demo (CDP screencast).

Launches headless Chrome on the deck, streams the page as it updates AND runs a
scripted "demo" (scroll / click / navigate) so the product literally walks through
itself, then assembles the frames into a video with true timing. No Screen-Recording
permission, no clutter — just the app. This is the authentic "behind you" track.

  python3 selfdrive_capture.py --url http://localhost:8100/ --dur 12 \
      --out assets/demo_trading.mp4 \
      --script "scroll 400" "wait 1.5" "click .tab-signals" "wait 2" "scroll -400"

Actions: `scroll <px>`, `wait <s>`, `click <css>`, `eval <js>`, `goto <url>`.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333


def _launch(url: str) -> subprocess.Popen:
    return subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--mute-audio", "--window-size=1600,1000",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ws_url() -> str:
    for _ in range(40):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
            for t in tabs:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("Chrome CDP never came up")


async def _drive(ws, script: list[str]) -> None:
    """Run the demo actions so the page animates itself."""
    mid = 10_000
    async def send(method, params=None):
        nonlocal mid
        mid += 1
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    for step in script:
        op, _, arg = step.partition(" ")
        if op == "wait":
            await asyncio.sleep(float(arg or 1))
        elif op == "scroll":
            await send("Runtime.evaluate", {"expression": f"window.scrollBy({{top:{arg or 300},behavior:'smooth'}})"})
        elif op == "click":
            js = f"(document.querySelector({json.dumps(arg)})||{{click(){{}}}}).click()"
            await send("Runtime.evaluate", {"expression": js})
        elif op == "eval":
            await send("Runtime.evaluate", {"expression": arg})
        elif op == "goto":
            await send("Page.navigate", {"url": arg})


async def _capture(url: str, dur: float, script: list[str], frames_dir: Path) -> list[tuple[Path, float]]:
    ws_url = _ws_url()
    frames: list[tuple[Path, float]] = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
        await ws.send(json.dumps({"id": 3, "method": "Page.startScreencast",
                                  "params": {"format": "jpeg", "quality": 85,
                                             "maxWidth": 1600, "maxHeight": 1000,
                                             "everyNthFrame": 1}}))
        start = time.monotonic()
        driver = asyncio.create_task(_drive(ws, script))
        i = 0
        while time.monotonic() - start < dur:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=dur)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                fp = frames_dir / f"f{i:05d}.jpg"
                fp.write_bytes(base64.b64decode(p["data"]))
                frames.append((fp, time.monotonic() - start))
                i += 1
                await ws.send(json.dumps({"id": 999, "method": "Page.screencastFrameAck",
                                          "params": {"sessionId": p["sessionId"]}}))
        driver.cancel()
        await ws.send(json.dumps({"id": 4, "method": "Page.stopScreencast"}))
    return frames


def _assemble(frames: list[tuple[Path, float]], dur: float, out: Path, fps: int = 30) -> None:
    """Build a concat file with real per-frame durations → faithful timing."""
    if not frames:
        raise SystemExit("no frames captured")
    fdir = frames[0][0].parent              # frames + concat live here; run ffmpeg from here
    # CDP emits frames at varying sizes as the page reflows; libx264 needs one size.
    from PIL import Image
    CW, CH = 1600, 1000
    for fp, _ in frames:
        im = Image.open(fp).convert("RGB")
        if im.size != (CW, CH):
            im.thumbnail((CW, CH))
            canvas = Image.new("RGB", (CW, CH), (8, 10, 16))
            canvas.paste(im, ((CW - im.width) // 2, (CH - im.height) // 2))
            canvas.save(fp, "JPEG", quality=90)
    concat = fdir / "_frames.txt"
    lines = []
    for idx, (fp, t) in enumerate(frames):
        nxt = frames[idx + 1][1] if idx + 1 < len(frames) else dur
        d = max(0.01, nxt - t)
        lines.append(f"file '{fp.name}'\nduration {d:.3f}")
    lines.append(f"file '{frames[-1][0].name}'")   # concat demuxer needs last file repeated
    concat.write_text("\n".join(lines))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", "_frames.txt", "-vsync", "cfr", "-r", str(fps),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-y", str(out.resolve())],
        check=True, cwd=str(fdir))
    concat.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--dur", type=float, default=12.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--script", nargs="*", default=["wait 2", "scroll 300", "wait 2",
                                                    "scroll 300", "wait 2", "scroll -600"])
    a = ap.parse_args()
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out.parent / "_sd_frames"
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()

    proc = _launch(a.url)
    try:
        frames = asyncio.run(_capture(a.url, a.dur, a.script, frames_dir))
    finally:
        proc.terminate()
    _assemble(frames, a.dur, out)
    print(f"self-drive: {len(frames)} frames over {a.dur}s → {out}")


if __name__ == "__main__":
    main()
