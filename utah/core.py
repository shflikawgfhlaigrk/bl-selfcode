"""Utah core — the tiny orchestrator: recall -> ground -> reason -> remember.

Voice and chat both call :func:`tell`; the brain is the Claude CLI (the
agentic harness). This is the whole loop; everything else is a capability
behind it. Every failure branch is handled and the loop never crashes and
never fabricates:

* memory down  -> reason with no context (the brain still no-fabs);
* gate not met -> reason grounded in the recalled hits;
* brain down   -> honest ``ReplySource.UNAVAILABLE`` reply, nothing stored;
* store failed -> the reply still goes out (remember is best-effort).
"""
from __future__ import annotations

import logging
import re
import sys
from collections import deque
from typing import Iterator

from utah import brain, config, failures, local, memory, router
from utah.brain import BrainUnavailable
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable
from utah.objects import Reply, ReplySource
from utah.router import Route

log = logging.getLogger("utah.core")

#: Recent (question, answer) turns — the live conversation thread shared by chat
#: AND voice, so follow-ups ("why?", "prove it") have context. Small + in-memory.
_CONVO: "deque[tuple[str, str]]" = deque(maxlen=6)


def reset_conversation() -> None:
    """Clear the conversation thread (new conversation / tests)."""
    _CONVO.clear()


def _conversation_context() -> str:
    return "\n".join(f"Michael: {q}\nUtah: {a}" for q, a in _CONVO)


def _build_context(hits: list, web: str = "") -> str:
    """Brain context = core identity facts + conversation thread + freshly fetched web
    text + recalled memory. ``web`` (the just-fetched page text from a learn-on-miss)
    is injected as its own labelled block so the retry grounds on the real source text
    in ONE pass — no per-source extraction round-trips, which is the latency win."""
    parts: list[str] = []
    core = memory.core_recall()
    if core:
        parts.append("CORE (always true):\n" + "\n".join(f"- {h.content}" for h in core))
    convo = _conversation_context()
    if convo:
        parts.append("RECENT CONVERSATION:\n" + convo)
    if web:
        parts.append("FRESHLY RESEARCHED (web sources, grounded):\n" + web)
    if hits:
        parts.append("RECALLED MEMORY:\n" + "\n".join(f"- {h.content}" for h in hits))
    return "\n\n".join(parts)


_TURN_ANSWER = re.compile(r"\bA:\s*(.*)$", re.S)


def _present_memory_answer(answer: str, hits: list) -> str:
    """A turn is stored as ``Q: …\\nA: …``; when one is recalled as a confident
    answer, surface just the answer (what Claude would say), not the scaffold."""
    if hits and getattr(hits[0], "source", "") == "turn":
        m = _TURN_ANSWER.search(answer)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return answer


# --------------------------------------------------------------------------
# L1 tier — capabilities + free local models, in FRONT of the paid Claude lane.
# A capability answers from real data; a local model answers quick things free;
# any local miss (refusal / Ollama down) escalates to the brain. (See 20-l1-tier.md)
# --------------------------------------------------------------------------


def _capability_reply(text: str, route: Route, hits: list) -> Reply | None:
    """A deterministic, grounded capability answer — or ``None`` if *route* is not a
    capability. Capability replies are real data (weather, brief, knowledge packs):
    surfaced and threaded for follow-ups, but NOT stored as durable turns (live
    state goes stale; pack text is already durable as facts)."""
    if route is Route.WEATHER:
        from utah.product import weather

        return Reply(text=weather.answer(text), source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.TIME:
        from utah.product import clock

        return Reply(text=clock.now_text(), source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.BRIEF:
        from utah.product import brief

        brief_text = (brief.run(speak_fn=None, can_email=False) or {}).get("brief") or ""
        if brief_text:
            return Reply(text=brief_text, source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.KNOWLEDGE:
        from utah.knowledge import douglas

        pack = douglas.answer(text) or ""
        if pack:
            return Reply(text=pack, source=ReplySource.CAPABILITY, hits=hits)
    return None


def _try_local(text: str, context: str, *, heavy: bool) -> str | None:
    """A local-model answer, or ``None`` to escalate to the brain (a refusal or an
    unavailable Ollama — never a fabrication)."""
    try:
        out = local.think(text, context, heavy=heavy)
    except local.LocalUnavailable as exc:
        log.warning("local lane unavailable, escalating to brain: %s", exc)
        failures.record("local", "unavailable", str(exc))
        return None
    return out if out and not local.is_refusal(out) else None


def _remember_turn(text: str, reply_text: str) -> None:
    """Store one exchange so future recall compounds. Refusals carry nothing
    durable and are skipped; storage is best-effort (the reply already went out)."""
    if not reply_text or local.is_refusal(reply_text):
        return
    try:
        memory.store(f"Q: {text}\nA: {reply_text}", source="turn", confidence=0.5)
    except (MemoryUnavailable, EmbedError, AdmissionDenied) as exc:
        log.warning("could not remember the turn (reply still sent): %s", exc)


# --------------------------------------------------------------------------
# Learn-on-miss — find → understand → remember, then answer.
# When the brain refuses a world-knowledge question because memory is cold, go
# LEARN it: research the web, extract grounded facts into memory through the
# admission gate, then re-reason over the fresh recall. No-fabrication is intact
# (the brain still answers only from CONTEXT — we just populate the context with
# real fetched facts first), and Utah compounds: the next ask is instant recall.
# --------------------------------------------------------------------------


def _learnable(text: str) -> bool:
    """Scope the learn-on-miss loop: enabled, and a world-knowledge question (so
    personal/agentic misses never pay for a web search). The TRIGGER is the brain's
    actual refusal — never a predicted 'is this grounded?', which false-positives on
    a semantically-near but wrong-entity hit (an Everest fact for a Kilimanjaro
    question passed the gate and wrongly suppressed learning). The refusal is ground
    truth: the brain only refuses when its CONTEXT genuinely lacks the answer."""
    return config.LEARN_ON_MISS and router.is_factual_recall(text)


def _learn(text: str) -> str:
    """Fetch real web text for *text* to ground the retry — the FAST path: search +
    parallel fetch, NO per-source brain extraction (that cost ~60s on a cold learn).
    Returns the fetched page text (brain context) or '' on any failure. Best-effort:
    a block / dead fetch / nothing fetched is swallowed → the honest refusal stands."""
    try:
        from utah.product import researcher

        return researcher.gather(text, k=config.LEARN_ON_MISS_SOURCES)
    except Exception as exc:  # noqa: BLE001 — learning is best-effort, never fatal
        log.warning("learn-on-miss gather failed: %s", exc)
        failures.record("learn", "gather_failed", f"{text[:60]}: {exc}")
        return ""


def tell(text: str) -> Reply:
    """One full turn: recall -> ground -> reason -> remember."""
    text = (text or "").strip()
    if not text:
        return Reply(text="I didn't catch that.", source=ReplySource.UNAVAILABLE)

    route = router.route(text)

    # 1. CAPABILITY / KNOWLEDGE first — these are LIVE (weather, brief) or
    #    AUTHORITATIVE (curated packs). They must NOT be shadowed by a stale or
    #    partial memory hit (a memory turn once served day-old weather here).
    cap = _capability_reply(text, route, hits=[])
    if cap is not None:
        _CONVO.append((text, cap.text))
        return cap

    # 2. RECALL + GROUND: answer general factual turns from memory only if confident
    #    (no fabrication). Capabilities already returned above, so this never serves
    #    stale live-state.
    hits: list = []
    try:
        answer, hits = memory.answer(text)
    except MemoryUnavailable as exc:
        log.warning("memory unavailable during recall, degrading: %s", exc)
        failures.record("memory", "unavailable", str(exc))
        answer = None
    if answer is not None:
        clean = _present_memory_answer(answer, hits)
        _CONVO.append((text, clean))
        return Reply(text=clean, source=ReplySource.MEMORY, hits=hits)

    context = _build_context(hits)

    # 3. LOCAL: a free resident model answers quick things; a miss escalates.
    if route in (Route.LOCAL_QUICK, Route.LOCAL_HEAVY):
        local_text = _try_local(text, context, heavy=route is Route.LOCAL_HEAVY)
        if local_text is not None:
            # Threaded for follow-ups, but NOT durably stored: a small local model
            # is not a trusted source of durable facts (it hallucinates), and a
            # stored hallucination poisons recall. Durable memory = the brain +
            # explicit facts + consolidation. (See 20-l1-tier.md, the poisoning fix.)
            _CONVO.append((text, local_text))
            return Reply(text=local_text, source=ReplySource.LOCAL, hits=hits)
        # miss (refusal / Ollama down) → escalate to the brain below.

    # 4. REASON: Claude CLI brain, grounded in the conversation thread + recall.
    try:
        reply_text = brain.think(text, context)
    except BrainUnavailable as exc:
        log.error("brain unavailable: %s", exc)
        failures.record("brain", "unavailable", str(exc))
        return Reply(
            text=f"I don't know — my reasoning brain is unavailable right now ({exc}).",
            source=ReplySource.UNAVAILABLE,
            hits=hits,
        )

    # 4.5 LEARN-ON-MISS: the brain refused a world-knowledge question → fetch the real
    #     web text and re-reason over it in ONE grounded pass. (No-fab intact: the brain
    #     answers only from the fetched CONTEXT. The answer is remembered → compounds.)
    if brain.is_refusal(reply_text) and _learnable(text):
        web = _learn(text)
        if web:
            relearned = brain.think(text, _build_context(hits, web=web))
            if relearned and not brain.is_refusal(relearned):
                _CONVO.append((text, relearned))
                _remember_turn(text, relearned)
                return Reply(text=relearned, source=ReplySource.LEARNED, hits=hits)
            # the web didn't actually answer it → fall through to the honest refusal.

    # 5. REMEMBER (best-effort): store the exchange so future recall compounds.
    #    Refusals ("I don't know…", verbose or not) carry nothing durable → skip.
    if reply_text:
        _CONVO.append((text, reply_text))
    _remember_turn(text, reply_text)

    return Reply(text=reply_text, source=ReplySource.BRAIN, hits=hits)


def tell_stream(text: str) -> Iterator[tuple[str, str]]:
    """Streaming turn: recall → ground → stream the brain's reasoning+answer →
    remember. Same no-fabrication contract as :func:`tell`, but yields ordered
    ``(channel, chunk)`` events so chat AND voice can show reasoning live like
    Claude. Channels: ``source`` (memory|capability|local|brain|unavailable),
    ``thinking``, ``answer``, ``done`` (final answer text). Never raises into the
    caller.
    """
    text = (text or "").strip()
    if not text:
        yield ("source", "unavailable")
        yield ("answer", "I didn't catch that.")
        yield ("done", "I didn't catch that.")
        return

    # 1. RECALL — pull memory as GROUNDING for the tiers. Memory feeds cognition;
    #    it never short-circuits the stream. The interactive turn ALWAYS reasons so
    #    the chat box (and voice) show thinking live, like Claude — even when the
    #    answer is "known". (The non-streaming tell() keeps the verbatim fast-path.)
    hits: list = []
    try:
        hits = memory.recall(text)
    except MemoryUnavailable as exc:
        log.warning("memory unavailable during recall, degrading: %s", exc)
        failures.record("memory", "unavailable", str(exc))
        hits = []
    context = _build_context(hits)
    route = router.route(text)

    # 1.5 L1 CAPABILITY — deterministic grounded answer (real data; not stored).
    cap = _capability_reply(text, route, hits)
    if cap is not None:
        yield ("source", "capability")
        yield ("answer", cap.text)
        _CONVO.append((text, cap.text))
        yield ("done", cap.text)
        return

    # 1.6 L1 LOCAL — free resident model; stream thinking live, escalate on a miss.
    if route in (Route.LOCAL_QUICK, Route.LOCAL_HEAVY):
        answered = yield from _stream_local(text, context, heavy=route is Route.LOCAL_HEAVY)
        if answered:
            return
        # miss (refusal / Ollama down) → escalate to the brain below.

    # 2. REASON — stream the Claude CLI brain, grounded in the conversation + hits.
    #    Thinking always streams live (chat box reasons like Claude). For a factual
    #    question the ANSWER is BUFFERED so a refusal can trigger learn-on-miss before
    #    anything is committed to the box — no "I don't know" flash that we'd correct.
    yield ("source", "brain")
    learnable = _learnable(text)
    parts: list[str] = []
    try:
        for channel, chunk in brain.think_stream(text, context):
            if channel == "answer":
                parts.append(chunk)
                if not learnable:
                    yield (channel, chunk)  # non-factual: stream the answer live
            else:
                yield (channel, chunk)      # thinking always streams live
    except BrainUnavailable as exc:
        log.error("brain unavailable: %s", exc)
        failures.record("brain", "unavailable", str(exc))
        msg = f"I don't know — my reasoning brain is unavailable right now ({exc})."
        yield ("source", "unavailable")
        yield ("answer", msg)
        yield ("done", msg)
        return
    reply_text = "".join(parts).strip()

    # 2.5 LEARN-ON-MISS — the brain REFUSED a factual question → find it on the web,
    #     ground on the COMPLETE fresh evidence, and reason again. (No-fab intact.)
    if learnable and brain.is_refusal(reply_text):
        yield ("source", "learned")
        yield ("thinking", "I don't have that yet — searching the web and learning it…\n")
        web = _learn(text)
        if web:
            relearned: list[str] = []
            try:
                for channel, chunk in brain.think_stream(text, _build_context(hits, web=web)):
                    if channel == "answer":
                        relearned.append(chunk)
                    else:
                        yield (channel, chunk)  # second-pass thinking live
            except BrainUnavailable as exc:
                log.error("brain unavailable on learn retry: %s", exc)
            grounded = "".join(relearned).strip()
            if grounded and not brain.is_refusal(grounded):
                reply_text = grounded  # commit the grounded answer
        # else (nothing learned / retry refused) → the honest refusal stands.
    if learnable:
        yield ("answer", reply_text)  # commit the (buffered or grounded) answer once

    # 3. REMEMBER (best-effort) — skip refusals (nothing durable).
    if reply_text:
        _CONVO.append((text, reply_text))  # conversation thread (incl. honest refusals)
    _remember_turn(text, reply_text)
    yield ("done", reply_text)


def _stream_local(text: str, context: str, *, heavy: bool) -> Iterator[tuple[str, str]]:
    """Stream a local model's thinking live, buffer its answer, then decide at the
    end. Returns ``True`` (via ``StopIteration.value``) when it produced a real,
    non-refusal answer and emitted a terminal ``("done", …)``; ``False`` to escalate
    to the brain (no terminal emitted). The quick model has no thinking, so a miss
    escalates seamlessly; the heavy reasoner may have streamed ``thinking`` before a
    rare refusal — honest, and the brain then supplies the answer. The buffered
    answer is flushed in one event (sub-second for the quick model), which keeps
    escalation clean."""
    answer_parts: list[str] = []
    source_sent = False
    try:
        for channel, chunk in local.think_stream(text, context, heavy=heavy):
            if channel == "answer":
                answer_parts.append(chunk)
            else:  # thinking — show it live
                if not source_sent:
                    yield ("source", "local")
                    source_sent = True
                yield ("thinking", chunk)
    except local.LocalUnavailable as exc:
        log.warning("local stream unavailable, escalating to brain: %s", exc)
        failures.record("local", "unavailable", str(exc))
        return False
    answer = "".join(answer_parts).strip()
    if not answer or local.is_refusal(answer):
        return False
    if not source_sent:
        yield ("source", "local")
    yield ("answer", answer)
    # Threaded for follow-ups, but NOT durably stored — a small local model is not a
    # trusted source of durable facts (see 20-l1-tier.md, the poisoning fix).
    _CONVO.append((text, answer))
    yield ("done", answer)
    return True


_USAGE = """\
usage: python -m utah.core [--init | --reset | --remember TEXT... |
                            --consolidate | --decay | QUESTION...]
With no arguments, the question is read from stdin."""


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code."""
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else ""
    try:
        if cmd in {"-h", "--help"}:
            print(_USAGE)
            return 0
        if cmd == "--init":
            memory.init()
            print("utah: memory schema ready")
            return 0
        if cmd == "--reset":
            memory.reset()
            print("utah: memory reset (dev clean slate)")
            return 0
        if cmd == "--remember":
            content = " ".join(args[1:]).strip()
            if not content:
                print("utah: nothing to remember", file=sys.stderr)
                return 2
            result = memory.store(content, source="fact", confidence=0.8)
            print(f"stored id {result.id} ({result.action.value})")
            return 0
        if cmd == "--consolidate":
            from utah.consolidate import consolidate

            report = consolidate()
            print(
                f"utah consolidate: turns={report.turns_seen} "
                f"promoted={report.facts_promoted} skipped={report.facts_skipped} "
                f"brain_failures={report.brain_failures} archived={report.archived}"
            )
            return 0
        if cmd == "--decay":
            print(f"utah decay: archived {memory.decay()}")
            return 0

        question = " ".join(args).strip() or sys.stdin.read().strip()
        if not question:
            print(_USAGE, file=sys.stderr)
            return 2
        reply = tell(question)
        print(f"[{reply.source.value}] {reply.text}")
        return 0
    except (MemoryUnavailable, EmbedError, AdmissionDenied, BrainUnavailable) as exc:
        print(f"utah: {exc}", file=sys.stderr)
        return 1
    finally:
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
