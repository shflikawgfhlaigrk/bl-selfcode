"""The intent router — pick the cheapest tier that can answer a turn.

Pure and table-driven (every mapping has a unit test). Order encodes priority:
explicit "use the paid lane" and agentic work go straight to the brain; exact
grounded capabilities (weather, brief) beat everything else because they are
cheapest and exact; reasoning verbs or long queries lean to the heavy local tier;
everything else is a quick local turn.

The router is *allowed* to be imperfect: a local refusal or an unreachable Ollama
escalates to the Claude CLI at runtime (:mod:`utah.core`), so a mis-route is only
ever slower — never a wrong answer.
"""
from __future__ import annotations

import enum
import re

from utah import config


class Route(enum.Enum):
    """The tier a turn is routed to (cheapest-that-can-answer)."""

    SOCIAL = "social"            # a greeting/ack/thanks — answered instantly, no model
    BRIEF = "brief"              # the morning-brief capability (live Postgres state)
    WEATHER = "weather"          # the weather capability (free API + cache)
    TIME = "time"                # the clock capability (time/date, grounded)
    KNOWLEDGE = "knowledge"      # a curated knowledge pack, verbatim (no model)
    LOCAL_QUICK = "local_quick"  # llama3.2:3b — fast instruct, most quick things
    LOCAL_HEAVY = "local_heavy"  # deepseek-r1:32b — the free resident reasoner
    BRAIN = "brain"              # the Claude CLI (the one paid lane)


#: Explicit "use the paid lane."
_ASK_CLAUDE = re.compile(r"\b(ask|use)\s+claude\b|\bthink\s+hard\b|\bdeep[\s-]?think\b", re.I)

#: Agentic work — needs the real harness (tools, code, web, git, files).
_AGENTIC = re.compile(
    r"\b(write|refactor|debug|fix|implement|build|run|execute|browse|grep|commit|"
    r"deploy|merge|edit)\b|\bsearch the web\b|\bopen a pr\b|\bcreate a (file|pr|branch)\b",
    re.I,
)

#: Grounded capabilities (exact, cheap — beat the models).
_WEATHER = re.compile(
    r"\b(weather|forecast|temperature|how (hot|cold|warm)|rain(ing|y)?|snow(ing|y)?|"
    r"humidity|wind|sunny|cloudy|outside)\b",
    re.I,
)
_BRIEF = re.compile(
    r"\b(morning brief|daily brief|brief me|the brief|status report|how are things|"
    r"where (do |are )?things stand|what'?s the status|state of (things|play)|"
    r"what happened (today|overnight|last night))\b",
    re.I,
)
_TIME = re.compile(
    r"\b(what'?s the (time|date)|what time is it|current time|the time right now|"
    r"what day is it|what'?s today'?s? (date|day)?|today'?s date|day of the week|"
    r"what'?s the day)\b",
    re.I,
)

#: Reasoning verbs — lean to the heavy (free) local reasoner.
_REASONING = re.compile(
    r"\b(why|explain|analy[sz]e|compare|reason about|prove|walk me through|"
    r"how does|how do|what'?s the difference|trade-?offs?|pros and cons)\b",
    re.I,
)

#: External factual-recall ("who won…", "when did…", "what was the score").
#: The small local model answers these unreliably — it invents specifics (it
#: served a fabricated "2019 Super Bowl … 13-3"). Such questions go straight to
#: the brain, whose no-fabrication gate is structural: it answers from context or
#: says "I don't know" — never a 3B guess served as fact. Personal forms ("who is
#: my realtor") are safe here too: the brain grounds them from recalled memory.
_FACTUAL_RECALL = re.compile(
    r"\bwho (won|wrote|invented|discovered|created|directed|founded|painted|composed)\b|"
    r"\bwhen (did|was|were|is|do|does)\b|"
    r"\bwhat (year|day|date) (did|was|were|do|does|is|are)\b|"
    r"\bwhat('?s| is| was| were) the (score|result|outcome|winner)\b|"
    r"\b(capital|population|currency|language|president|prime minister) of\b|"
    r"\bhow many\b|"
    r"\bhow (tall|old|far|long|fast|high|deep|much) (is|are|was|were|did|do|does)\b|"
    r"\b(super bowl|world cup|world series|olympics|nobel prize|oscar|grammy)\b|"
    r"\bairspeed velocity\b",
    re.I,
)


def is_factual_recall(text: str) -> bool:
    """True for an external/world-knowledge question ("who won…", "capital of…",
    "how tall is…"). These are what the brain's no-fab gate refuses when memory is
    cold — and exactly what the web can ground — so the learn-on-miss loop is scoped
    to them (personal/agentic misses don't trigger a web search)."""
    return bool(_FACTUAL_RECALL.search(text or ""))


def route(text: str) -> Route:
    """Map a turn to the cheapest tier that can answer it."""
    t = (text or "").strip()
    if not t:
        return Route.LOCAL_QUICK
    # A whole-message greeting/ack/thanks → instant canned reply, no model. Anchored
    # to the FULL message, so "hello, debug X" is not social (it falls through to the
    # brain) and bare "yes"/"no"/"ok" stay normal turns (they continue a thread).
    from utah import social

    if social.matches(t):
        return Route.SOCIAL
    if _ASK_CLAUDE.search(t):
        return Route.BRAIN
    if _AGENTIC.search(t):
        return Route.BRAIN
    if _WEATHER.search(t):
        return Route.WEATHER
    if _TIME.search(t):
        return Route.TIME
    if _BRIEF.search(t):
        return Route.BRIEF
    # Curated knowledge packs answer verbatim — never a model (which hallucinates
    # them). Checked before reasoning/local so "explain Douglas's rules" stays exact.
    from utah.knowledge import douglas

    if douglas.matches(t):
        return Route.KNOWLEDGE
    # External factual-recall → the brain (structural no-fab), never the 3B which
    # invents specifics. Checked before the local tiers; after capabilities/packs
    # so grounded weather/time/brief/Douglas still win.
    if _FACTUAL_RECALL.search(t):
        return Route.BRAIN
    if _REASONING.search(t) or len(t.split()) >= config.ROUTER_HEAVY_MIN_WORDS:
        return Route.LOCAL_HEAVY
    return Route.LOCAL_QUICK
