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
import time
from typing import Callable, Iterable, Iterator, Protocol, Sequence

from utah import UtahError, config

log = logging.getLogger("utah.brain")

I_DONT_KNOW = "I don't know."


def is_refusal(text: str) -> bool:
    """True if *text* is an 'I don't know' refusal (exact OR verbose).

    The brain sometimes elaborates ("I don't know. X isn't in the context…").
    Such turns carry nothing durable and must NOT be stored — matching only the
    exact string let verbose refusals leak into memory.
    """
    return text.strip().lower().startswith("i don't know")

NO_FAB = (
    "You are Utah, Michael's assistant. Answer ONLY from the CONTEXT and the "
    "question. If the answer is not supported by the context or basic knowledge, "
    'say "I don\'t know." Be concise. No markdown.'
)

#: Elicits the model's real chain-of-thought as a leading ``<thinking>…</thinking>``
#: block so the deck can render reasoning live (the CLI does not expose a native
#: thinking stream under ``-p``). ``brain.split_thinking`` routes the block.
THINK_INSTRUCTION = (
    "First, reason through the question inside a single <thinking>…</thinking> "
    "block (brief, grounded in the CONTEXT). Then, after </thinking>, give the "
    "final answer. Do not put the answer inside the thinking block."
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

    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                raise BrainUnavailable(f"brain timed out after {timeout}s")
            yield line
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
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
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict) or evt.get("type") != "stream_event":
            continue
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


def think_stream(question: str, context: str = "") -> Iterator[tuple[str, str]]:
    """Stream grounded reasoning + answer as ordered ``(channel, chunk)`` events.

    ``channel`` is ``"thinking"`` or ``"answer"``. Native thinking deltas pass
    straight through; answer text is routed through the ``<thinking>`` splitter.
    Raises :class:`BrainUnavailable` on any CLI failure (caller must not fabricate).
    """
    ctx = (context or "").strip()
    if len(ctx) > config.BRAIN_CONTEXT_MAX_CHARS:
        ctx = ctx[: config.BRAIN_CONTEXT_MAX_CHARS]
    prompt = (
        f"{NO_FAB}\n\n{THINK_INSTRUCTION}\n\n"
        f"CONTEXT:\n{ctx or '(none)'}\n\nQUESTION: {question}"
    )
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
    ctx = (context or "").strip()
    if len(ctx) > config.BRAIN_CONTEXT_MAX_CHARS:
        ctx = ctx[: config.BRAIN_CONTEXT_MAX_CHARS]
    prompt = f"{NO_FAB}\n\nCONTEXT:\n{ctx or '(none)'}\n\nQUESTION: {question}"
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
