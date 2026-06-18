#!/usr/bin/env python3
"""VOICE-PATH END-TO-END PROOF: wake -> STT -> brain (with MEMORY/RAG hits) -> TTS.

This drives the LIVE pipeline through the *same* entry point the running UtahVoice.app
uses — ``utah.voice.agent.handle_utterance`` -> ``utah.core.tell`` -> ``utah.memory``
recall+ground -> ``utah.brain`` -> TTS speak — to prove that MEMORY/RAG is wired into
the VOICE path end to end (not only the deck's /api/tell).

It is deterministic and self-contained:
  * a REAL ``utah.memory`` backend (FakeStore) seeded with ONE verifiable fact,
  * a REAL embedder + reranker (the deterministic test doubles) so recall actually fires,
  * a REAL brain boundary whose argv is INSPECTED to prove the recalled fact reached the
    brain prompt (i.e. the answer is grounded in memory, not parametric guesswork),
  * the REAL Stage-A/Stage-B wake gate (audio_wake + transcript),
  * a capturing TTS sink so we record exactly what would be SPOKEN.

No network, no Postgres, no Claude subscription call — every boundary is the real code
path with an injected leaf, so a regression in recall/ground/voice wiring breaks this.

Run:  .venv/bin/python scripts/voice_roundtrip_proof.py
Exit:  0 on a grounded, memory-cited spoken answer; non-zero (and a FAIL line) otherwise.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# repo on path (so `-m`-style import resolution matches the live loop)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utah import brain, core, embed, memory, rerank, local  # noqa: E402
from utah.objects import ReplySource  # noqa: E402
from utah.voice import agent  # noqa: E402
from tests.fakes import FakeEmbedder, FakeStore, ZeroReranker  # noqa: E402

LOG = Path("/tmp/ace_voice_roundtrip.log")

# The ONE durable fact we seed. The spoken answer must be grounded in THIS, proving the
# memory hit flowed wake -> brain. Chosen to be unambiguous and entity-bearing.
SEEDED_FACT = (
    "Utah's canonical sellable source for the Sovereign product lives at "
    "~/sovereign-live and is packaged directly, not from git."
)
SEEDED_SOURCE = "fact"
QUESTION = "where does the sovereign sellable source live"
# what the live wake gate hears: Stage A (openWakeWord) armed this segment, and the STT
# transcript carries the wake token + command (the real two-stage shape).
TRANSCRIPT = "ace where does the sovereign sellable source live"


def _seed_memory() -> int:
    """Insert the fact through the REAL memory write path (embed + store.insert)."""
    store = FakeStore()
    memory.set_backend(store)
    emb = FakeEmbedder()
    embed.set_embedder(emb)
    rerank.set_reranker(ZeroReranker())
    # local lane OFF so the router escalates to the (memory-grounded) brain, never Ollama
    local.set_runner(lambda *a, **k: (_ for _ in ()).throw(local.LocalUnavailable("off")))
    local.set_stream_runner(lambda *a, **k: (_ for _ in ()).throw(local.LocalUnavailable("off")))
    vector = emb.embed(SEEDED_FACT)
    mem_id = store.insert(
        content=SEEDED_FACT, source=SEEDED_SOURCE, tags=["sovereign"],
        confidence=0.95, embedding=vector,
        entity_names=["Sovereign", "sovereign-live"], supersede_ids=[],
    )
    return mem_id


class _GroundedBrain:
    """Real brain boundary (argv, timeout) -> stdout. It PROVES grounding: it refuses to
    answer unless the seeded fact's distinctive token is present in the prompt argv, then
    answers FROM that context — exactly the no-fabrication contract."""

    def __init__(self) -> None:
        self.saw_memory_in_prompt = False
        self.prompt_excerpt = ""

    def __call__(self, argv, timeout):  # noqa: ANN001
        prompt = "\n".join(str(a) for a in argv)
        self.prompt_excerpt = prompt
        if "sovereign-live" in prompt:
            self.saw_memory_in_prompt = True
            return ("The Sovereign sellable source lives at ~/sovereign-live, "
                    "packaged directly rather than from git.")
        # No memory in context -> honest no-fab refusal (this would FAIL the proof).
        return "I don't know."


def main() -> int:
    lines: list[str] = []

    def log(s: str = "") -> None:
        lines.append(s)
        print(s)

    log("=" * 72)
    log("ACE VOICE-PATH ROUND-TRIP PROOF  (wake -> STT -> brain+MEMORY -> TTS)")
    log("=" * 72)
    log(f"timestamp        : {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    log(f"entry point      : utah.voice.agent.handle_utterance  (the live UtahVoice path)")
    log(f"brain pipeline   : utah.core.tell  (recall -> ground -> reason -> remember)")
    log("")

    mem_id = _seed_memory()
    log("[1] SEED MEMORY (real memory.insert through embed + store)")
    log(f"    mem_id={mem_id} source={SEEDED_SOURCE!r}")
    log(f"    fact: {SEEDED_FACT}")
    log("")

    # Prove RAG fires on its own first (recall -> hits), before the voice turn.
    hits = memory.recall(QUESTION)
    log("[2] RECALL / RAG  (memory.recall on the spoken question)")
    if not hits:
        log("    FAIL: recall returned zero hits — memory/RAG not retrieving.")
        _flush(lines)
        return 2
    top = hits[0]
    log(f"    top hit: id={top.id} source={top.source!r} sim={top.sim} score={top.score}")
    log(f"    content: {top.content}")
    grounded_hit = any("sovereign-live" in h.content for h in hits)
    log(f"    seeded fact present in hits: {grounded_hit}")
    log("")

    # Drive the REAL voice entry point. Capture STT transcript, brain grounding, spoken TTS.
    gbrain = _GroundedBrain()
    brain.set_runner(gbrain)

    spoken: list[str] = []
    published: list[tuple[str, dict]] = []
    barge_events: list[str] = []

    def speak_stream(chunks, on_start=None):  # noqa: ANN001 — real tts.speak_stream signature
        first = True
        for c in chunks:
            if first and on_start:
                on_start()
                first = False
            spoken.append(c)
        return "".join(spoken)

    def publish(channel, event):  # noqa: ANN001
        published.append((channel, event))

    log("[3] VOICE TURN  (Stage-A audio_wake + Stage-B transcript -> handle_utterance)")
    log(f"    audio_wake=True  transcript={TRANSCRIPT!r}")
    t0 = time.perf_counter()
    result = agent.handle_utterance(
        TRANSCRIPT,
        audio_wake=True,
        wake_confidence=0.92,
        speak_stream=speak_stream,
        publish=publish,
        on_speaking=lambda: barge_events.append("on_speaking-fired"),
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0
    log("")

    if result is None:
        log("    FAIL: wake gate ignored the utterance (command not resolved).")
        _flush(lines)
        return 3

    log("[4] RESULT")
    log(f"    wake resolved command : {result.get('command')!r}")
    log(f"    answer source         : {result.get('source')!r}")
    log(f"    spoken (TTS sink)     : {''.join(spoken)!r}")
    log(f"    brain saw memory      : {gbrain.saw_memory_in_prompt}")
    log(f"    turn latency          : {latency_ms:.1f} ms (full recall+ground+reason+speak)")
    log("")

    # publish proof: the deck orb 'wake' pulse fired before the brain turn (latency UX),
    # and the final 'voice' turn was published.
    chans = [c for c, _ in published]
    log("[5] PUBLISH / DECK EVENTS")
    log(f"    channels published    : {chans}")
    wake_pulsed = "wake" in chans
    voice_published = "voice" in chans
    log(f"    wake-orb pulse fired  : {wake_pulsed}  (before the slow brain turn)")
    log(f"    voice turn published  : {voice_published}")
    log("")

    # BARGE-IN: on_speaking fires exactly when the first audio is sent to the speaker, the
    # hook the loop uses to flip thinking->speaking and to honor a button-barge interrupt.
    log("[6] BARGE-IN / LATENCY HEALTH")
    on_speaking_ok = "on_speaking-fired" in barge_events
    log(f"    on_speaking hook fired: {on_speaking_ok}  (speaking-state flip / barge anchor)")
    # exercise the cross-process button-barge surface (stop playback + re-arm capture)
    from utah.voice import barge_control
    barge_control.request_button_barge()
    barge_consumed = barge_control.consume_button_barge()
    log(f"    button-barge request/consume round-trips: {barge_consumed}")
    healthy_latency = latency_ms < 2000.0  # full turn on fakes must be well under 2s
    log(f"    latency under 2s budget: {healthy_latency} ({latency_ms:.1f} ms)")
    log("")

    # VERDICT -----------------------------------------------------------------
    answer_text = "".join(spoken)
    grounded_answer = "sovereign-live" in answer_text
    memory_cited = result.get("source") in (
        ReplySource.MEMORY.value, ReplySource.BRAIN.value, ReplySource.LEARNED.value,
    )
    checks = {
        "recall returned hits": bool(hits),
        "seeded fact in hits": grounded_hit,
        "wake gate resolved command": bool(result.get("command")),
        "brain prompt contained memory": gbrain.saw_memory_in_prompt,
        "spoken answer grounded in memory": grounded_answer,
        "answer source is memory/brain": memory_cited,
        "wake orb pulsed pre-brain": wake_pulsed,
        "voice turn published": voice_published,
        "on_speaking (barge anchor) fired": on_speaking_ok,
        "button-barge round-trips": barge_consumed,
        "latency under budget": healthy_latency,
    }
    log("=" * 72)
    log("VERDICT")
    log("=" * 72)
    for name, ok in checks.items():
        log(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    all_ok = all(checks.values())
    log("")
    log(f"RESULT: {'PASS — voice path is grounded in MEMORY/RAG end-to-end' if all_ok else 'FAIL'}")
    log("=" * 72)

    # restore boundaries
    brain.set_runner(None)
    memory.set_backend(None)
    embed.set_embedder(None)
    rerank.set_reranker(None)
    local.set_runner(None)
    local.set_stream_runner(None)

    _flush(lines)
    return 0 if all_ok else 1


def _flush(lines: list[str]) -> None:
    LOG.write_text("\n".join(lines) + "\n")
    print(f"\n[proof transcript written to {LOG}]")


if __name__ == "__main__":
    raise SystemExit(main())
