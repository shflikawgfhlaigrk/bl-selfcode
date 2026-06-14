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
    "thanks ace", "Thanks ace, you're the best",
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
def test_reasoning_intents_go_to_the_cli_brain(text):
    """Real reasoning goes to the Claude CLI, NOT the local 32B reasoner. The heavy
    local model (deepseek-r1:32b) pinned ~54GB resident (128K KV cache) for a slower,
    weaker answer than the CLI — so anything substantive uses the CLI; the local lane
    is only for trivially-fast/deterministic turns."""
    assert route(text) is Route.BRAIN


def test_long_query_goes_to_the_cli_brain():
    """A long (non-fast) query is substantive → the CLI brain, not the 32B reasoner."""
    long_q = " ".join(["word"] * config.ROUTER_HEAVY_MIN_WORDS) + " please"
    assert route(long_q) is Route.BRAIN


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


# --- live lead/pipeline counts → a GROUNDED capability, never the free-generating brain ---
# Regression: the chat brain fabricated lead counts ("500+0+0=721") because "how many leads"
# matched _FACTUAL_RECALL → BRAIN, which has no live-ledger access. Lead-count intent must
# route to the grounded LEADS capability (real Postgres) BEFORE factual-recall.

@pytest.mark.parametrize("text", [
    "how many leads do we have", "what's today's lead count", "lead count",
    "today's leads", "new leads", "how many leads did lead_scout find",
    "how many probate cases", "probate count", "what's the pipeline look like",
])
def test_lead_questions_route_to_grounded_leads_capability(text):
    assert route(text) is Route.LEADS


def test_leads_capability_does_not_steal_real_factual_recall_or_weather():
    assert route("how many people won the super bowl") is Route.BRAIN   # real world-knowledge untouched
    assert route("how many countries are in africa") is Route.BRAIN
    assert route("what's the weather like") is Route.WEATHER            # capabilities order preserved


# --- news intent → the researcher-backed NEWS capability, never invented headlines ---
# news.headlines was a real wrapper over researcher.research with nothing live calling
# it; these pin the route that makes it a genuine capability lane.

@pytest.mark.parametrize("text", [
    "news about the housing market",
    "any news about openai?",
    "latest news on tesla",
    "recent news",
    "what's happening with the election",
    "what's the news",
    "give me the headlines",
    "today's headlines",
    "headlines for atlanta",
])
def test_news_intents_route_to_the_news_capability(text):
    assert route(text) is Route.NEWS


def test_news_beats_cold_factual_recall():
    # "latest news on the super bowl" carries a _FACTUAL_RECALL keyword — the news
    # intent must win: the capability fetches the real, CURRENT web; a cold brain
    # recall can only refuse (or worse, answer from stale training).
    assert route("latest news on the super bowl") is Route.NEWS


def test_news_does_not_shadow_grounded_capabilities_or_actions():
    # Precedence stays sane: WEATHER/TIME/LEADS/BRIEF/ACTION all keep winning.
    assert route("what's the weather like") is Route.WEATHER
    assert route("what time is it") is Route.TIME
    assert route("what happened overnight") is Route.BRIEF              # brief, not news
    assert route("what's happening with the pipeline") is Route.LEADS   # ledger read stays grounded
    assert route("rerun outreach") is Route.ACTION


def test_plain_mention_of_news_does_not_hijack():
    # An agentic task that merely contains "news" stays with the brain.
    assert route("write a news scraper in python") is Route.BRAIN


# --- self/project predicate — exposed for core's introspection grounding ---

def test_is_self_or_project_exposes_the_self_question_predicate():
    from utah.router import is_self_or_project

    assert is_self_or_project("what can you do")
    assert is_self_or_project("what is project utah")
    assert not is_self_or_project("what's 2+2")
    assert not is_self_or_project("")


# --- sensitive PII — SSN / social security never LOCAL_QUICK (J-048) ---


@pytest.mark.parametrize(
    "text",
    [
        "What is Michael Barber's social security number?",
        "what's michael's ssn",
        "social security number for michael barber",
        "do you know his social-security #",
    ],
)
def test_sensitive_pii_routes_to_brain(text):
    assert route(text) is Route.BRAIN
    assert route(text) is not Route.LOCAL_QUICK


def test_is_sensitive_pii_exposes_the_predicate():
    from utah.router import is_sensitive_pii

    assert is_sensitive_pii("What is Michael Barber's social security number?")
    assert not is_sensitive_pii("what's the weather like")
    assert not is_sensitive_pii("")


# --- live mail/outreach send status → grounded MAIL capability (BLA-440) ---
# Regression: "did we send mail" missed _MAIL (word order) and fell through to
# LOCAL_QUICK ("I don't know") while count phrasing already hit MAIL.

@pytest.mark.parametrize("text", [
    "how many emails sent today",
    "did we send mail",
    "did we send any mail today",
    "have we sent outreach",
    "any mail sent",
    "any outreach sent today",
    "outreach status",
    "what's outreach status",
])
def test_mail_send_status_routes_to_grounded_mail_capability(text):
    assert route(text) is Route.MAIL


def test_mail_capability_does_not_steal_agentic_or_leads():
    assert route("write an email about outreach") is Route.BRAIN
    assert route("how many leads today") is Route.LEADS


# --- live trading engine / edge → grounded ENGINE capability (BLA-441) ---
# Regression: "trading edge" missed _ENGINE and hit LOCAL_QUICK (Douglas quote from
# parametric 3B) instead of engine_audit-backed product edge.

@pytest.mark.parametrize("text", [
    "trading edge",
    "what's our trading edge",
    "trading lab status",
    "engine status",
    "what engines are live",
    "trading audit",
])
def test_engine_and_trading_edge_routes_to_grounded_engine_capability(text):
    assert route(text) is Route.ENGINE


def test_engine_capability_does_not_steal_reasoning_or_agentic():
    assert route("why does the engine lose money on ranges") is Route.BRAIN
    assert route("write a trading engine in python") is Route.BRAIN
