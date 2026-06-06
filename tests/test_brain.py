"""The brain boundary: subprocess failures are structured, parsing is strict,
and 'no answer' is "I don't know." — never an invented reply."""
from __future__ import annotations

import json

import pytest

from utah import brain, config
from tests.fakes import ScriptedRunner, unavailable_runner


# --- think ---------------------------------------------------------------------

def test_think_returns_brain_reply(fake_brain):
    fake_brain.respond = "  Utah is the new project.  "
    assert brain.think("what is utah?") == "Utah is the new project."


def test_think_empty_output_is_i_dont_know(fake_brain):
    fake_brain.respond = ""
    assert brain.think("anything") == brain.I_DONT_KNOW


def test_think_prompt_contains_no_fab_contract_and_context(fake_brain):
    fake_brain.respond = "ok"
    brain.think("where does Michael live?", "- Michael lives in Utah")
    prompt = fake_brain.last_prompt
    assert brain.NO_FAB in prompt
    assert "- Michael lives in Utah" in prompt
    assert "where does Michael live?" in prompt


def test_think_truncates_oversized_context(fake_brain):
    fake_brain.respond = "ok"
    brain.think("q", "x" * (config.BRAIN_CONTEXT_MAX_CHARS + 5000))
    assert len(fake_brain.last_prompt) < config.BRAIN_CONTEXT_MAX_CHARS + 1000


def test_think_raises_brain_unavailable_when_cli_fails():
    brain.set_runner(unavailable_runner("cli gone"))
    with pytest.raises(brain.BrainUnavailable, match="cli gone"):
        brain.think("q")


# --- the real subprocess runner (no Claude CLI needed) -----------------------------

def test_subprocess_runner_missing_binary_is_structured(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_CMD", "/nonexistent/claude-cli")
    brain.set_runner(None)  # the real runner
    with pytest.raises(brain.BrainUnavailable, match="not found"):
        brain.ask("hello")


def test_subprocess_runner_nonzero_exit_is_structured(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_CMD", "false")
    monkeypatch.setattr(config, "BRAIN_ARGS", ())
    brain.set_runner(None)
    with pytest.raises(brain.BrainUnavailable, match="exited"):
        brain.ask("hello")


def test_subprocess_runner_timeout_is_structured(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_CMD", "sleep")
    monkeypatch.setattr(config, "BRAIN_ARGS", ())
    brain.set_runner(None)
    with pytest.raises(brain.BrainUnavailable, match="timed out"):
        brain.ask("5", timeout=1)


def test_subprocess_runner_happy_path(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_CMD", "echo")
    monkeypatch.setattr(config, "BRAIN_ARGS", ())
    brain.set_runner(None)
    assert brain.ask("hello world") == "hello world"


# --- extract_facts ---------------------------------------------------------------

def test_extract_facts_clean_json(fake_brain):
    fake_brain.respond = '["Michael lives in Utah", "Utah is step 1"]'
    assert brain.extract_facts("...") == ["Michael lives in Utah", "Utah is step 1"]


def test_extract_facts_tolerates_prose_around_json(fake_brain):
    fake_brain.respond = 'Sure! Here are the facts:\n["a fact"]\nLet me know!'
    assert brain.extract_facts("...") == ["a fact"]


def test_extract_facts_invalid_json_is_no_facts_not_a_crash(fake_brain):
    fake_brain.respond = "I could not find any facts, sorry."
    assert brain.extract_facts("...") == []


def test_extract_facts_non_array_json_is_no_facts(fake_brain):
    fake_brain.respond = '{"fact": "not an array"}'
    assert brain.extract_facts("...") == []


def test_extract_facts_filters_non_strings_and_blanks(fake_brain):
    fake_brain.respond = json.dumps(["real fact", 42, None, "  ", {"x": 1}, "another"])
    assert brain.extract_facts("...") == ["real fact", "another"]


def test_extract_facts_caps_count_and_length(fake_brain):
    fake_brain.respond = json.dumps([f"fact {i} " + "y" * 600 for i in range(20)])
    facts = brain.extract_facts("...")
    assert len(facts) == config.MAX_FACTS_PER_TURN
    assert all(len(f) <= config.MAX_FACT_CHARS for f in facts)


def test_extract_facts_brain_down_is_none_not_empty():
    """None = retry later; [] = genuinely nothing. The distinction is load-bearing."""
    brain.set_runner(unavailable_runner())
    assert brain.extract_facts("...") is None


def test_set_runner_none_restores_subprocess_runner():
    runner = ScriptedRunner(respond="x")
    brain.set_runner(runner)
    brain.set_runner(None)
    assert brain._runner is brain._subprocess_runner
