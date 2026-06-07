"""The intent router: deterministic, table-driven, every mapping pinned. The
router is allowed to be imperfect — a local miss escalates to the brain at
runtime (utah.core) — so these tests pin the CHEAPEST-tier intent, not correctness
of the eventual answer."""
from __future__ import annotations

import pytest

from utah import config
from utah.router import Route, route


@pytest.mark.parametrize("text", [
    "hi", "hello", "hey", "yo", "good morning", "hey ace",
    "thanks", "thank you", "thx", "cheers",
    "bye", "good night", "see ya",
    "how are you", "what's up", "cool", "nice", "got it", "sounds good",
])
def test_social_intents_take_the_fast_path(text):
    assert route(text) is Route.SOCIAL


def test_social_only_matches_a_whole_social_message():
    # A greeting glued to a real task is NOT social — it still routes to the handler.
    assert route("hello can you debug the parser") is Route.BRAIN
    assert route("good morning what's the weather") is Route.WEATHER


@pytest.mark.parametrize("text", ["yes", "no", "ok", "okay", "yeah"])
def test_ambiguous_answers_are_not_hijacked_by_social(text):
    # Bare yes/no/ok usually CONTINUE a thread — they must not become a canned reply.
    assert route(text) is not Route.SOCIAL


@pytest.mark.parametrize("text", [
    "what's the weather",
    "weather in gulf shores",
    "is it raining right now?",
    "how hot is it today",
    "what's the forecast",
])
def test_weather_intents(text):
    assert route(text) is Route.WEATHER


@pytest.mark.parametrize("text", [
    "morning brief",
    "give me the daily brief",
    "how are things",
    "where do things stand",
    "what's the status",
    "what happened overnight",
])
def test_brief_intents(text):
    assert route(text) is Route.BRIEF


@pytest.mark.parametrize("text", [
    "what time is it",
    "what's the time",
    "what's today's date",
    "what day is it",
    "what's the date right now",
])
def test_time_intents(text):
    assert route(text) is Route.TIME


@pytest.mark.parametrize("text", [
    "ask claude what to do",
    "use claude for this",
    "think hard about my portfolio",
])
def test_explicit_escalation_to_brain(text):
    assert route(text) is Route.BRAIN


@pytest.mark.parametrize("text", [
    "write a python function to sort a list",
    "refactor the daemon",
    "debug this traceback",
    "search the web for X",
    "open a PR for the fix",
    "commit and deploy",
])
def test_agentic_intents_go_to_brain(text):
    assert route(text) is Route.BRAIN


@pytest.mark.parametrize("text", [
    "why does the engine lose money on ranges",
    "explain the difference between L1 and L2",
    "analyze my last 10 trades",
    "compare deepseek and llama",
])
def test_reasoning_intents_go_to_heavy_local(text):
    assert route(text) is Route.LOCAL_HEAVY


def test_long_query_leans_heavy():
    long_q = " ".join(["word"] * config.ROUTER_HEAVY_MIN_WORDS) + " please"
    assert route(long_q) is Route.LOCAL_HEAVY


@pytest.mark.parametrize("text", [
    "who is michael",
    "say hi",
    "what's 2+2",
])
def test_quick_intents_default_to_quick_local(text):
    assert route(text) is Route.LOCAL_QUICK


@pytest.mark.parametrize("text", [
    "what are mark douglas's trading rules",
    "tell me mark douglas's rules",
    "explain the trading in the zone principles",   # 'explain' but knowledge wins
    "what are the five fundamental truths",
])
def test_curated_knowledge_pack_routes_to_knowledge(text):
    assert route(text) is Route.KNOWLEDGE


def test_unrelated_trading_rules_do_not_hijack_knowledge():
    # "my trading rules" is NOT the Douglas pack — must not claim it
    assert route("what are my trading rules") is not Route.KNOWLEDGE


def test_empty_is_quick():
    assert route("") is Route.LOCAL_QUICK
    assert route("   ") is Route.LOCAL_QUICK


def test_weather_beats_reasoning_keyword():
    # "why is it raining" contains a reasoning verb AND weather — weather wins
    # (the grounded capability is cheaper and exact).
    assert route("why is it raining") is Route.WEATHER


@pytest.mark.parametrize("text", [
    "who won the 2019 Super Bowl and what was the score?",
    "who wrote Moby Dick",
    "when did World War 2 end",
    "what year did the Titanic sink",
    "what was the score of the world cup final",
    "capital of Australia",
    "how many moons does Jupiter have",
    "what is the airspeed velocity of an unladen swallow?",
])
def test_external_factual_recall_routes_to_brain(text):
    # The 3B invents specifics; the brain's no-fab is structural ("I don't know"
    # on unsupported), so world-fact recall goes to the brain, never local.
    assert route(text) is Route.BRAIN


@pytest.mark.parametrize("text", [
    "summarize this for me",
    "what's 2+2",
    "rewrite that more politely",
])
def test_quick_tasks_still_go_local_not_brain(text):
    # The factual-recall guard must not hijack genuine quick tasks.
    assert route(text) is Route.LOCAL_QUICK


@pytest.mark.parametrize("text", [
    "what is Project Utah",
    "what is utah",
    "tell me about ace",
    "who are you",
    "what are you",
    "what can you do",
    "what do you remember about Project Utah",
    "what do you know about my goals",
    "what's your mission",
])
def test_self_and_project_questions_ground_at_the_brain(text):
    # The 3B fabricates about ourselves ("Utah-pre"); these must hit the grounded,
    # no-fab brain so they answer from the real Utah corpus or say "I don't know".
    assert route(text) is Route.BRAIN


def test_weather_in_utah_still_wins_over_self_identity():
    # Capabilities are checked first — "weather in Utah" is a weather turn, not an
    # identity turn (the word 'utah' must not steal a grounded capability).
    assert route("what's the weather in Utah") is Route.WEATHER


def test_self_identity_word_boundary_does_not_catch_substrings():
    # \bace\b / \butah\b must not fire on 'place', 'race', etc.
    assert route("find me a parking place") is not Route.BRAIN
    assert route("what's 2+2") is Route.LOCAL_QUICK
