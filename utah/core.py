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
from typing import Iterator

from utah import brain, failures, memory
from utah.brain import BrainUnavailable
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable
from utah.objects import Reply, ReplySource

log = logging.getLogger("utah.core")

_TURN_ANSWER = re.compile(r"\bA:\s*(.*)$", re.S)


def _present_memory_answer(answer: str, hits: list) -> str:
    """A turn is stored as ``Q: …\\nA: …``; when one is recalled as a confident
    answer, surface just the answer (what Claude would say), not the scaffold."""
    if hits and getattr(hits[0], "source", "") == "turn":
        m = _TURN_ANSWER.search(answer)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return answer


def tell(text: str) -> Reply:
    """One full turn: recall -> ground -> reason -> remember."""
    text = (text or "").strip()
    if not text:
        return Reply(text="I didn't catch that.", source=ReplySource.UNAVAILABLE)

    # 1. RECALL + GROUND: answer from memory only if confident (no fabrication).
    hits: list = []
    try:
        answer, hits = memory.answer(text)
    except MemoryUnavailable as exc:
        log.warning("memory unavailable during recall, degrading: %s", exc)
        failures.record("memory", "unavailable", str(exc))
        answer = None
    if answer is not None:
        return Reply(text=_present_memory_answer(answer, hits), source=ReplySource.MEMORY, hits=hits)

    # 2. REASON: Claude CLI brain, grounded in whatever context we recalled.
    context = "\n".join(f"- {h.content}" for h in hits)
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

    # 3. REMEMBER (best-effort): store the exchange so future recall compounds.
    #    Refusals ("I don't know…", verbose or not) carry nothing durable → skip.
    if not brain.is_refusal(reply_text):
        try:
            memory.store(f"Q: {text}\nA: {reply_text}", source="turn", confidence=0.5)
        except (MemoryUnavailable, EmbedError, AdmissionDenied) as exc:
            log.warning("could not remember the turn (reply still sent): %s", exc)

    return Reply(text=reply_text, source=ReplySource.BRAIN, hits=hits)


def tell_stream(text: str) -> Iterator[tuple[str, str]]:
    """Streaming turn: recall → ground → stream the brain's reasoning+answer →
    remember. Same no-fabrication contract as :func:`tell`, but yields ordered
    ``(channel, chunk)`` events so chat AND voice can show reasoning live like
    Claude. Channels: ``source`` (memory|brain|unavailable), ``thinking``,
    ``answer``, ``done`` (final answer text). Never raises into the caller.
    """
    text = (text or "").strip()
    if not text:
        yield ("source", "unavailable")
        yield ("answer", "I didn't catch that.")
        yield ("done", "I didn't catch that.")
        return

    # 1. RECALL + GROUND — answer straight from memory only if confident.
    hits: list = []
    try:
        answer, hits = memory.answer(text)
    except MemoryUnavailable as exc:
        log.warning("memory unavailable during recall, degrading: %s", exc)
        failures.record("memory", "unavailable", str(exc))
        answer = None
    if answer is not None:
        answer = _present_memory_answer(answer, hits)
        yield ("source", "memory")
        yield ("answer", answer)
        yield ("done", answer)
        return

    # 2. REASON — stream the Claude CLI brain, grounded in the recalled hits.
    context = "\n".join(f"- {h.content}" for h in hits)
    yield ("source", "brain")
    parts: list[str] = []
    try:
        for channel, chunk in brain.think_stream(text, context):
            if channel == "answer":
                parts.append(chunk)
            yield (channel, chunk)
    except BrainUnavailable as exc:
        log.error("brain unavailable: %s", exc)
        failures.record("brain", "unavailable", str(exc))
        msg = f"I don't know — my reasoning brain is unavailable right now ({exc})."
        yield ("source", "unavailable")
        yield ("answer", msg)
        yield ("done", msg)
        return

    # 3. REMEMBER (best-effort) — skip refusals (nothing durable).
    reply_text = "".join(parts).strip()
    if reply_text and not brain.is_refusal(reply_text):
        try:
            memory.store(f"Q: {text}\nA: {reply_text}", source="turn", confidence=0.5)
        except (MemoryUnavailable, EmbedError, AdmissionDenied) as exc:
            log.warning("could not remember the turn (reply still sent): %s", exc)
    yield ("done", reply_text)


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
