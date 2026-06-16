# Sovereign — the full assistant  ·  branch-off app

> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/` (+ the separate `~/sovereign-live` build).

## Spec (what the app must do)
- Everything the assistant already does — **weather, you name it**.
- An **area to populate the Claude login** (sign in to Claude → brain runs on the subscription).

## Modules (copied)
brain, local_brain, fm_local, weather, brief, clock, console, jobs_status, tasks, timers, trackers, selfcode_web, and the whole `voice/` package (wake, stt, tts, vad, barge, loop, macapp, …)

## Status
- ✅ exists: brain on the Claude subscription, full voice pipeline, weather, dashboard modules, self-coding.
- 🔨 to build: **the Claude-login population area** (the "Sign in to Claude" flow lives in `~/sovereign-live`; wire it here, token in Keychain).

## Shared spine (`../_shared_core/`)
config, failures, db_pool, foundation, alerts, objects
