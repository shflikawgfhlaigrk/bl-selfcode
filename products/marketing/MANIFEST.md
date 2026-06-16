# Marketing  ·  branch-off app

> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`.

## Spec (what the app must do)
- Use **Apple's top-of-the-line** imagery + video tooling to make marketing videos and assets.
- Per-lead websites and spotlight outreach.

## Modules (copied)
marketer, reel_queue, sitegen, news

## Status
- ✅ exists: per-lead site generation (`sitegen`), reel queue (`reel_queue`), spotlight emails (`marketer`).
- 🔨 to build: **Apple imagery/video pipeline** (Image Playground / Apple Intelligence + video assembly) driving `reel_queue`.

## Shared spine (`../_shared_core/`)
config, failures, foundation, local_brain, mail
## Live crons
com.utah.marketer
