# Black Label Studio — presenter-over-autonomous-demo shorts

Make videos where **you stand center, background removed, semi-transparent, and your
app runs its own demo *behind and through* you**, wrapped in a futuristic Black-Gold
HUD. 9:16 vertical. One video per product.

Everything is **local and free** — no Veo/Sora/CapCut subscription. We replicate the
techniques: Apple Vision for background removal (the same model Apple ships in Final
Cut), ffmpeg for compositing, whisper-cli for narration sync.

## Pipeline (3 stages)

| Stage | Tool | What it does |
|-------|------|--------------|
| 1. Demo background | `capture_demo.py` | Screenshots / records the app driving itself |
| 2. Presenter matte | `matte` (Swift, Apple Vision) | Removes your webcam background → ProRes4444 + alpha |
| 3. Composite | `compose.py` | Center-translucent + HUD ring + hook/captions → 1080×1920 |

`studio.py` chains all three with per-product presets.

## Quick start

```bash
# 0. build the matte tool once
swiftc -O matte.swift -o matte

# 1. see the layout before you film anything (placeholder presenter, live deck behind):
python3 studio.py --product trading --dur 8

# 2a. turnkey: record yourself from the webcam+mic and build it in one shot:
python3 studio.py --product trading --record 20 --beats --queue

# 2b. or bring your own clip:
python3 studio.py --product trading --footage ~/Desktop/me_trading.mov \
    --beats --queue --dur 20            # add --rotate 90/180/270 if it's sideways

# one per product:
python3 studio.py --product leads      --record 20 --beats --queue
python3 studio.py --product realestate --footage me_re.mov --beats
```

Robustness baked in: the whole deck stays visible (blurred fill + sharp fit, no
crop), captions auto-shrink to fit, audio is loudness-normalized, matte edges are
feathered, `--start` trims video+audio+captions together, and a silent clip just
skips captions instead of failing.

Output lands in `out/<product>.mp4`, ready for the reel queue (`products/marketing/reel_queue.py`).

## Filming yourself (so the matte is clean)

- Any background is fine — Apple Vision removes it, no green screen needed.
- Even, front lighting on your face; avoid matching your shirt to the wall.
- Frame head-and-shoulders, centered. Look at the lens.
- Talk through the demo as if it's happening behind you — the demo timing is set in post.

## Composition

Default is **center / translucent** (the option you picked): you sit at frame-center at
`--opacity 0.62` so the demo glows through you, ringed by the gold HUD reticle + corner
brackets. Tune with `compose.py --opacity` (1.0 = solid, lower = more see-through).

## Autonomous demo — three levels of authenticity

- **A. Cinematic still:** `capture_demo.py --url` grabs the live deck; `compose.py` adds a
  slow push. Zero setup.
- **B. Multi-shot:** `capture_demo.py --shots URL1 URL2 …` cross-dissolves deck states.
- **C. True self-driving (DONE):** `selfdrive_capture.py` launches headless Chrome on the
  deck, **runs a scripted demo (scroll/click/navigate) while CDP records the screen**, so
  the app literally walks through itself. No Screen-Recording permission. This is the
  `studio.py` default.

## Narration sync (DONE)

`narration_sync.py` runs `whisper-cli` over your audio for word-level timing, then:
- **captions** appear exactly as you speak them (the muted-viewer retention win),
- **step cues** (`--steps "the signal fires=signal"`) emit the timestamp a phrase is
  spoken so the demo can switch to that step on your voice.
`studio.py` feeds your footage's audio through this automatically.

## Beat sync (DONE)

`beats.py` finds the track's beats (spectral-flux onset detection + autocorrelation
tempo, numpy only — replicates Apple's Beat Detection) → `compose.py --beats` makes
the whole frame pulse on the beat. `studio.py --beats` wires it in. `--bpm` forces a
fixed grid.

## Step-synced demo switching (DONE)

The demo cuts to the view you're talking about, on your word:
```bash
# capture one clip per step (different deck views), then:
python3 stepswitch.py --dur 20 --out assets/demo_stepped.mp4 \
    --steps chart=clips/chart.mp4 orderbook=clips/ob.mp4 signal=clips/sig.mp4 \
    --cues cues/trading.json --pre chart     # cues from narration_sync --steps
python3 compose.py --demo assets/demo_stepped.mp4 --presenter you.mov …
```
Say "the order book" → background cuts to the order book at that timestamp.

## Reel queue

`studio.py --queue` copies the finished short + a caption into
`~/Desktop/REELS-TO-POST/`, where `products/marketing/reel_queue.py` posts it.

## Roadmap

- [x] Apple Vision matte (proven on real footage; `--rotate` for any orientation)
- [x] Narration sync (whisper-cli → speech-timed captions + step cues)
- [x] True self-driving demo capture (CDP screencast)
- [x] Beat sync (onset detection → frame pulse)
- [x] Step-synced demo switching (cut demo on spoken cue)
- [x] Reel-queue handoff
- [ ] Record your own head-and-shoulders clip to replace the placeholder/test face
- [ ] Per-product step storyboards (define each product's demo views + spoken cues)

## Zero-hallucination rule

Hooks/captions claim only what the product actually does. No fabricated metrics or
track records on screen (see the Black Label binding).
```
```

## Files
- `matte.swift` / `matte` — Apple Vision person segmentation (`--quality accurate|balanced|fast`, `--start/--dur`, `--rotate 0|90|180|270`, `--testmask`).
- `compose.py` — futuristic compositor; `--captions` (speech-synced) + `--beats` (pulse). Bakes HUD/grid/text overlays in PIL (this ffmpeg lacks drawtext).
- `selfdrive_capture.py` — CDP self-driving demo capture (the app clicks through itself).
- `capture_demo.py` — simple headless-Chrome still / multi-shot capture.
- `narration_sync.py` — whisper-cli → word timing → caption cues + step cues.
- `beats.py` — onset/beat detection → beat times (numpy, no librosa).
- `stepswitch.py` — cut the demo background between step clips on spoken cues.
- `studio.py` — one command: self-drive + matte + narration + beats + compose + `--queue`.
