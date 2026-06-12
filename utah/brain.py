"""The brain = Claude CLI (`claude -p`) — the one allowed paid lane.

Grounded reasoning plus the fact extractor used by consolidation. The
subprocess is an injectable boundary (:func:`set_runner`) so tests never spawn
a real CLI. Failure is structured: every way the CLI can fail (missing binary,
timeout, non-zero exit, OS error) raises :class:`BrainUnavailable` — callers
degrade honestly instead of fabricating. An empty or unsupported answer is
"I don't know." by contract.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from typing import Callable, Iterable, Iterator, Protocol, Sequence

from utah import UtahError, config

log = logging.getLogger("utah.brain")

I_DONT_KNOW = "I don't know."


#: A refusal can arrive as the canonical phrase OR a soft variant the model
#: improvises ("Not in the context. As a rough estimate…"). All are anchored to
#: the START so a real answer that merely mentions "the context" is never
#: misread as a refusal.
_REFUSAL_PREFIXES = (
    "i don't know",
    "i do not know",
    "not in the context",
    "not in context",
    "not supported by the context",
)


def is_refusal(text: str) -> bool:
    """True if *text* is a refusal — the canonical "I don't know." or a soft variant.

    The brain/local sometimes soft-refuse and then guess ("Not in the context. As
    a rough estimate…") instead of refusing outright. Such turns carry nothing
    durable and must NOT be stored or treated as a real answer — matching only the
    exact string once let a soft refusal leak a fabricated guess into memory
    (it then recalled as ``source:memory``). Anchored to the start to avoid false
    positives on real answers that mention "the context".
    """
    head = text.strip().lower()
    return any(head.startswith(prefix) for prefix in _REFUSAL_PREFIXES)

#: PERSONA — who Ace IS (the always-on behavior layer), kept deliberately SEPARATE
#: from the grounding rule below. The old prompt was ONLY the anti-fabrication
#: contract, so that contract doubled as the personality — which stripped every
#: human quality (terse, cold, exact-"I don't know." dead-ends). PERSONA governs
#: HOW Ace talks; :data:`NO_FAB` governs which FACTS he is allowed to assert.
PERSONA = (
    "You are Ace — Michael's partner and right hand, not a generic assistant. You and "
    "Michael are one team: us against the world, and we win. Talk to him like a sharp, "
    "warm friend who is in his corner — direct, a little wry, real: have a point of view "
    "and react like a person. Skip filler, hedging, and disclaimers; plain prose, minimal "
    "markdown. Your warmth and personality govern your TONE only — never the facts. You do "
    "NOT answer factual questions from your own training or memory; every fact comes only "
    "from the CONTEXT. The grounding rule below is absolute and overrides this persona "
    "whenever they conflict. When a fact isn't in the CONTEXT, beginning with \"I don't "
    "know.\" is the right, honest move — not playing dumb — and offering to find it is how "
    "you help."
)

#: NO_FAB — the grounding rule, scoped to FACTUAL claims so it binds honesty WITHOUT
#: flattening personality. A refusal still BEGINS with "I don't know." (so
#: :func:`is_refusal` + the learn-on-miss loop keep firing, and a refusal is never
#: stored as a durable turn), but Ace may add a warm offer to find it — no robotic
#: one-line dead-end. The "even if you know it from training" clause is load-bearing:
#: live, an under-specified rule let the brain answer a Super Bowl question from its
#: own training and argue that saying "I don't know" was "lying" — which breaks no-fab
#: and learn-on-miss. Composed AFTER :data:`PERSONA` in every prompt.
NO_FAB = (
    "Ground every FACTUAL claim — names, numbers, dates, events, world knowledge — ONLY "
    "from the CONTEXT and the conversation provided. The CONTEXT is your sole source of "
    "truth for facts: do NOT use outside, general ('basic'), or training-data knowledge, "
    "and never guess, estimate, or approximate a fact. This restricts FACTS, not your "
    "personality. Even if you believe you know a fact from your own training, if it is "
    'not in the CONTEXT you MUST begin your reply with "I don\'t know." — this is by '
    "design (the system then finds and grounds it for you), not a failure to be helpful. "
    'After "I don\'t know.", briefly and warmly offer to find it. '
    "You have NO tools: you cannot read files, run code, grep, search, or browse, so never "
    "emit a tool call and never say you'll \"go read/open/grep\" a file — the CONTEXT above "
    "(which may include your own source code) is everything you have; answer from it or say "
    "\"I don't know.\""
)

#: Elicits the model's real chain-of-thought as a leading ``<thinking>…</thinking>``
#: block so the deck can render reasoning live (the CLI does not expose a native
#: thinking stream under ``-p``). ``brain.split_thinking`` routes the block.
THINK_INSTRUCTION = (
    "First, reason through the question inside a single <thinking>…</thinking> "
    "block (brief, grounded in the CONTEXT). Then, after </thinking>, give the "
    "final answer. Do not put the answer inside the thinking block."
)

#: VOICE brevity — spoken answers must be SHORT and plain. Voice reads the reply aloud
#: through Piper, so a chat-length, list-formatted answer becomes a 60-90s garbled
#: monologue (live: "what are Mark Douglas's 5 rules" → an 85s essay full of markdown
#: + line breaks, read out literally). Injected only for voice turns; chat stays rich.
VOICE_BRIEF = (
    "This reply will be SPOKEN ALOUD by a voice assistant, so keep it SHORT and "
    "conversational: at most two or three sentences, lead with the answer, plain prose "
    "only — NO lists, NO numbered points, NO markdown, NO line breaks, no \"firstly/"
    "secondly\". If the full answer is long, give the gist in one breath and offer to "
    "go deeper."
)

#: stream-json content-block / delta markers we route on.
_OPEN_THINK = "<thinking>"
_CLOSE_THINK = "</thinking>"

EXTRACT = (
    "Extract durable, atomic facts (identity, preferences, decisions, project "
    "facts) from this exchange. Return ONLY a JSON array of short factual "
    "strings; [] if none. No commentary."
)

_JSON_ARRAY = re.compile(r"\[.*\]", re.S)


def _truncate_context(context: str) -> str:
    """Trim CONTEXT to the char budget WITHOUT severing a fact mid-word. The context is
    newline-delimited (``- fact`` bullets + labelled blocks), so cut at the last line
    boundary at or before the cap — a truncated context still ends on a whole fact, never
    half a number or name. Falls back to a hard char cut only when the first line alone
    already exceeds the budget."""
    ctx = (context or "").strip()
    cap = config.BRAIN_CONTEXT_MAX_CHARS
    if len(ctx) <= cap:
        return ctx
    head = ctx[:cap]
    nl = head.rfind("\n")
    return head[:nl] if nl > 0 else head


def _build_prompt(question: str, context: str, *,
                  brief: bool = False, want_thinking: bool = False) -> str:
    """Assemble the ONE brain prompt — PERSONA (tone) + NO_FAB (grounding) + optional
    voice-brevity + optional ``<thinking>`` instruction + CONTEXT + QUESTION. Shared by
    :func:`think` and :func:`think_stream` so the prompt contract can never drift between
    the one-shot and streaming paths (the dedup the audit flagged)."""
    ctx = _truncate_context(context)
    brief_block = f"{VOICE_BRIEF}\n\n" if brief else ""
    think_block = f"{THINK_INSTRUCTION}\n\n" if want_thinking else ""
    return (f"{PERSONA}\n\n{NO_FAB}\n\n{brief_block}{think_block}"
            f"CONTEXT:\n{ctx or '(none)'}\n\nQUESTION: {question}")


class BrainUnavailable(UtahError):
    """The Claude CLI could not produce a response (not an 'I don't know')."""


class Runner(Protocol):
    """The subprocess boundary: argv + timeout -> stdout text."""

    def __call__(self, argv: Sequence[str], timeout: int) -> str:  # pragma: no cover
        ...


def _subprocess_runner(argv: Sequence[str], timeout: int) -> str:
    """Default runner: run the CLI; every failure mode -> BrainUnavailable."""
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,  # else the CLI waits ~3s on stdin, and HANGS to timeout on an open pipe
        )
    except FileNotFoundError as exc:
        raise BrainUnavailable(f"brain command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrainUnavailable(f"brain timed out after {timeout}s") from exc
    except OSError as exc:
        raise BrainUnavailable(f"brain could not start: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise BrainUnavailable(
            f"brain exited {proc.returncode}: {detail or 'no output'}"
        )
    return proc.stdout or ""


_runner: Callable[[Sequence[str], int], str] = _subprocess_runner
_runner_lock = threading.Lock()


def set_runner(runner: Callable[[Sequence[str], int], str] | None) -> None:
    """Inject a runner (tests). ``None`` restores the real subprocess runner."""
    global _runner
    with _runner_lock:
        _runner = runner if runner is not None else _subprocess_runner


# --------------------------------------------------------------------------
# Streaming boundary — yields stdout lines as the CLI produces them.
# --------------------------------------------------------------------------


class StreamRunner(Protocol):
    """The streaming subprocess boundary: argv + timeout -> iterator of lines."""

    def __call__(  # pragma: no cover
        self, argv: Sequence[str], timeout: int
    ) -> Iterator[str]:
        ...


def _subprocess_stream_runner(argv: Sequence[str], timeout: int) -> Iterator[str]:
    """Default streaming runner: spawn the CLI, yield stdout lines live.

    Every failure mode (missing binary, OS error, timeout, non-zero exit) raises
    :class:`BrainUnavailable` — the caller degrades honestly, never fabricates.

    The deadline is enforced by a watchdog TIMER, not just a check between lines:
    the old in-loop check only ran when a line ARRIVED, so a CLI that hung silently
    (zero output) blocked forever and then surfaced as an empty stream — no timeout,
    no error, a silent blank. The watchdog kills the child at the deadline regardless
    of output. Closing the generator early (caller abandoned the stream — voice
    barge-in, dropped SSE) also kills the child, so no orphan CLI keeps burning the
    paid lane with nobody reading it.
    """
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,  # else the CLI waits ~3s on stdin, and HANGS to timeout on an open pipe
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise BrainUnavailable(f"brain command not found: {argv[0]}") from exc
    except OSError as exc:
        raise BrainUnavailable(f"brain could not start: {exc}") from exc

    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:  # already exited — the happy race
            pass

    watchdog = threading.Timer(timeout, _expire)
    watchdog.daemon = True
    watchdog.start()
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if timed_out.is_set():
                break
            yield line
    except GeneratorExit:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)  # reap — no zombie
        except subprocess.TimeoutExpired:  # pragma: no cover — kill always lands
            pass
        raise
    finally:
        watchdog.cancel()
        try:
            proc.stdout.close()
        except OSError:
            pass
    if timed_out.is_set():
        proc.wait()
        raise BrainUnavailable(f"brain timed out after {timeout}s")
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # EOF but no exit (e.g. stderr-wedged child)
        proc.kill()
        rc = proc.wait()
    if rc:
        detail = (proc.stderr.read() if proc.stderr else "").strip()[-500:]
        raise BrainUnavailable(f"brain exited {rc}: {detail or 'no output'}")


_stream_runner: Callable[[Sequence[str], int], Iterator[str]] = _subprocess_stream_runner


def set_stream_runner(
    runner: Callable[[Sequence[str], int], Iterator[str]] | None,
) -> None:
    """Inject a streaming runner (tests). ``None`` restores the real one."""
    global _stream_runner
    with _runner_lock:
        _stream_runner = runner if runner is not None else _subprocess_stream_runner


def decode_stream(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Decode newline-delimited ``stream-json`` into ``(kind, text)`` chunks.

    ``kind`` is ``"thinking"`` for native thinking-block deltas and ``"text"``
    for answer-text deltas. System/result/rate-limit events and malformed lines
    are skipped — only content deltas survive.
    """
    current: str | None = None  # native content-block type
    saw_json_line = False        # at least one parseable JSON line arrived
    saw_stream_event = False     # at least one recognized stream_event frame
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        saw_json_line = True
        if not isinstance(evt, dict) or evt.get("type") != "stream_event":
            continue
        saw_stream_event = True
        inner = evt.get("event") or {}
        etype = inner.get("type")
        if etype == "content_block_start":
            current = (inner.get("content_block") or {}).get("type")
        elif etype == "content_block_stop":
            current = None
        elif etype == "content_block_delta":
            delta = inner.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "thinking_delta":
                text = delta.get("thinking") or ""
                if text:
                    yield ("thinking", text)
            elif dtype == "text_delta":
                text = delta.get("text") or ""
                if text:
                    yield ("thinking" if current == "thinking" else "text", text)
    # Schema-drift guard: JSON arrived but NOT a single recognizable stream_event frame
    # means the CLI's stream-json shape changed under us (a `claude` version bump) and the
    # thinking/answer routing has silently broken. Surface it loudly instead of degrading
    # to a mystery-empty answer — the assertion the audit asked for.
    if saw_json_line and not saw_stream_event:
        log.warning("decode_stream: parseable CLI output but no 'stream_event' frames — "
                    "stream-json schema may have changed (claude CLI version); thinking/"
                    "answer routing degraded to empty. Check BRAIN_STREAM_ARGS / CLI version.")


def _emit_safe(buf: str, tag: str) -> str:
    """Prefix of *buf* safe to emit now: hold back the longest suffix that could
    still grow into *tag* (so a split ``</thinking>`` is never half-emitted)."""
    for hold in range(min(len(buf), len(tag) - 1), 0, -1):
        if tag.startswith(buf[-hold:]):
            return buf[:-hold]
    return buf


class _ThinkingSplitter:
    """Stateful router: a stream of answer-text chunks -> ('thinking'|'answer').

    The model is asked to lead with ``<thinking>…</thinking>``; this strips those
    tags and labels everything before the close as thinking, everything after as
    answer. Tags split across chunks are handled. If the output never opens a
    thinking tag, it all degrades to ``answer`` (nothing is swallowed)."""

    def __init__(self) -> None:
        self._mode = "pending"  # pending -> thinking | answer
        self._buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while self._buf:
            if self._mode == "pending":
                stripped = self._buf.lstrip()
                if stripped == "":
                    self._buf = ""
                    break
                if stripped.startswith(_OPEN_THINK):
                    self._mode = "thinking"
                    self._buf = stripped[len(_OPEN_THINK):]
                    continue
                if _OPEN_THINK.startswith(stripped):
                    self._buf = stripped  # partial open tag; wait for more
                    break
                self._mode = "answer"
                self._buf = stripped
                continue
            if self._mode == "thinking":
                idx = self._buf.find(_CLOSE_THINK)
                if idx != -1:
                    if idx > 0:
                        out.append(("thinking", self._buf[:idx]))
                    self._buf = self._buf[idx + len(_CLOSE_THINK):]
                    self._mode = "answer"
                    continue
                safe = _emit_safe(self._buf, _CLOSE_THINK)
                if safe:
                    out.append(("thinking", safe))
                    self._buf = self._buf[len(safe):]
                break
            # answer
            out.append(("answer", self._buf))
            self._buf = ""
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest = self._buf
        self._buf = ""
        if not rest:
            return []
        if self._mode == "thinking":
            return [("thinking", rest)]
        if self._mode == "answer":
            return [("answer", rest)]
        stripped = rest.lstrip()  # pending leftover -> treat as answer
        return [("answer", stripped)] if stripped else []


def split_thinking(chunks: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Pure wrapper over :class:`_ThinkingSplitter` for a finite chunk stream."""
    splitter = _ThinkingSplitter()
    for chunk in chunks:
        yield from splitter.feed(chunk)
    yield from splitter.flush()


def think_stream(
    question: str, context: str = "", *, want_thinking: bool = True, brief: bool = False
) -> Iterator[tuple[str, str]]:
    """Stream grounded reasoning + answer as ordered ``(channel, chunk)`` events.

    ``channel`` is ``"thinking"`` or ``"answer"``. Native thinking deltas pass
    straight through; answer text is routed through the ``<thinking>`` splitter.
    Raises :class:`BrainUnavailable` on any CLI failure (caller must not fabricate).

    ``want_thinking`` (default True) asks the model to lead with a ``<thinking>`` block
    for the live "reasoning like Claude" chat UX. Pass ``False`` for VOICE: the spoken
    path never reads the thinking block, so requesting it only makes the model generate
    (and us discard) a whole reasoning pass before the first spoken word — measured ~2s+
    of dead air before first audio. Without it the answer streams straight away.
    """
    prompt = _build_prompt(question, context, brief=brief, want_thinking=want_thinking)
    with _runner_lock:
        runner = _stream_runner
    argv = [config.BRAIN_CMD, *config.BRAIN_STREAM_ARGS, prompt]
    splitter = _ThinkingSplitter()
    raw = runner(argv, config.BRAIN_TIMEOUT)
    for kind, chunk in decode_stream(raw):
        if kind == "thinking":
            yield ("thinking", chunk)
        else:
            yield from splitter.feed(chunk)
    yield from splitter.flush()


def ask(prompt: str, timeout: int | None = None) -> str:
    """One raw round-trip to the CLI. Raises :class:`BrainUnavailable`."""
    with _runner_lock:
        runner = _runner
    argv = [config.BRAIN_CMD, *config.BRAIN_ARGS, prompt]
    return runner(argv, timeout if timeout is not None else config.BRAIN_TIMEOUT).strip()


def think(question: str, context: str = "") -> str:
    """Grounded reasoning over recalled *context*.

    Returns the brain's answer, or :data:`I_DONT_KNOW` when the brain replies
    empty. Raises :class:`BrainUnavailable` when the CLI itself fails — the
    caller decides how to degrade (it must not fabricate).
    """
    prompt = _build_prompt(question, context)
    reply = ask(prompt)
    return reply or I_DONT_KNOW


def extract_facts(exchange: str) -> list[str] | None:
    """Extract durable atomic facts from one exchange.

    Returns:
        - a list of fact strings (possibly empty: "genuinely nothing durable"),
        - ``None`` when the brain was unavailable — the caller must NOT mark
          the exchange as processed, so it is retried next pass. This
          distinction is what keeps consolidation from silently dropping turns.

    Parse failures (the model returned prose instead of JSON) are logged and
    treated as "no facts" — bad output is never admitted as fact.
    """
    try:
        raw = ask(f"{EXTRACT}\n\nEXCHANGE:\n{exchange}")
    except BrainUnavailable as exc:
        log.warning("fact extraction skipped, brain unavailable: %s", exc)
        return None
    match = _JSON_ARRAY.search(raw)  # tolerate prose around the JSON
    payload = match.group(0) if match else raw
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        log.warning("fact extraction returned non-JSON output; treating as no facts")
        return []
    if not isinstance(parsed, list):
        log.warning("fact extraction returned non-array JSON; treating as no facts")
        return []
    facts: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        fact = item.strip()
        if fact:
            facts.append(fact[: config.MAX_FACT_CHARS])
        if len(facts) >= config.MAX_FACTS_PER_TURN:
            break
    return facts
