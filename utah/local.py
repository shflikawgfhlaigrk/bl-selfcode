"""The local lane — free, resident Ollama models in FRONT of the Claude CLI.

L1 cognition: a quick instruct model (``llama3.2:3b``) and a heavy reasoner
(``deepseek-r1:32b``, native thinking), both pinned warm via ``keep_alive``. This
mirrors :mod:`utah.brain` exactly: the HTTP call is an injectable boundary
(:func:`set_runner` / :func:`set_stream_runner`) so tests never touch Ollama, and
every failure mode raises :class:`LocalUnavailable` — the caller escalates to the
brain instead of fabricating. The no-fabrication contract (:data:`NO_FAB`,
:func:`is_refusal`, "I don't know.") is shared with the brain so both tiers behave
identically. Ollama (0.30.5) streams ``message.thinking`` separately from
``message.content``, so the reasoner maps straight onto Utah's
``("thinking"|"answer")`` channels with no tag parsing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator, Protocol

from utah import UtahError, config
from utah.brain import I_DONT_KNOW, NO_FAB, is_refusal  # one shared contract

log = logging.getLogger("utah.local")

__all__ = [
    "LocalUnavailable",
    "think",
    "think_stream",
    "is_refusal",
    "I_DONT_KNOW",
    "NO_FAB",
    "set_runner",
    "set_stream_runner",
]


class LocalUnavailable(UtahError):
    """A local model could not produce a response (the caller escalates to the brain)."""


# --------------------------------------------------------------------------
# Prompt / payload construction
# --------------------------------------------------------------------------

def _model(heavy: bool) -> str:
    return config.LOCAL_HEAVY_MODEL if heavy else config.LOCAL_QUICK_MODEL


def _payload(question: str, context: str, *, heavy: bool, stream: bool) -> dict:
    """The Ollama ``/api/chat`` request body. Same grounding + no-fab contract as
    the brain; ``think`` is only set for the heavy reasoner (the quick model is a
    plain instruct model and would reject it)."""
    ctx = (context or "").strip()
    if len(ctx) > config.BRAIN_CONTEXT_MAX_CHARS:
        ctx = ctx[: config.BRAIN_CONTEXT_MAX_CHARS]
    user = f"CONTEXT:\n{ctx or '(none)'}\n\nQUESTION: {question}"
    payload: dict = {
        "model": _model(heavy),
        "messages": [
            {"role": "system", "content": NO_FAB},
            {"role": "user", "content": user},
        ],
        "stream": stream,
        "keep_alive": config.LOCAL_KEEP_ALIVE,
        "options": {
            "num_predict": config.LOCAL_HEAVY_MAX_TOKENS if heavy else config.LOCAL_QUICK_MAX_TOKENS
        },
    }
    if heavy:
        payload["think"] = True
    return payload


def _parse_message(body: str) -> dict:
    """Parse one Ollama response body into ``{content, thinking}`` (the only fields
    Utah routes). Raises :class:`LocalUnavailable` on a non-JSON body or a model
    error payload — bad output is never admitted as an answer."""
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LocalUnavailable("ollama returned non-JSON") from exc
    if not isinstance(obj, dict):
        raise LocalUnavailable("ollama returned a non-object")
    if obj.get("error"):
        raise LocalUnavailable(f"ollama error: {obj['error']}")
    msg = obj.get("message") or {}
    return {"content": msg.get("content") or "", "thinking": msg.get("thinking") or ""}


# --------------------------------------------------------------------------
# HTTP boundaries (injectable) — every failure mode -> LocalUnavailable.
# --------------------------------------------------------------------------

Runner = Callable[[dict, int], dict]
StreamRunner = Callable[[dict, int], Iterator[dict]]


def _post(payload: dict, timeout: int):
    url = config.OLLAMA_URL.rstrip("/") + "/api/chat"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        raise LocalUnavailable(f"ollama unreachable: {getattr(exc, 'reason', exc)}") from exc
    except OSError as exc:
        raise LocalUnavailable(f"ollama call failed: {exc}") from exc


def _http_runner(payload: dict, timeout: int) -> dict:
    """Default non-streaming runner: one round-trip to Ollama."""
    payload = {**payload, "stream": False}
    resp = _post(payload, timeout)
    try:
        body = resp.read().decode("utf-8")
    except OSError as exc:
        raise LocalUnavailable(f"ollama read failed: {exc}") from exc
    finally:
        resp.close()
    return _parse_message(body)


def _http_stream_runner(payload: dict, timeout: int) -> Iterator[dict]:
    """Default streaming runner: yield ``{content, thinking}`` deltas as Ollama
    produces them (newline-delimited JSON). Bounded by an overall deadline."""
    payload = {**payload, "stream": True}
    resp = _post(payload, timeout)
    deadline = time.monotonic() + timeout
    try:
        for raw in resp:
            if time.monotonic() > deadline:
                raise LocalUnavailable(f"ollama timed out after {timeout}s")
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("error"):
                raise LocalUnavailable(f"ollama error: {obj['error']}")
            msg = obj.get("message") or {}
            yield {"content": msg.get("content") or "", "thinking": msg.get("thinking") or ""}
            if obj.get("done"):
                break
    finally:
        resp.close()


_runner: Runner = _http_runner
_stream_runner: StreamRunner = _http_stream_runner
_runner_lock = threading.Lock()


def set_runner(runner: Runner | None) -> None:
    """Inject a non-streaming runner (tests). ``None`` restores the real HTTP runner."""
    global _runner
    with _runner_lock:
        _runner = runner if runner is not None else _http_runner


def set_stream_runner(runner: StreamRunner | None) -> None:
    """Inject a streaming runner (tests). ``None`` restores the real HTTP runner."""
    global _stream_runner
    with _runner_lock:
        _stream_runner = runner if runner is not None else _http_stream_runner


# --------------------------------------------------------------------------
# Public API — mirrors brain.think / brain.think_stream.
# --------------------------------------------------------------------------

def think(question: str, context: str = "", *, heavy: bool = False) -> str:
    """Grounded reasoning over *context* on a local model. Returns the answer, or
    :data:`I_DONT_KNOW` when the model replies empty. Raises
    :class:`LocalUnavailable` when Ollama itself fails (the caller escalates)."""
    payload = _payload(question, context, heavy=heavy, stream=False)
    with _runner_lock:
        runner = _runner
    resp = runner(payload, config.LOCAL_TIMEOUT)
    return (resp.get("content") or "").strip() or I_DONT_KNOW


def think_stream(
    question: str, context: str = "", *, heavy: bool = False
) -> Iterator[tuple[str, str]]:
    """Stream grounded reasoning + answer as ordered ``(channel, chunk)`` events
    (``"thinking"`` | ``"answer"``) — the same shape the chat box and voice already
    render for the brain. Raises :class:`LocalUnavailable` on any Ollama failure."""
    payload = _payload(question, context, heavy=heavy, stream=True)
    with _runner_lock:
        runner = _stream_runner
    for chunk in runner(payload, config.LOCAL_TIMEOUT):
        thinking = chunk.get("thinking") or ""
        if thinking:
            yield ("thinking", thinking)
        content = chunk.get("content") or ""
        if content:
            yield ("answer", content)
