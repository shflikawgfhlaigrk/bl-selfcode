# 11 — INTERFACE audit (voice + chat = the agentic harness)

> The thing Michael lives in (he never opens a terminal again). Voice + chat as one
> agentic surface. Dirt (`acesd/voice`, `daemon.py`, `tools.py`) + web SOTA.

## 1. What we had
- **Two brains, two paths:** voice ran a fast local tier (Qwen-14B, `max_tokens=512`),
  chat a separate path; **hidden thinking** (one-line answers).
- **Wake:** openWakeWord (`hey_ace.onnx`) + **smart-turn-v3.2**; **STT:** whisper.cpp
  (`ggml-base.en`, ~2–4 s); **VAD:** Silero/webrtc; **TTS:** Piper (Kokoro/XTTS erased).
- **Chat tools:** sandboxed — `db_query` (whitelisted), `file_read` (few roots),
  `http_get` (loopback) — **no shell/git/subagents**; it recited memory and
  deflected self-questions ("not wired into that data stream").

## 2. Why we did it
Latency-first voice → a small fast local model with a tight token cap. A sandboxed
toolset to keep the local model safe. Memory-first recite to avoid confabulation.
All reasonable for a *voice assistant* — but Utah isn't that.

## 3. What we didn't think about
- **It's the terminal replacement.** A 14B/512-token recite-only chat with no
  shell/git/file/subagent tools **can't do what this terminal does** — it couldn't
  even tell you what it was made of (§ the chat-box audit).
- **whisper.cpp is the latency pole** (~2–4 s); Piper is robotic.
- Voice and chat were split (two brains) instead of one surface.

## 4. What we're gonna change
- **One unified surface = a full frontier AGENTIC HARNESS.** Voice or text → the
  *same* loop. The operator brain = **Claude (Agent SDK / `claude -p`) with the
  complete tool surface** (shell, file r/w, git, web, subagents, self-edit), with
  the **entire thinking + tool + subagent trace streamed to the chat** (Cursor/
  Claude-style). Spoken turns also TTS the conclusion.
- **Intent-tiered:** **Apple Foundation Models (free, on-device ~3B)** + MLX for
  fast classify/route/chitchat; anything needing investigation/coding/ops/analysis
  **auto-escalates to the harness.**
- **STT:** **Apple SpeechAnalyzer** (native, macOS 26 ✓, ~55% faster than Whisper,
  no model download) as default; **Moonshine** for ultra-low-latency streaming.
- **TTS:** Piper realtime + **F5-TTS/StyleTTS2** natural (F5 fine — Utah is personal).
- **Wake/turn:** keep openWakeWord + smart-turn-v3.2; barge-in.
- **Self-introspection is a granted capability** (read its own files/processes/db),
  never "I'm not wired in."

## 5. How it helps
- The surface **does everything this terminal does** — the baseline that makes
  "never open a terminal again" real.
- **Sub-8 s natural voice** (SpeechAnalyzer STT 3–6× faster than whisper.cpp;
  F5/StyleTTS2 natural); **free** Apple STT + structured tier (no model download,
  no token cost).
- One brain, one surface, visible thinking — the opposite of the recite-only black
  box.

Sources: Apple SpeechAnalyzer https://www.macrumors.com/2025/06/18/apple-transcription-api-faster-than-whisper/ · Apple Foundation Models https://developer.apple.com/documentation/FoundationModels · Moonshine vs Whisper https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026 · TTS https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts
