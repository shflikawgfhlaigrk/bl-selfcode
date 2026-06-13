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

import json
import logging
import re
import sys
from collections import deque
from typing import Iterator

from utah import brain, config, failures, local, memory, router, social
from utah.brain import BrainRateLimited, BrainUnavailable
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
    # The assistant turn is labelled "Ace:" — one consistent identity (Utah is the
    # system; Ace is who Michael talks to). The brain's PERSONA says "You are Ace",
    # so the thread it reads back must agree, or the name splits and reads non-human.
    return "\n".join(f"Michael: {q}\nAce: {a}" for q, a in _CONVO)


def _self_model_facts(self_model=None) -> str:
    """Utah's live self-model (:func:`utah.introspect.self_model`) rendered as grounding
    bullet facts for a self/project question — the brain answers "what are you / what can
    you do" from REAL daemon + memory state, never parametric guesswork. ``self_model``
    is injectable (defaults to the live introspect call). Never raises; a failed
    introspection degrades HONESTLY to an explicit "self-model unavailable" line (logged
    + documented) so the brain knows the live state is missing — it is never silently
    dropped."""
    if self_model is None:
        from utah import introspect

        self_model = introspect.self_model
    try:
        m = self_model() or {}
    except Exception as exc:  # noqa: BLE001 — grounding must never break a reply
        log.warning("introspect self-model unavailable for grounding: %s", exc)
        failures.record("introspect", "self_model_failed", str(exc))
        return "- live self-model unavailable right now (introspection failed)"
    mem_counts = m.get("memory") or {}
    mem_line = ", ".join(f"{k}={v}" for k, v in mem_counts.items()) or "counts unavailable"
    return "\n".join([
        f"- {m.get('identity', 'Utah')}",
        f"- Capabilities ({m.get('capability_count', 0)}): "
        + ", ".join(m.get("capabilities", [])),
        f"- Daemon: {'up' if m.get('daemon_up') else 'DOWN (status unreachable)'}",
        f"- Memory: {mem_line}",
    ])


def _build_context(hits: list, web: str = "", *, text: str = "") -> str:
    """Brain context = core identity facts + (for a self/project question) the live
    introspection self-model + conversation thread + freshly fetched web text + recalled
    memory. ``web`` (the just-fetched page text from a learn-on-miss) is injected as its
    own labelled block so the retry grounds on the real source text in ONE pass — no
    per-source extraction round-trips, which is the latency win. ``text`` is the question
    being answered: when it is a self/project turn (router.is_self_or_project), the
    self-model block grounds "who/what are you" in real daemon + memory state."""
    parts: list[str] = []
    core = memory.core_recall()
    if core:
        parts.append("CORE (always true):\n" + "\n".join(f"- {h.content}" for h in core))
    if text and router.is_self_or_project(text):
        parts.append(
            "YOUR LIVE SELF-MODEL (introspection — real daemon/memory state):\n"
            + _self_model_facts())
    convo = _conversation_context()
    if convo:
        parts.append("RECENT CONVERSATION:\n" + convo)
    if web:
        parts.append("FRESHLY RESEARCHED (web sources, grounded):\n" + web)
    if hits:
        # Code is YOUR OWN committed source — present it in its own AUTHORITATIVE block,
        # not buried in generic "RECALLED MEMORY" bullets next to chatty turns. Without
        # this the brain treated indexed code as untrusted memory and refused to name its
        # own files/functions ("oauth.py isn't in my context") even with the code in hand.
        code_hits = [h for h in hits if getattr(h, "source", "") == "code"]
        other_hits = [h for h in hits if getattr(h, "source", "") != "code"]
        if code_hits:
            parts.append(
                "YOUR OWN SOURCE CODE (committed + indexed — authoritative; you MAY name "
                "these files, functions, and symbols directly):\n"
                + "\n".join(f"- {h.content}" for h in code_hits))
        if other_hits:
            parts.append("RECALLED MEMORY:\n" + "\n".join(f"- {h.content}" for h in other_hits))
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
    if route is Route.ACTION:
        # A COMMAND that RUNS a real capability (rerun leads/outreach/probate/engines/
        # research) and reports the REAL returned numbers — never a fabricated "done".
        # Data-populating actions fire immediately; the SEND action runs inside the
        # code's own CAN-SPAM / business-hours / deliverability send-gates. Surfaced as a
        # grounded CAPABILITY reply (real numbers, live state) — not stored as a durable
        # turn (the counts go stale, same as the other capability replies).
        from utah import actions

        return Reply(text=actions.run(text), source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.LEADS:
        from utah.product import leads_status

        return Reply(text=leads_status.answer(text), source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.MAIL:
        from utah.product import mail_status

        return Reply(text=mail_status.answer(text), source=ReplySource.CAPABILITY, hits=hits)
    if route is Route.NEWS:
        # A news/headlines question → the researcher-backed news capability (real DDG
        # search + fetch + grounded extraction; the facts land in memory through the
        # admission gate, so the next ask compounds). A blocked search / empty web
        # degrades to an honest "couldn't pull news" — never an invented headline.
        # Live data → CAPABILITY, not stored as a durable turn (same as weather/brief).
        from utah.product import news

        return Reply(text=news.answer(text), source=ReplySource.CAPABILITY, hits=hits)
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


def _is_substantive_turn(text: str) -> bool:
    """True when a question is worth remembering as a durable fact.

    The mic is always on, so room speech and grunts ("No,no,no…", "uh uh", "?",
    a single word) get transcribed and would otherwise be stored as a
    confidence-0.5 ``turn`` that recalls #1 on the next ask — a self-answer
    confabulation loop. Require real content: ≥2 word-tokens AND ≥2 *distinct*
    ones (rejects a single repeated grunt)."""
    words = re.findall(r"[a-z0-9]{2,}", (text or "").lower())
    return len(words) >= 2 and len(set(words)) >= 2


def _remember_turn(text: str, reply_text: str) -> None:
    """Store one exchange so future recall compounds. Refusals carry nothing
    durable and are skipped; non-substantive room speech is skipped so it never
    becomes a recallable fact; storage is best-effort (the reply already went out)."""
    if not reply_text or local.is_refusal(reply_text):
        return
    if not _is_substantive_turn(text):
        log.debug("not remembering non-substantive turn: %r", (text or "")[:40])
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


def _memory_grounds(text: str, hits: list) -> bool:
    """True if the best recalled hit confidently AND on-entity answers *text* — the same
    decision :func:`memory.answer` makes, computed on already-recalled hits so the
    streaming path can choose learn-first WITHOUT a second recall. When this is False
    for a factual question, the brain would refuse (no-fab), so we skip that round-trip
    and learn straight away."""
    if not hits:
        return False
    best = hits[0]
    if not memory.passes_gate(getattr(best, "sim", 0.0), memory.lexical_overlap(text, best.content)):
        return False
    return memory.entity_grounds(text, best.content) is not False


def _vet_attribution(question: str, answer: str, context: str) -> str:
    """Structural no-fab check on a brain answer (audit TIER3). Returns the answer (advisory
    default) or a refusal (strict mode) when a numeric claim isn't supported by CONTEXT.
    Records an audit event on an unsupported claim either way. Never raises."""
    try:
        from utah import attribution

        vetted, report = attribution.vet_answer(
            answer, context, strict=config.BRAIN_ATTRIBUTION_STRICT)
        if not report.supported:
            failures.record(
                "brain", "unsupported_claim",
                f"{question[:50]}: numbers not in context {report.unsupported_numbers} "
                f"(strict={config.BRAIN_ATTRIBUTION_STRICT})")
        return vetted
    except Exception as exc:  # noqa: BLE001 — the check must never break a reply
        log.debug("attribution check skipped: %s", exc)
        return answer


def _stream_brain_buffered(
    text: str, context: str, *, want_thinking: bool = True
) -> Iterator[tuple[str, str]]:
    """Stream the brain with thinking live and the answer BUFFERED; return the answer
    text via ``StopIteration.value`` (``""`` on :class:`BrainUnavailable`, logged). The
    caller decides whether/how to commit the answer (e.g. learn on a refusal first)."""
    parts: list[str] = []
    try:
        for channel, chunk in brain.think_stream(text, context, want_thinking=want_thinking):
            if channel == "answer":
                parts.append(chunk)
            else:
                yield (channel, chunk)
    except BrainRateLimited as exc:
        log.warning("brain rate-limited: %s", exc)
        failures.record("brain", "rate_limited", str(exc))
        return ""
    except BrainUnavailable as exc:
        log.error("brain unavailable: %s", exc)
        failures.record("brain", "unavailable", str(exc))
        return ""
    return "".join(parts).strip()


def _prelude(text: str) -> "tuple[str, Route | None, Reply | None]":
    """Shared first stage for :func:`tell` and :func:`tell_stream` — the routing that
    once lived (and could drift) in both. Normalize the input, route it, and resolve the
    two INSTANT lanes both paths answer identically before any recall:

    * empty input → a finished ``UNAVAILABLE`` "I didn't catch that." reply;
    * a whole-message social pleasantry → a finished ``SOCIAL`` canned reply;
    * a deterministic capability (weather/time/brief/knowledge) → a finished ``CAPABILITY``.

    Returns ``(text, route, instant)``. ``instant`` is the finished :class:`Reply` when one
    of those lanes matched (the caller emits it in its own shape — return vs yield — and
    threads it into ``_CONVO`` unless it is the empty-input UNAVAILABLE); otherwise ``None``,
    meaning the caller proceeds to recall + reason."""
    text = (text or "").strip()
    if not text:
        return text, None, Reply(text="I didn't catch that.", source=ReplySource.UNAVAILABLE)
    route = router.route(text)
    if route is Route.SOCIAL:
        canned = social.reply(text)
        if canned:
            return text, route, Reply(text=canned, source=ReplySource.SOCIAL)
    cap = _capability_reply(text, route, hits=[])
    if cap is not None:
        return text, route, cap
    return text, route, None


def tell(text: str, *, persist: bool = True) -> Reply:
    """One full turn: recall -> ground -> reason -> remember.

    ``persist=False`` skips the durable turn write — for the diagnostic CLI (``utah tell``),
    so probing the brain from a terminal never pollutes recall. A probe like "name the
    function in oauth.py" otherwise stores a turn that near-verbatim echoes future code
    questions and out-ranks the actual code chunk. Real conversation (voice/web) keeps
    persist=True."""
    # 0–1. INSTANT lanes (empty / social / capability) — shared with tell_stream via
    #      _prelude so the routing can't drift between the two paths. Social + capability
    #      are threaded for follow-up continuity but never stored (live state / pleasantry).
    text, route, instant = _prelude(text)
    if instant is not None:
        if instant.source is not ReplySource.UNAVAILABLE:
            _CONVO.append((text, instant.text))
        return instant

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

    context = _build_context(hits, text=text)

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

    # 3.5 LEARN-FIRST: a factual question that memory could not confidently answer
    #     (we are past the memory gate) WILL be refused by the brain (no-fab, cold) —
    #     so skip that certain round-trip: fetch the web and answer in ONE grounded
    #     pass. Falls through to the plain brain below if the web yields nothing or the
    #     brain still can't answer it, so the honest "I don't know" is preserved.
    if _learnable(text):
        web = _learn(text)
        if web:
            try:
                grounded = brain.think(text, _build_context(hits, web=web, text=text))
            except BrainUnavailable:
                grounded = ""
            if grounded and not brain.is_refusal(grounded):
                _CONVO.append((text, grounded))
                if persist:
                    _remember_turn(text, grounded)
                return Reply(text=grounded, source=ReplySource.LEARNED, hits=hits)

    # 4. REASON: Claude CLI brain, grounded in the conversation thread + recall.
    try:
        reply_text = brain.think(text, context)
    except BrainRateLimited as exc:
        # A subscription limit is TRANSIENT and expected (shared Claude plan), not an
        # outage — record it as such so the AUDIT panel/alerts don't read a routine
        # rate-limit as system breakage, and tell Michael honestly when it clears.
        log.warning("brain rate-limited: %s", exc)
        failures.record("brain", "rate_limited", str(exc))
        return Reply(text=f"My reasoning brain is rate-limited right now ({exc}).",
                     source=ReplySource.UNAVAILABLE, hits=hits)
    except BrainUnavailable as exc:
        log.error("brain unavailable: %s", exc)
        failures.record("brain", "unavailable", str(exc))
        return Reply(
            text=f"I don't know — my reasoning brain is unavailable right now ({exc}).",
            source=ReplySource.UNAVAILABLE,
            hits=hits,
        )

    # 4.5 ATTRIBUTION PROOF: verify the answer's salient numbers trace to CONTEXT. Advisory
    #     by default (records an unsupported-claim audit event); strict mode downgrades an
    #     unsupported answer to a refusal — no-fab as a proven per-answer property, not just
    #     a prompted intention. Refusals/empty answers are trivially supported.
    reply_text = _vet_attribution(text, reply_text, context)

    # 5. REMEMBER (best-effort): store the exchange so future recall compounds.
    #    Refusals ("I don't know…", verbose or not) carry nothing durable → skip.
    if reply_text:
        _CONVO.append((text, reply_text))
    if persist:
        _remember_turn(text, reply_text)

    return Reply(text=reply_text, source=ReplySource.BRAIN, hits=hits)


def tell_stream(text: str, *, want_thinking: bool = True, voice: bool = False) -> Iterator[tuple[str, str]]:
    """Streaming turn: recall → ground → stream the brain's reasoning+answer →
    remember. Same no-fabrication contract as :func:`tell`, but yields ordered
    ``(channel, chunk)`` events so chat AND voice can show reasoning live like
    Claude. Channels: ``source`` (memory|capability|local|brain|unavailable),
    ``thinking``, ``answer``, ``done`` (final answer text). Never raises into the
    caller.

    ``want_thinking`` (default True) drives the live "reasoning like Claude" chat UX.
    VOICE passes ``False``: it never speaks the thinking block, so requesting it is pure
    latency before first audio — see :func:`brain.think_stream`.

    ``voice=True`` skips the grounding transparency event (voice never renders it).
    Capability/social routing matches :func:`tell` — BEFORE recall — so weather/time
    do not pay a Postgres vector round-trip on the hot path.
    """
    # 0–1. INSTANT lanes (empty / social / capability) — shared with tell() via _prelude
    #      so routing never drifts between the two paths. The emit differs (yield vs
    #      return); social + capability are threaded, the empty-input UNAVAILABLE is not.
    text, route, instant = _prelude(text)
    if instant is not None:
        yield ("source", instant.source.value)
        yield ("answer", instant.text)
        if instant.source is not ReplySource.UNAVAILABLE:
            _CONVO.append((text, instant.text))
        yield ("done", instant.text)
        return

    # 2. RECALL — grounding for reasoning tiers only (past the instant lanes above).
    hits: list = []
    try:
        hits = memory.recall(text)
    except MemoryUnavailable as exc:
        log.warning("memory unavailable during recall, degrading: %s", exc)
        failures.record("memory", "unavailable", str(exc))
        hits = []
    context = _build_context(hits, text=text)

    # 2.5 GROUNDING (transparency) — surface the REAL recalled memory the reasoning
    #      tiers (local/learn/brain) are about to stand on, so the chat box can render
    #      "grounded in N facts" with a drill-down into the exact rows. Capabilities
    #      already returned above (they are deterministic, not memory-grounded), so this
    #      reflects only reasoning turns. Empty recall → no event (cold; the brain then
    #      says "I don't know" or learns). Never fabricated — these are live rows.
    #      Voice skips this — it never renders the drill-down, only latency.
    if hits and not voice:
        yield ("grounding", json.dumps([
            {"id": getattr(h, "id", None),
             "source": getattr(h, "source", "") or "",
             "sim": round(float(getattr(h, "sim", 0.0) or 0.0), 3),
             "content": (getattr(h, "content", "") or "")[:200]}
            for h in hits[:5]
        ]))

    # 1.6 L1 LOCAL — free resident model; stream thinking live, escalate on a miss.
    if route in (Route.LOCAL_QUICK, Route.LOCAL_HEAVY):
        answered = yield from _stream_local(text, context, heavy=route is Route.LOCAL_HEAVY)
        if answered:
            return
        # miss (refusal / Ollama down) → escalate to the brain below.

    learnable = _learnable(text)

    # 1.7 LEARN-FIRST — a factual question with NO entity-grounded memory hit WILL be
    #     refused by the brain (no-fab, cold). Skip that certain round-trip: fetch the
    #     web and stream a grounded answer directly (the cold-learn latency win for
    #     chat/voice). Falls through to the normal brain pass if the web yields nothing.
    if learnable and not voice and not _memory_grounds(text, hits):
        # VOICE never does live web research: it's 13-60s, and on a mis-heard or
        # genuinely-unknown command that turns a turn into a dead-air hang (the
        # "Blun." -> 58s learn loop that made voice feel broken). Voice instead
        # refuses fast and warmly (the brain's "I don't know." + offer is spoken in
        # ~2s); chat keeps learn-on-miss, where a "searching the web…" wait is fine.
        web = _learn(text)
        if web:
            yield ("source", "learned")
            yield ("thinking", "I don't have that yet — searching the web and learning it…\n")
            grounded = yield from _stream_brain_buffered(
                text, _build_context(hits, web=web, text=text), want_thinking=want_thinking)
            if grounded and not brain.is_refusal(grounded):
                yield ("answer", grounded)
                _CONVO.append((text, grounded))
                _remember_turn(text, grounded)
                yield ("done", grounded)
                return
            # the web didn't answer it → fall through to the honest brain pass below.

    # 2. REASON — stream the Claude CLI brain, grounded in the conversation + hits.
    #    Thinking always streams live (chat box reasons like Claude). For a factual
    #    question the ANSWER is BUFFERED so a refusal can trigger the learn fallback
    #    before anything is committed to the box — no "I don't know" flash to correct.
    yield ("source", "brain")
    parts: list[str] = []
    try:
        for channel, chunk in brain.think_stream(
                text, context, want_thinking=want_thinking, brief=voice):
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

    # 2.5 LEARN-ON-MISS fallback — a factual question that LOOKED memory-grounded but
    #     the brain still refused (learn-first did not run). Find it on the web and
    #     reason again. (No-fab intact; learn-first already covers the cold case.)
    if learnable and not voice and brain.is_refusal(reply_text):
        yield ("source", "learned")
        yield ("thinking", "I don't have that yet — searching the web and learning it…\n")
        web = _learn(text)
        if web:
            grounded = yield from _stream_brain_buffered(
                text, _build_context(hits, web=web, text=text), want_thinking=want_thinking)
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
