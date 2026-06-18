"""VOICE <-> MEMORY <-> BRAIN end-to-end wiring lock.

Proves MEMORY/RAG is wired into the VOICE path (not only the deck's /api/tell): a wake-
gated utterance flows through the SAME ``utah.voice.agent.handle_utterance`` ->
``utah.core.tell`` -> ``utah.memory`` recall -> ``utah.brain`` path the live UtahVoice.app
uses, and the recalled fact reaches the brain prompt so the spoken answer is grounded in
memory rather than parametric guesswork. See scripts/voice_roundtrip_proof.py for the
human-readable proof transcript (/tmp/ace_voice_roundtrip.log).
"""
from __future__ import annotations

from utah import brain, embed, local, memory, rerank
from utah.objects import ReplySource
from utah.voice import agent
from tests.fakes import FakeEmbedder, FakeStore, ZeroReranker

_FACT = ("Utah's canonical sellable source for the Sovereign product lives at "
         "~/sovereign-live and is packaged directly, not from git.")
_TRANSCRIPT = "ace where does the sovereign sellable source live"


def _seed(monkeypatch):
    store = FakeStore()
    emb = FakeEmbedder()
    memory.set_backend(store)
    embed.set_embedder(emb)
    rerank.set_reranker(ZeroReranker())
    # router must escalate to the memory-grounded brain, never the local lane
    monkeypatch.setattr(local, "set_runner", local.set_runner)
    local.set_runner(lambda *a, **k: (_ for _ in ()).throw(local.LocalUnavailable("off")))
    local.set_stream_runner(lambda *a, **k: (_ for _ in ()).throw(local.LocalUnavailable("off")))
    store.insert(content=_FACT, source="fact", tags=["sovereign"], confidence=0.95,
                 embedding=emb.embed(_FACT), entity_names=["Sovereign", "sovereign-live"],
                 supersede_ids=[])
    return store


def test_voice_turn_grounds_the_brain_in_recalled_memory(monkeypatch):
    """The whole point: a SPOKEN question retrieves the seeded fact via real recall, that
    fact lands in the brain prompt, and the spoken answer is grounded in it. If voice ever
    stopped threading recall into the brain (the /api/tell-only regression), this fails."""
    _seed(monkeypatch)

    seen = {"memory_in_prompt": False}

    def grounded_brain(argv, timeout):
        prompt = "\n".join(str(a) for a in argv)
        if "sovereign-live" in prompt:
            seen["memory_in_prompt"] = True
            return "The Sovereign sellable source lives at ~/sovereign-live."
        return "I don't know."

    brain.set_runner(grounded_brain)

    # recall fires on its own first
    hits = memory.recall("where does the sovereign sellable source live")
    assert hits, "memory/RAG returned no hits — recall not wired"
    assert any("sovereign-live" in h.content for h in hits)

    spoken: list[str] = []
    published: list[str] = []
    started = {"on_start": False}

    def speak_stream(chunks, on_start=None):
        for i, c in enumerate(chunks):
            if i == 0 and on_start:
                on_start()
                started["on_start"] = True
            spoken.append(c)
        return "".join(spoken)

    fired = {"on_speaking": False}

    def on_speaking():
        fired["on_speaking"] = True

    result = agent.handle_utterance(
        _TRANSCRIPT, audio_wake=True, wake_confidence=0.92,
        speak_stream=speak_stream,
        publish=lambda ch, ev: published.append(ch),
        on_speaking=on_speaking,
    )

    assert result is not None, "wake gate ignored the utterance"
    assert result["command"] == "where does the sovereign sellable source live"
    # the brain was grounded in the RECALLED memory (the end-to-end claim)
    assert seen["memory_in_prompt"], "recalled memory never reached the brain prompt via voice"
    # what would actually be SPOKEN is grounded in the fact, not invented
    assert "sovereign-live" in "".join(spoken)
    assert result["source"] in (ReplySource.MEMORY.value, ReplySource.BRAIN.value,
                                ReplySource.LEARNED.value)
    # deck UX: wake orb pulses BEFORE the brain turn, and the turn publishes
    assert published[0] == "wake"
    assert "voice" in published
    # barge anchor: on_start fires exactly when first audio is sent (speaking-state flip),
    # which propagates the caller's on_speaking hook through _speak_safe -> speak_stream.
    assert started["on_start"], "on_start (barge/speaking anchor) never fired"
    assert fired["on_speaking"], "caller's on_speaking hook never reached the speaker"


def test_voice_memory_unavailable_degrades_without_crashing(monkeypatch):
    """If the store is down mid-turn, the voice path must NOT crash — it degrades to a
    brain turn with no context (the brain still no-fabs). Proves the recall failure is
    caught on the voice path, same as the deck."""
    store = _seed(monkeypatch)
    store.fail = True  # store now raises MemoryUnavailable on every call

    brain.set_runner(lambda argv, timeout: "I don't have that in memory right now.")

    spoken: list[str] = []
    result = agent.handle_utterance(
        _TRANSCRIPT, audio_wake=True, wake_confidence=0.92,
        speak_stream=lambda chunks, on_start=None: [spoken.append(c) for c in chunks],
        publish=lambda ch, ev: None,
    )
    # turn still completes (degraded), never raises
    assert result is not None
    assert result["command"]
