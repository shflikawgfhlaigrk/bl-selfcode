"""The local lane (L1): the Ollama HTTP boundary is injectable (tests never touch
Ollama), failure is structured (-> escalate, never fabricate), and the heavy
reasoner's native thinking is split from its answer onto Utah's channels."""
from __future__ import annotations

import json

import pytest

from utah import config, local


# --- think() (non-streaming) ---------------------------------------------------

def test_think_returns_local_answer():
    local.set_runner(lambda payload, timeout: {"content": "  72 degrees.  ", "thinking": ""})
    assert local.think("weather?") == "72 degrees."


def test_think_empty_is_i_dont_know():
    local.set_runner(lambda payload, timeout: {"content": "", "thinking": "hmm"})
    assert local.think("anything") == local.I_DONT_KNOW


def test_think_prompt_carries_no_fab_contract_and_context():
    seen = {}

    def runner(payload, timeout):
        seen["payload"] = payload
        return {"content": "ok", "thinking": ""}

    local.set_runner(runner)
    local.think("where does Michael live?", "- Michael lives in Utah")
    msgs = seen["payload"]["messages"]
    assert any(local.NO_FAB in m["content"] for m in msgs)            # system contract
    user = next(m["content"] for m in msgs if m["role"] == "user")
    assert "- Michael lives in Utah" in user and "where does Michael live?" in user


def test_quick_vs_heavy_select_different_models():
    seen = {}
    local.set_runner(lambda payload, timeout: seen.update(model=payload["model"]) or {"content": "x", "thinking": ""})
    local.think("q", heavy=False)
    assert seen["model"] == config.LOCAL_QUICK_MODEL
    local.think("q", heavy=True)
    assert seen["model"] == config.LOCAL_HEAVY_MODEL


def test_heavy_requests_native_thinking_quick_does_not():
    seen = {}
    local.set_runner(lambda payload, timeout: seen.update(p=payload) or {"content": "x", "thinking": ""})
    local.think("q", heavy=True)
    assert seen["p"].get("think") is True
    local.think("q", heavy=False)
    assert not seen["p"].get("think")  # quick model is not a reasoner


def test_think_truncates_oversized_context():
    seen = {}
    local.set_runner(lambda payload, timeout: seen.update(p=payload) or {"content": "x", "thinking": ""})
    local.think("q", "x" * (config.BRAIN_CONTEXT_MAX_CHARS + 5000))
    user = next(m["content"] for m in seen["p"]["messages"] if m["role"] == "user")
    assert len(user) < config.BRAIN_CONTEXT_MAX_CHARS + 1000


def test_think_unavailable_raises_local_unavailable():
    def boom(payload, timeout):
        raise local.LocalUnavailable("ollama down")

    local.set_runner(boom)
    with pytest.raises(local.LocalUnavailable, match="ollama down"):
        local.think("q")


# --- think_stream() ------------------------------------------------------------

def test_stream_splits_thinking_from_answer():
    chunks = [
        {"content": "", "thinking": "let me "},
        {"content": "", "thinking": "think"},
        {"content": "The ", "thinking": ""},
        {"content": "answer.", "thinking": ""},
    ]
    local.set_stream_runner(lambda payload, timeout: iter(chunks))
    out = list(local.think_stream("q", heavy=True))
    assert ("thinking", "let me ") in out and ("thinking", "think") in out
    answer = "".join(c for ch, c in out if ch == "answer")
    assert answer == "The answer."


def test_stream_quick_emits_only_answer():
    chunks = [{"content": "hi ", "thinking": ""}, {"content": "there", "thinking": ""}]
    local.set_stream_runner(lambda payload, timeout: iter(chunks))
    out = list(local.think_stream("q", heavy=False))
    assert [ch for ch, _ in out] == ["answer", "answer"]
    assert "".join(c for _, c in out) == "hi there"


def test_stream_unavailable_raises():
    def boom(payload, timeout):
        raise local.LocalUnavailable("conn refused")

    local.set_stream_runner(boom)
    with pytest.raises(local.LocalUnavailable):
        list(local.think_stream("q"))


# --- the real HTTP runner (no Ollama needed: structured failure) ---------------

def test_http_runner_unreachable_is_structured(monkeypatch):
    # point at a dead port; the real runner must raise LocalUnavailable, not crash.
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:1")
    local.set_runner(None)  # the real urllib runner
    with pytest.raises(local.LocalUnavailable):
        local.think("hello")


def test_http_runner_parses_message_fields():
    # exercise the parsing seam directly with a captured Ollama-shaped body.
    body = json.dumps({"message": {"role": "assistant", "content": "A", "thinking": "T"}, "done": True})
    parsed = local._parse_message(body)
    assert parsed == {"content": "A", "thinking": "T"}
