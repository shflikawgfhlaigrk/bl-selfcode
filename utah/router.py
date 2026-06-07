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

    BRIEF = "brief"              # the morning-brief capability (live Postgres state)
    WEATHER = "weather"          # the weather capability (free API + cache)
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

#: Reasoning verbs — lean to the heavy (free) local reasoner.
_REASONING = re.compile(
    r"\b(why|explain|analy[sz]e|compare|reason about|prove|walk me through|"
    r"how does|how do|what'?s the difference|trade-?offs?|pros and cons)\b",
    re.I,
)


def route(text: str) -> Route:
    """Map a turn to the cheapest tier that can answer it."""
    t = (text or "").strip()
    if not t:
        return Route.LOCAL_QUICK
    if _ASK_CLAUDE.search(t):
        return Route.BRAIN
    if _AGENTIC.search(t):
        return Route.BRAIN
    if _WEATHER.search(t):
        return Route.WEATHER
    if _BRIEF.search(t):
        return Route.BRIEF
    # Curated knowledge packs answer verbatim — never a model (which hallucinates
    # them). Checked before reasoning/local so "explain Douglas's rules" stays exact.
    from utah.knowledge import douglas

    if douglas.matches(t):
        return Route.KNOWLEDGE
    if _REASONING.search(t) or len(t.split()) >= config.ROUTER_HEAVY_MIN_WORDS:
        return Route.LOCAL_HEAVY
    return Route.LOCAL_QUICK
