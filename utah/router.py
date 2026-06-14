"""The intent router — pick the cheapest tier that can answer a turn.

Pure and table-driven (every mapping has a unit test). Order encodes priority:
explicit "use the paid lane" and agentic work go straight to the brain; exact
grounded capabilities (weather, brief) beat everything else because they are
cheapest and exact; reasoning verbs or long queries go to the Claude CLI brain
(the local 32B reasoner is retired from routing — it pinned ~54GB resident for a
slower, weaker answer); everything else is a quick local turn.

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
    ACTION = "action"            # a COMMAND to RUN a capability (rerun leads/outreach/etc.)
    LEADS = "leads"              # live lead/pipeline/probate counts (grounded Postgres)
    MAIL = "mail"                # live email/text send counts (grounded mail_ledger)
    JOBS = "jobs"                # live launchd roster + health — Ace's standing daily duties
    ENGINE = "engine"            # live trading engine/lab state (grounded, honest if unread)
    NEWS = "news"                # recent headlines on a topic (researcher-backed, grounded)
    KNOWLEDGE = "knowledge"      # a curated knowledge pack, verbatim (no model)
    LOCAL_QUICK = "local_quick"  # llama3.2:3b — fast instruct, the trivially-fast lane
    LOCAL_HEAVY = "local_heavy"  # deepseek-r1:32b — RETIRED from routing (~54GB resident);
    #                              kept for compat, but reasoning now goes to BRAIN
    BRAIN = "brain"              # the Claude CLI — anything substantive (the one paid lane)


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
#: Live lead / pipeline / probate COUNT questions — answered from the ledger, never the
#: free-generating brain (which fabricated "500+0+0=721"). Scoped to count-intent so a
#: stray "what leads to X" verb does not hijack the capability.
_LEADS = re.compile(
    r"\blead[_\s-]?scout\b|"
    r"\b(how many|number of|count of|total|the)\s+(leads?|prospects?|probate)\b|"
    r"\b(leads?|prospects?|probate)\s+(count|counts|today|so far|this week|breakdown|split|pipeline)\b|"
    r"\b(today'?s|new|fresh|how many)\s+leads?\b|"
    r"\blead counts?\b|\bpipeline\b|"
    r"\bprobate\s+(cases?|offers?|count|filings?)\b",
    re.I,
)
#: Live email / text SEND-COUNT questions — answered from mail_ledger, never the brain
#: (2026-06-13: the brain gave "I can't reach the ledger" a dozen turns running). Scoped to
#: send/count intent on mail nouns so it never hijacks a normal email-about-X message.
_MAIL = re.compile(
    r"\b(how many|number of|count of|total)\s+(e-?mails?|texts?|messages?|sms|sends?)\b|"
    r"\b(e-?mails?|texts?|messages?|sms|mail)\s+(sent|sends?|count|counts|today|so far|this morning|this week)\b|"
    r"\b(e-?mails?|texts?|messages?)\s+(did we|have we|were)\s+(sent|send)\b|"
    r"\b(did we|have we|were we)\s+(send|sent)\s+(any\s+)?(mail|e-?mails?|texts?|messages?|outreach|anything)\b|"
    r"\bany\s+(mail|e-?mails?|texts?|messages?|outreach)\s+sent\b|"
    r"\boutreach\s+(status|sent|today|so far|this morning|this week|going out)\b|"
    r"\bhow many\b[^.?!]*\b(e-?mails?|texts?|messages?)\b[^.?!]*\bsent\b|"
    r"\bmail\s+(count|ledger|sent)\b",
    re.I,
)
#: Ace's OWN standing duties — "what do you run/uphold every day", "list your jobs/crons",
#: "your responsibilities". Answered from the live launchd roster + health, never invented.
_JOBS = re.compile(
    r"\buphold\b|"                                    # Michael's word for the daily duties
    r"\b(responsibilit\w*|duties)\b|"                 # responsibilities / duties = the roster
    r"\b(your|the|every|all)\s+(jobs?|crons?|tasks?)\b|"
    r"\b(what|which)\s+(jobs?|crons?|services?|tasks?)\s+(do you|are you|you)\b|"
    r"\bwhat (do|are) you\b[^.?!]*\b(every\s+(single\s+)?day|daily|each\s+day|all day|day in)\b|"
    r"\blist\s+(your|every|all|them|the)\b[^.?!]*\b(jobs?|crons?|services?|duties|responsibilit)",
    re.I,
)
#: Live trading-engine / lab state — "your engine state", "what engines are live",
#: "lab state", "engine status". Grounded from live state; honest "I don't know" if unread.
_ENGINE = re.compile(
    r"\b(engine|lab)\s+(state|status|health)\b|"
    r"\bengine\s+lab\b|"
    r"\btrading\s+(edge|lab|engine|engines?|status|state|audit|panel)\b|"
    r"\b(what|which|how many)\s+engines?\s+(are\s+)?(live|running|firing|active|up|going)\b|"
    r"\bare\s+(your|the)\s+engines?\s+(live|running|firing|up|on)\b|"
    r"\b(your|the)\s+engines?\s+(live|running|firing|state|status)\b",
    re.I,
)

#: News/headlines intent — "news about X", "latest news on X", "what's happening
#: with X", "the headlines". Answered by the researcher-backed news capability
#: (real web fetch + grounded extraction), never a model inventing headlines.
#: Bare "headline(s)" deliberately needs a news-y qualifier ("the/today's/latest
#: headlines", "headlines for X") so "rewrite that headline" stays a normal turn.
_NEWS = re.compile(
    r"\bnews\s+(?:about|on|for|regarding)\b|"
    r"\b(?:latest|recent|any|today'?s)\s+news\b|"
    r"\bwhat'?s\s+(?:in\s+the\s+news|the\s+news)\b|"
    r"\bwhat(?:'?s|\s+is)\s+happening\s+with\b|"
    r"\b(?:the|today'?s|latest|morning|any)\s+headlines\b|"
    r"\bheadlines\s+(?:for|on|about|from)\b",
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

#: Sensitive PII intent — SSN/social-security questions must never hit LOCAL_QUICK
#: (the 3B improvises a policy refusal that :func:`utah.brain.is_refusal` once missed,
#: so the exchange was storable). Route to the brain for a structural no-fab answer.
_SENSITIVE_PII = re.compile(
    r"\b(ssn|social[\s-]?security(\s+number)?)\b|"
    r"\bsocial[\s-]?security\s+(number|#|no\.?)\b",
    re.I,
)


#: Self / project / identity questions — about Utah, Ace/AceOS, "who/what are you",
#: "what can you do", "what do you know/remember about …", "tell me about yourself".
#: The small local model answers these from its OWN (wrong) parametric knowledge — it
#: served a fabricated "Project Utah is a lightweight fork called Utah-pre". These must
#: be GROUNDED: the brain answers from recalled memory (the Utah corpus) under the
#: structural no-fab gate, or says "I don't know" — never a 3B guess about ourselves.
_SELF_OR_PROJECT = re.compile(
    r"\bproject\s+utah\b|\baceos\b|\butah\b|\bace\b|"
    r"\bwho\s+are\s+you\b|\bwhat\s+are\s+you\b|\bwhat\s+can\s+you\s+do\b|"
    r"\bwhat\s+do\s+you\s+do\b|\btell\s+me\s+about\s+(yourself|utah|ace|michael)\b|"
    r"\bwhat('?s| is)\s+your\s+(name|purpose|mission|goal)\b|"
    r"\bwhat\s+do\s+you\s+(know|remember)\s+about\b",
    re.I,
)


def is_sensitive_pii(text: str) -> bool:
    """True for a sensitive-PII question (SSN / social security). These must route to
    the brain — never LOCAL_QUICK — so policy refusals stay non-storable."""
    return bool(_SENSITIVE_PII.search(text or ""))


def is_factual_recall(text: str) -> bool:
    """True for an external/world-knowledge question ("who won…", "capital of…",
    "how tall is…"). These are what the brain's no-fab gate refuses when memory is
    cold — and exactly what the web can ground — so the learn-on-miss loop is scoped
    to them (personal/agentic misses don't trigger a web search)."""
    return bool(_FACTUAL_RECALL.search(text or ""))


def is_self_or_project(text: str) -> bool:
    """True for a self/project/identity question ("who are you", "what can you do",
    "what is Project Utah"). :mod:`utah.core` uses this to inject the live
    introspection self-model (:func:`utah.introspect.self_model`) as grounding facts
    on the brain pass — the answer comes from REAL daemon/memory state, never
    parametric guesswork."""
    return bool(_SELF_OR_PROJECT.search(text or ""))


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
    # A COMMAND to RUN a capability ("rerun the leads", "run outreach", "rerun probate",
    # "run the engines"). BEFORE _AGENTIC (so the generic "run"/"execute" verb doesn't get
    # swallowed into the brain) and BEFORE _LEADS (the bug: "rerun the lead scout" matched
    # the LEADS *read* capability and only reported counts — now it executes). A question
    # ("how many leads") has no run verb → is_action is False → falls through to the read.
    from utah import actions

    if actions.is_action(t):
        return Route.ACTION
    if _AGENTIC.search(t):
        return Route.BRAIN
    # Sensitive PII (SSN, social security) → brain, never the 3B local lane.
    if _SENSITIVE_PII.search(t):
        return Route.BRAIN
    if _WEATHER.search(t):
        return Route.WEATHER
    # Lead/pipeline counts → grounded ledger capability. BEFORE _TIME (so "today's lead
    # count" isn't swallowed by the today's-date matcher) and BEFORE _FACTUAL_RECALL (so
    # "how many leads" hits real Postgres, not the brain that invented "500+0+0=721").
    if _LEADS.search(t):
        return Route.LEADS
    # Email/text send counts → grounded mail_ledger capability. BEFORE _TIME (so "emails
    # today"/"sent this morning" isn't swallowed by the date matcher) — Ace KNOWS the
    # number instead of describing the code that writes it.
    if _MAIL.search(t):
        return Route.MAIL
    if _JOBS.search(t):
        return Route.JOBS
    if _ENGINE.search(t):
        return Route.ENGINE
    if _TIME.search(t):
        return Route.TIME
    if _BRIEF.search(t):
        return Route.BRIEF
    # News/headlines intent → the researcher-backed news capability (real web fetch +
    # grounded extraction), never a model inventing headlines. After WEATHER/LEADS/
    # TIME/BRIEF so the grounded capabilities keep winning ("what happened overnight"
    # stays BRIEF; "what's happening with the pipeline" stays LEADS); before the
    # knowledge/factual-recall checks so "latest news on the super bowl" pulls the
    # real, CURRENT web instead of a cold brain recall.
    if _NEWS.search(t):
        return Route.NEWS
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
    # Self / project / identity → the GROUNDED brain, never the 3B (which fabricates
    # about ourselves). After capabilities/packs so "weather in Utah" still wins; before
    # the local tiers so "what is Project Utah" / "what do you remember about X" ground.
    if _SELF_OR_PROJECT.search(t):
        return Route.BRAIN
    # Anything SUBSTANTIVE — reasoning verbs (why/explain/analyze/compare) or a long
    # query — goes to the Claude CLI brain, NOT the local 32B reasoner. deepseek-r1:32b
    # pinned ~54GB resident (a 128K-token KV cache) to give a slower, weaker answer than
    # the CLI. The local lane is reserved for trivially-fast turns; the heavy tier is
    # retired from routing (its model loads only if something still asks for it).
    if _REASONING.search(t) or len(t.split()) >= config.ROUTER_HEAVY_MIN_WORDS:
        return Route.BRAIN
    return Route.LOCAL_QUICK
