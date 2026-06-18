#!/usr/bin/env python3
"""compose.py — Black Label "presenter-over-autonomous-demo" compositor.

Takes (a self-running app demo) + (you, background removed by matte.swift) and
renders a 9:16 short where you stand CENTER, semi-transparent, with the demo
glowing THROUGH you, wrapped in a futuristic Black-Gold HUD. All local/free —
PIL generates the HUD/grid overlays, ffmpeg does the compositing.

  python3 compose.py --demo deck.png --presenter you_matte.mov \
      --hook "I LET MY AI TRADE LIVE" --caption "watch it run itself" \
      --music bed.mp3 --dur 12 --out out/trading.mp4

--demo accepts an image (gets a slow cinematic push) or a video (used as-is).
If --presenter is omitted, a labelled placeholder cutout is generated so you can
see the layout before you've filmed anything.
"""
from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
W, H = 1080, 1920                      # 9:16 vertical
FPS = 30
GOLD = (231, 188, 86)                  # Black Label gold
GOLD_HI = (255, 214, 120)
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

# Presenter sits centered; this is the on-screen box the HUD ring frames.
PRES_CX, PRES_CY = W // 2, int(H * 0.50)
PRES_R = int(W * 0.46)                 # ring radius


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def make_grid_vignette(out: Path) -> Path:
    """Faint tech grid + scanlines + vignette — the 'inside a HUD' atmosphere."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    step = 90
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(*GOLD, 10), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(*GOLD, 10), width=1)
    # scanlines
    for y in range(0, H, 3):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, 22), width=1)
    # vignette: dark, soft-edged border
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    vd.rounded_rectangle([60, 60, W - 60, H - 60], radius=70, fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 170))
    dark.putalpha(Image.eval(vig, lambda v: 170 - int(v * 170 / 255)))
    img = Image.alpha_composite(img, dark)
    img.save(out)
    return out


def _arc_ticks(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, n: int,
               length: int, color, width: int, start_deg: float = 0):
    for i in range(n):
        a = math.radians(start_deg + i * 360 / n)
        x0, y0 = cx + r * math.cos(a), cy + r * math.sin(a)
        x1, y1 = cx + (r + length) * math.cos(a), cy + (r + length) * math.sin(a)
        d.line([(x0, y0), (x1, y1)], fill=color, width=width)


def make_hud_ring(out: Path) -> Path:
    """Glowing reticle + corner brackets that frame the presenter."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = PRES_CX, PRES_CY, PRES_R
    # main ring (drawn thick then blurred for glow, then a crisp ring on top)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*GOLD, 200), width=10)
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    img = Image.alpha_composite(img, glow)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*GOLD_HI, 230), width=3)
    # dashed inner ring
    _arc_ticks(d, cx, cy, r - 22, 72, 8, (*GOLD, 120), 2)
    # rotating-style longer ticks at quarters
    _arc_ticks(d, cx, cy, r - 4, 4, 26, (*GOLD_HI, 255), 4, start_deg=45)
    # corner brackets framing the whole 9:16 frame
    m, ln, wd = 46, 70, 4
    for (ax, ay, dx, dy) in [(m, m, 1, 1), (W - m, m, -1, 1),
                             (m, H - m, 1, -1), (W - m, H - m, -1, -1)]:
        d.line([(ax, ay), (ax + dx * ln, ay)], fill=(*GOLD, 220), width=wd)
        d.line([(ax, ay), (ax, ay + dy * ln)], fill=(*GOLD, 220), width=wd)
    img.save(out)
    return out


def _text_layer(text: str, font_path: str, size: int, fill, y: int,
                box_alpha: int = 120, accent: bool = False) -> Image.Image:
    """Full-frame transparent layer with centered text + readability box.
    (This ffmpeg lacks drawtext/freetype, so all type is baked here in PIL.)"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # auto-fit: shrink until the line fits the safe width so it never runs off-frame
    f = _font(font_path, size)
    while size > 26 and d.textlength(text, font=f) > W - 120:
        size -= 3
        f = _font(font_path, size)
    tw = d.textlength(text, font=f)
    bbox = f.getbbox(text)
    th = bbox[3] - bbox[1]
    x = (W - tw) / 2
    pad = int(size * 0.45)
    # rounded readability box
    d.rounded_rectangle([x - pad, y - pad * 0.5, x + tw + pad, y + th + pad * 0.7],
                        radius=18, fill=(0, 0, 0, box_alpha))
    # soft shadow then text
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((x, y - bbox[1]), text, font=f, fill=(0, 0, 0, 200))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.text((x, y - bbox[1]), text, font=f, fill=(*fill, 255))
    if accent:
        d.line([(x, y + th + pad * 0.4), (x + tw, y + th + pad * 0.4)],
               fill=(*GOLD_HI, 255), width=4)
    return img


def make_text_pngs(hook: str, caption: str, assets: Path) -> tuple[Path, Path]:
    hk = _text_layer(hook.upper(), FONT_BLACK, 80, (255, 255, 255), 150,
                     box_alpha=120, accent=True)
    cp = _text_layer(caption, FONT_BOLD, 50, GOLD, H - 250, box_alpha=130)
    hp, cpp = assets / "_hook.png", assets / "_caption.png"
    hk.save(hp); cp.save(cpp)
    return hp, cpp


def make_placeholder_presenter(out_mov: Path, dur: float) -> Path:
    """A gold-edged human bust silhouette w/ alpha — stand-in until you film.
    Rendered to ProRes4444 so it composites exactly like a real matte."""
    sil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(sil)
    cx = W // 2
    head_r = 150
    head_cy = int(H * 0.30)
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
              fill=(60, 66, 80, 255), outline=(*GOLD, 255), width=5)
    # shoulders/torso
    d.rounded_rectangle([cx - 360, head_cy + head_r - 30, cx + 360, H],
                        radius=200, fill=(60, 66, 80, 255), outline=(*GOLD, 255), width=5)
    label_f = _font(FONT_BOLD, 40)
    txt = "YOUR MATTE DROPS IN HERE"
    tw = d.textlength(txt, font=label_f)
    d.text((cx - tw / 2, head_cy - 20), txt, font=label_f, fill=(255, 255, 255, 230))
    png = out_mov.with_suffix(".png")
    sil.save(png)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(png),
         "-t", str(dur), "-r", str(FPS), "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", "-y", str(out_mov)],
        check=True)
    return out_mov


def build(demo: Path, presenter: Path, out: Path, *, dur: float, hook: str,
          caption: str, music: Path | None, opacity: float,
          captions: list[dict] | None = None,
          beats: list[float] | None = None) -> None:
    assets = ROOT / "assets"
    grid = make_grid_vignette(assets / "_grid.png")
    ring = make_hud_ring(assets / "_ring.png")
    hook_png, cap_png = make_text_pngs(hook, caption, assets)

    demo_is_img = demo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    inputs: list[str] = []
    if demo_is_img:
        inputs += ["-loop", "1", "-t", str(dur), "-i", str(demo)]   # 0
    else:
        inputs += ["-i", str(demo)]
    inputs += ["-i", str(presenter)]                              # 1
    inputs += ["-loop", "1", "-t", str(dur), "-i", str(grid)]     # 2
    inputs += ["-loop", "1", "-t", str(dur), "-i", str(ring)]     # 3
    inputs += ["-loop", "1", "-t", str(dur), "-i", str(hook_png)] # 4

    # caption track: timed speech-synced cues (from narration_sync) OR one static line
    cue_idx0 = 5
    if captions:
        for i, c in enumerate(captions):
            layer = _text_layer(c["text"], FONT_BOLD, 50, GOLD, H - 250, box_alpha=130)
            p = assets / f"_cap{i}.png"; layer.save(p)
            inputs += ["-loop", "1", "-t", str(dur), "-i", str(p)]  # 5+i
        music_idx = cue_idx0 + len(captions)
    else:
        inputs += ["-loop", "1", "-t", str(dur), "-i", str(cap_png)]  # 5
        music_idx = 6
    if music:
        inputs += ["-i", str(music)]

    # background: blurred fill (so the frame's never empty) + the WHOLE demo fit sharp
    # and centered (a landscape deck keeps all its panels instead of being cropped away).
    bg = (
        f"[0:v]split=2[d1][d2];"
        f"[d1]scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
        f"zoompan=z='min(1.0+0.0008*on,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={W}x{H}:fps={FPS},gblur=sigma=38,eq=brightness=-0.14:saturation=0.82[fill];"
        f"[d2]scale={W-40}:-2,setsar=1,eq=saturation=1.07:contrast=1.05,"
        f"colorbalance=rm=0.05:gm=0.01:bm=-0.05[band];"
        f"[fill][band]overlay=(W-w)/2:(H-h)/2:format=auto,"
        f"trim=duration={dur},setpts=PTS-STARTPTS[bg];"
    )
    # presenter: fit, make translucent so the demo shows THROUGH, center it
    pres = (
        f"[1:v]scale=-1:{int(H*0.92)},format=yuva420p,"
        f"colorchannelmixer=aa={opacity},setpts=PTS-STARTPTS[pp];"
    )
    # composite stack: bg <- presenter <- grid <- ring
    comp = (
        f"[bg][pp]overlay=(W-w)/2:(H-h)/2:format=auto[c1];"
        f"[c1][2:v]overlay=0:0[c2];"
        f"[c2][3:v]overlay=0:0[c3];"
    )
    # hook punches in for the first ~2.4s (frontloaded intensity)
    txt = (
        f"[4:v]fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=2.1:d=0.3:alpha=1[hk];"
        f"[c3][hk]overlay=0:0:enable='lt(t,2.4)'[c4];"
    )
    if captions:
        # each cue shows only while that phrase is spoken — captions track the voice
        prev = "c4"
        for i, c in enumerate(captions):
            lbl = "v" if i == len(captions) - 1 else f"k{i}"
            txt += (f"[{prev}][{cue_idx0 + i}:v]overlay=0:0:"
                    f"enable='between(t,{c['t0']:.2f},{c['t1']:.2f})'[{lbl}];")
            prev = lbl
        txt = txt.rstrip(";")
        # guard: if a clip somehow had no caption between two times, ensure [v] exists
        if not txt.endswith("[v]"):
            txt += f";[{prev}]null[v]" if prev != "v" else ""
    else:
        txt += "[c4][5:v]overlay=0:0[v]"
    filt = bg + pres + comp + txt

    # beat-sync: a short brightness pop on each beat → the whole frame pulses to the track
    vlabel = "[v]"
    if beats:
        bumps = "+".join(
            f"0.14*exp(-((t-{b:.3f})/0.045)*((t-{b:.3f})/0.045))"
            for b in beats if b < dur)
        if bumps:
            filt += f";[v]eq=brightness='{bumps}':eval=frame[vb]"
            vlabel = "[vb]"

    maps = ["-map", vlabel]
    if music:
        # normalize loudness so narration sits at a consistent, platform-friendly level
        maps += ["-map", f"{music_idx}:a", "-shortest",
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                 "-c:a", "aac", "-b:a", "192k"]
    cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", filt, *maps,
            "-r", str(FPS), "-t", str(dur),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
            "-preset", "medium", "-movflags", "+faststart", "-y", str(out)])
    print("ffmpeg:", " ".join(shlex.quote(c) for c in cmd[:6]), "…")
    subprocess.run(cmd, check=True)
    print(f"composed → {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", required=True, help="self-running demo (image or video)")
    ap.add_argument("--presenter", help="matted you (ProRes alpha). Omit for placeholder.")
    ap.add_argument("--out", default=str(ROOT / "out" / "sample.mp4"))
    ap.add_argument("--hook", default="WATCH MY AI RUN IT")
    ap.add_argument("--caption", default="black label")
    ap.add_argument("--music", default=None)
    ap.add_argument("--captions", default=None,
                    help="cues json from narration_sync.py → speech-synced captions")
    ap.add_argument("--beats", default=None,
                    help="beats json from beats.py → frame pulses on the beat")
    ap.add_argument("--dur", type=float, default=10.0)
    ap.add_argument("--opacity", type=float, default=0.62,
                    help="presenter opacity; <1 lets the demo glow through you")
    a = ap.parse_args()

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    presenter = Path(a.presenter) if a.presenter else \
        make_placeholder_presenter(ROOT / "assets" / "_placeholder_presenter.mov", a.dur)
    music = Path(a.music) if a.music else None
    import json
    captions = None
    if a.captions:
        cues = json.loads(Path(a.captions).read_text()).get("captions", [])
        captions = [c for c in cues if c["t0"] < a.dur]   # keep cues within the cut
    beats = None
    if a.beats:
        beats = json.loads(Path(a.beats).read_text()).get("beats", [])
    build(Path(a.demo), presenter, out, dur=a.dur, hook=a.hook,
          caption=a.caption, music=music, opacity=a.opacity,
          captions=captions, beats=beats)


if __name__ == "__main__":
    main()
