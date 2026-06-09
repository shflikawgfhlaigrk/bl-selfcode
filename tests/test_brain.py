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


def test_context_truncation_cuts_on_a_fact_boundary(monkeypatch):
    """Over-budget CONTEXT is trimmed at a newline, never mid-fact (so a number/name
    is never severed). Fixes the hard char-cut the audit flagged."""
    monkeypatch.setattr(config, "BRAIN_CONTEXT_MAX_CHARS", 40)
    ctx = "- fact one is short\n- fact two is much longer and would be cut mid-word\n- three"
    out = brain._truncate_context(ctx)
    assert len(out) <= 40
    assert out == "- fact one is short"          # whole facts only, cut at the newline
    assert not out.endswith(("muc", "longe"))     # never a torn word


def test_build_prompt_is_shared_shape_for_think_and_stream():
    """_build_prompt is the single prompt assembler — PERSONA + NO_FAB + CONTEXT + Q,
    with optional voice-brief and thinking blocks."""
    base = brain._build_prompt("Q?", "- a fact")
    assert base.startswith(brain.PERSONA) and brain.NO_FAB in base and "QUESTION: Q?" in base
    assert brain.THINK_INSTRUCTION not in base and brain.VOICE_BRIEF not in base
    full = brain._build_prompt("Q?", "- a fact", brief=True, want_thinking=True)
    assert brain.THINK_INSTRUCTION in full and brain.VOICE_BRIEF in full


def test_decode_stream_warns_on_schema_drift(caplog):
    """JSON lines with no stream_event frame → a loud schema-drift warning, not a
    silent empty answer."""
    import logging
    drifted = [json.dumps({"type": "message_delta", "text": "hi"})]   # new/unknown shape
    with caplog.at_level(logging.WARNING, logger="utah.brain"):
        out = list(brain.decode_stream(drifted))
    assert out == []                                  # nothing routed (shape unknown)
    assert any("schema may have changed" in r.getMessage() for r in caplog.records)


def test_no_fab_forbids_basic_knowledge_and_guessing():
    """The contract must answer ONLY from context — no outside/'basic' knowledge,
    no guessing. A loophole here let the brain fabricate trivia (swallow airspeed,
    Super Bowl) that then poisoned memory."""
    contract = brain.NO_FAB.lower()
    assert "only from the context" in contract
    assert "basic" in contract  # explicitly names + forbids 'basic' knowledge
    assert "guess" in contract
    assert "i don't know." in contract


def test_persona_defines_ace_partner_identity():
    """PERSONA is a behavior layer separate from the grounding rule: it makes the brain
    talk like Ace — Michael's partner — not a cold database. This is what was missing
    (the only 'persona' used to be the anti-fabrication prompt, which strips humanity)."""
    p = brain.PERSONA.lower()
    assert "ace" in p                       # one consistent identity (not "Utah")
    assert "partner" in p or "michael" in p  # the relationship, embodied
    assert "warm" in p or "friend" in p      # warmth, not a robot


def test_prompt_layers_persona_before_grounding(fake_brain):
    """Every brain turn carries PERSONA (who Ace is) ahead of NO_FAB (the fact-grounding
    rule). Persona always-on is what makes replies human; grounding still binds facts."""
    fake_brain.respond = "ok"
    brain.think("where does Michael live?", "- Michael lives in Utah")
    prompt = fake_brain.last_prompt
    assert brain.PERSONA in prompt
    assert brain.NO_FAB in prompt
    assert prompt.index(brain.PERSONA) < prompt.index(brain.NO_FAB)


def test_no_fab_grounds_facts_without_a_robotic_dead_end():
    """The grounding rule still binds FACTS, but no longer forces a cold one-line
    dead-end: a refusal must still BEGIN with 'I don't know' (so is_refusal +
    learn-on-miss keep firing) yet may add a warm offer to find it."""
    c = brain.NO_FAB.lower()
    assert "i don't know." in c          # still the detectable refusal contract
    assert "and nothing else" not in c   # the dehumanizing clause is gone
    assert "fact" in c                   # restriction scoped to facts, not personality


def test_persona_subordinates_to_grounding_no_training_facts():
    """Warmth must NEVER license fabrication. Live regression caught this: an over-free
    persona ('use common sense', 'don't play dumb') made the brain answer a Super Bowl
    question from its own training and refuse to say 'I don't know' — breaking no-fab and
    killing learn-on-miss. The persona must scope freedom to TONE and yield to grounding."""
    p = brain.PERSONA.lower()
    assert "training" in p                                   # names the model's own knowledge
    assert "context" in p                                    # facts come from CONTEXT only
    assert any(w in p for w in ("override", "overrides", "wins"))  # grounding wins conflicts


def test_no_fab_overrides_known_facts_not_in_context():
    """The refusal mandate must hold even when the model 'knows' the answer from training —
    otherwise it rationalizes past 'I don't know.' ('feigning ignorance is lying') and the
    fetch-and-ground loop never fires. State that beginning with 'I don't know.' is BY DESIGN."""
    c = brain.NO_FAB.lower()
    assert "training" in c
    assert "even if" in c


@pytest.mark.parametrize(
    "text",
    [
        "I don't know.",
        "i don't know",
        "I do not know who won.",
        "Not in the context. As a rough estimate, ~11 m/s.",
        "  not supported by the context  ",
    ],
)
def test_is_refusal_catches_soft_refusals(text):
    assert brain.is_refusal(text)


@pytest.mark.parametrize(
    "text",
    [
        "Michael lives in Utah.",
        "The context says he prefers terse answers.",
        "Based on the conversation, you decided to rebuild AceOS.",
    ],
)
def test_is_refusal_does_not_flag_real_answers(text):
    assert not brain.is_refusal(text)


def test_think_truncates_oversized_context(fake_brain):
    fake_brain.respond = "ok"
    brain.think("q", "x" * (config.BRAIN_CONTEXT_MAX_CHARS + 5000))
    # CONTEXT is capped at BRAIN_CONTEXT_MAX_CHARS; only the fixed prompt scaffolding
    # (persona + grounding rule + labels/question) is added on top. Sized from the
    # constants so it stays honest as the wording evolves — without truncation the
    # prompt would exceed this by ~5000 (the oversized context), so it still catches a
    # truncation regression.
    overhead = len(brain.PERSONA) + len(brain.NO_FAB) + 500
    assert len(fake_brain.last_prompt) < config.BRAIN_CONTEXT_MAX_CHARS + overhead


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
