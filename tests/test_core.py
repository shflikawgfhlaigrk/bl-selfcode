"""The tell() loop: every failure branch handled, never crashes, never fabricates.
Plus the CLI entrypoint."""
from __future__ import annotations

import json

import pytest

from utah import brain, core, local, memory
from utah.objects import ReplySource
from tests.fakes import basis, blend


# --- tell(): the four branches -------------------------------------------------------

def test_memory_path_confident_hit_answers_without_the_brain(mem, fake_brain):
    fake_brain.respond = AssertionError("the paid lane must not be used")
    mem.embedder.register("Michael lives in Utah", basis(0))
    mem.embedder.register("Where does Michael live?", blend(basis(0), basis(1), 0.9))
    memory.store("Michael lives in Utah", source="fact", confidence=0.8)

    reply = core.tell("Where does Michael live?")

    assert reply.source is ReplySource.MEMORY
    assert reply.text == "Michael lives in Utah"
    assert reply.hits


def test_brain_path_grounded_in_recalled_context(mem, fake_brain):
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("should I get coffee or tea for Michael?", blend(basis(0), basis(1), 0.40))
    memory.store("Michael prefers tea", source="fact")
    fake_brain.respond = "Get tea — Michael prefers it."

    reply = core.tell("should I get coffee or tea for Michael?")

    assert reply.source is ReplySource.BRAIN
    assert reply.text == "Get tea — Michael prefers it."
    assert "Michael prefers tea" in fake_brain.last_prompt  # grounded
    # the exchange was remembered for compounding
    turns = [r for r in mem.store.rows.values() if r.source == "turn"]
    assert len(turns) == 1
    assert "Get tea" in turns[0].content


def test_brain_unavailable_is_honest_and_stores_nothing(mem):
    from tests.fakes import unavailable_runner

    brain.set_runner(unavailable_runner("cli down"))
    reply = core.tell("what's the plan?")
    assert reply.source is ReplySource.UNAVAILABLE
    assert "unavailable" in reply.text
    assert all(r.source != "turn" for r in mem.store.rows.values())


def test_memory_down_degrades_to_brain_with_no_context(mem, fake_brain):
    mem.store.fail = True
    fake_brain.respond = "Here is what I think."
    reply = core.tell("what's the plan?")
    assert reply.source is ReplySource.BRAIN
    assert reply.text == "Here is what I think."
    assert "(none)" in fake_brain.last_prompt  # no fabricated context


def test_i_dont_know_turns_are_not_stored(mem, fake_brain):
    fake_brain.respond = ""
    reply = core.tell("something unanswerable")
    assert reply.text == brain.I_DONT_KNOW
    assert all(r.source != "turn" for r in mem.store.rows.values())


# --- tell(): the L1 tier (capabilities + local models in front of the brain) ---

def test_weather_route_uses_capability_not_the_brain(mem, fake_brain, monkeypatch):
    fake_brain.respond = AssertionError("the paid lane must not be used for weather")
    monkeypatch.setattr("utah.product.weather.current", lambda *a, **k: "Gulf Shores, AL: sunny, 75°F.")
    reply = core.tell("what's the weather")
    assert reply.source is ReplySource.CAPABILITY
    assert "75°F" in reply.text
    # capability replies are LIVE state — never stored as durable turns
    assert all(r.source != "turn" for r in mem.store.rows.values())


def test_brief_route_uses_capability_not_the_brain(mem, fake_brain, monkeypatch):
    fake_brain.respond = AssertionError("the paid lane must not be used for the brief")
    monkeypatch.setattr("utah.product.brief.run", lambda *a, **k: {"brief": "UTAH MORNING BRIEF\n..."})
    reply = core.tell("give me the morning brief")
    assert reply.source is ReplySource.CAPABILITY
    assert "MORNING BRIEF" in reply.text


def test_local_route_answers_quick_without_the_brain(mem, fake_brain):
    fake_brain.respond = AssertionError("the paid lane must not be used for a quick local turn")
    local.set_runner(lambda payload, timeout: {"content": "Hi there!", "thinking": ""})
    reply = core.tell("say hi")
    assert reply.source is ReplySource.LOCAL
    assert reply.text == "Hi there!"
    # a small local model is NOT a trusted durable source — its turns are threaded
    # for follow-ups but never stored, so a hallucination can never poison recall.
    assert all(r.source != "turn" for r in mem.store.rows.values())


def test_capability_wins_over_a_stale_memory_hit(mem, fake_brain, monkeypatch):
    # the live bug: a memory turn served day-old weather (74°) instead of the live
    # capability. A capability is LIVE and must win over even a confident memory hit.
    fake_brain.respond = AssertionError("brain must not run")
    mem.embedder.register("It's 74 and partly cloudy", basis(0))
    mem.embedder.register("what's the weather", blend(basis(0), basis(1), 0.99))
    memory.store("It's 74 and partly cloudy", source="fact", confidence=0.9)
    monkeypatch.setattr("utah.product.weather.current", lambda *a, **k: "LIVE: 80°F and clear.")
    reply = core.tell("what's the weather")
    assert reply.source is ReplySource.CAPABILITY
    assert "LIVE" in reply.text       # the live capability, not the stale memory


def test_knowledge_pack_answers_verbatim_never_a_model(mem, fake_brain):
    # the bug this fixes: a 3B hallucinated the Douglas rules. The pack must answer
    # deterministically — no local model, no brain, the REAL rules.
    fake_brain.respond = AssertionError("brain must not be used for a curated pack")

    def local_must_not_run(payload, timeout):
        raise AssertionError("the local model must not regenerate a curated pack")

    local.set_runner(local_must_not_run)
    reply = core.tell("what are mark douglas's trading rules")
    assert reply.source is ReplySource.CAPABILITY
    assert "Anything can happen." in reply.text          # truth 1, verbatim
    assert "I predefine the risk of every trade." in reply.text  # principle 2, verbatim
    assert all(r.source != "turn" for r in mem.store.rows.values())  # not stored as a turn


def test_reasoning_query_uses_the_heavy_local_model(mem, fake_brain):
    fake_brain.respond = AssertionError("brain must not be used")
    seen = {}
    local.set_runner(lambda payload, timeout: seen.update(model=payload["model"]) or {"content": "Because chop.", "thinking": "t"})
    from utah import config
    reply = core.tell("why does the engine lose money on ranges")
    assert reply.source is ReplySource.LOCAL
    assert seen["model"] == config.LOCAL_HEAVY_MODEL


def test_local_refusal_escalates_to_the_brain(mem, fake_brain):
    local.set_runner(lambda payload, timeout: {"content": "I don't know.", "thinking": ""})
    fake_brain.respond = "The real answer from Claude."
    reply = core.tell("say hi")
    assert reply.source is ReplySource.BRAIN
    assert reply.text == "The real answer from Claude."


def test_local_unavailable_escalates_to_the_brain(mem, fake_brain):
    def boom(payload, timeout):
        raise local.LocalUnavailable("ollama down")

    local.set_runner(boom)
    fake_brain.respond = "Claude picked it up."
    reply = core.tell("say hi")
    assert reply.source is ReplySource.BRAIN
    assert reply.text == "Claude picked it up."


def test_agentic_query_goes_straight_to_the_brain(mem, fake_brain):
    def must_not_run(payload, timeout):
        raise AssertionError("local must not be called for an agentic query")

    local.set_runner(must_not_run)
    fake_brain.respond = "wrote the function"
    reply = core.tell("write a python function to sort a list")
    assert reply.source is ReplySource.BRAIN


def test_local_answer_is_grounded_in_recalled_context(mem, fake_brain):
    fake_brain.respond = AssertionError("brain must not be used")
    mem.embedder.register("Michael prefers tea", basis(0))
    mem.embedder.register("does Michael like tea", blend(basis(0), basis(1), 0.40))
    memory.store("Michael prefers tea", source="fact")
    seen = {}
    local.set_runner(lambda payload, timeout: seen.update(p=payload) or {"content": "Yes, tea.", "thinking": ""})
    reply = core.tell("does Michael like tea")
    assert reply.source is ReplySource.LOCAL
    user = next(m["content"] for m in seen["p"]["messages"] if m["role"] == "user")
    assert "Michael prefers tea" in user  # the local model was grounded


def test_core_facts_are_injected_first_in_brain_context(mem, fake_brain):
    # source='core' rows must arrive in the brain prompt before recalled memory
    # so that identity/standing facts always ground the brain — the StoreBackend
    # protocol path (core_rows) must be exercised, not just a FakeStore _tx hack.
    memory.store("Michael lives in Gulf Shores, AL", source="core", confidence=1.0)
    fake_brain.respond = "I know where you live."

    core.tell("do you know where I live?")

    assert "CORE (always true):" in fake_brain.last_prompt
    assert "Gulf Shores" in fake_brain.last_prompt


def test_store_failure_does_not_eat_the_reply(mem, fake_brain):
    fake_brain.respond = "An answer."
    original_insert = mem.store.insert

    def broken_insert(*args, **kwargs):
        raise memory.MemoryUnavailable("disk fell over")

    mem.store.insert = broken_insert
    try:
        reply = core.tell("a question")
    finally:
        mem.store.insert = original_insert
    assert reply.source is ReplySource.BRAIN
    assert reply.text == "An answer."


def test_empty_input_is_handled(mem, fake_brain):
    reply = core.tell("   ")
    assert reply.source is ReplySource.UNAVAILABLE
    assert mem.store.rows == {}


# --- learn-on-miss: find → understand → remember, then answer -------------------------

_EIFFEL = "The Eiffel Tower is 330 metres tall."
_EIFFEL_Q = "how tall is the eiffel tower"
#: What researcher.gather returns — raw fetched page text (NOT extracted facts); the
#: brain grounds on it in one pass. The slow per-source extraction is gone.
_EIFFEL_WEB = f"[Eiffel Tower - Wikipedia]\nThe tower is in Paris. {_EIFFEL} It was built in 1889."


def _ground_after_learn(prompt: str) -> str:
    """A brain that refuses cold, but answers once the fetched web text is in CONTEXT."""
    return _EIFFEL if "330 metres" in prompt else brain.I_DONT_KNOW


def _stub_gather(web: str = _EIFFEL_WEB):
    """A researcher.gather that returns fetched web text (no brain calls, no store —
    the real fast path), and counts its calls. Returns ``(fake_gather, calls)``."""
    calls = {"n": 0}

    def fake_gather(query, **kw):
        calls["n"] += 1
        return web

    return fake_gather, calls


def test_learn_on_miss_fetches_grounds_and_remembers(mem, fake_brain, monkeypatch):
    # The exact gap Michael hit: a factual question, cold memory → brain says "I
    # don't know." Instead of stopping there, Utah fetches the web, re-reasons over
    # the real page text, answers — and remembers it so the next ask compounds.
    fake_brain.respond = _ground_after_learn
    fake_gather, calls = _stub_gather()
    monkeypatch.setattr("utah.product.researcher.gather", fake_gather)

    reply = core.tell(_EIFFEL_Q)

    assert reply.source is ReplySource.LEARNED
    assert "330 metres" in reply.text
    assert calls["n"] == 1  # it actually went and fetched
    # the grounded exchange is remembered → the next identical ask is instant recall
    assert any(r.source == "turn" and "330 metres" in r.content for r in mem.store.rows.values())


def test_learn_on_miss_grounds_on_fetched_web_text(mem, fake_brain, monkeypatch):
    # Regression for the live Burj Khalifa bug: the retry must ground on the fetched
    # source text (which carries the canonical 828m AND a 555m deck distractor), not a
    # lossy recall that dropped the right fact. The brain reads it all and answers 828m.
    web = ("[Burj Khalifa - Wikipedia]\nThe SKY deck is at about 555 metres. "
           "The architectural height is 828 metres, recognised by CTBUH.")
    fake_brain.respond = lambda prompt: "828 metres." if "828 metres" in prompt else brain.I_DONT_KNOW
    monkeypatch.setattr("utah.product.researcher.gather", lambda query, **kw: web)

    reply = core.tell("how tall is the burj khalifa in metres")

    assert reply.source is ReplySource.LEARNED
    assert "828" in reply.text
    # the retry prompt carried the fetched web text, in its own labelled block
    assert "FRESHLY RESEARCHED" in fake_brain.last_prompt
    assert "828 metres" in fake_brain.last_prompt and "555 metres" in fake_brain.last_prompt


def test_learn_on_miss_skips_non_factual_personal_misses(mem, fake_brain, monkeypatch):
    # A personal / non-knowledge miss must NOT trigger a web fetch (keeps latency low)
    # — it stays an honest "I don't know."
    fake_brain.respond = brain.I_DONT_KNOW
    fake_gather, calls = _stub_gather()
    monkeypatch.setattr("utah.product.researcher.gather", fake_gather)

    reply = core.tell("what should I name my dog")

    assert reply.text == brain.I_DONT_KNOW
    assert calls["n"] == 0  # no web fetch for a non-factual miss


def test_learn_on_miss_keeps_honest_refusal_when_web_finds_nothing(mem, fake_brain, monkeypatch):
    # Factual question, but the web yields nothing (block/empty) → no fabrication, the
    # honest "I don't know." stands (learning never invents an answer).
    fake_brain.respond = brain.I_DONT_KNOW
    monkeypatch.setattr("utah.product.researcher.gather", lambda query, **kw: "")

    reply = core.tell("who won the 1923 world series")

    assert reply.text == brain.I_DONT_KNOW
    assert all(r.source != "turn" for r in mem.store.rows.values())


def test_learn_on_miss_disabled_by_flag(mem, fake_brain, monkeypatch):
    # The deploy seam: with the flag off, a factual miss stays an honest refusal.
    fake_brain.respond = brain.I_DONT_KNOW
    monkeypatch.setattr("utah.config.LEARN_ON_MISS", False)
    fake_gather, calls = _stub_gather()
    monkeypatch.setattr("utah.product.researcher.gather", fake_gather)

    reply = core.tell(_EIFFEL_Q)

    assert reply.text == brain.I_DONT_KNOW
    assert calls["n"] == 0


def test_learn_on_miss_streams_grounded_answer(mem, fake_brain, monkeypatch):
    # The live chat-box / voice path: a factual cold miss fetches the web (visible as a
    # "looking it up" thinking line) then streams a GROUNDED answer, not a refusal.
    from tests.fakes import ScriptedStreamRunner

    def _answer_lines(prompt: str) -> list[str]:
        text = _EIFFEL if "330 metres" in prompt else brain.I_DONT_KNOW
        start = json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 1, "content_block": {"type": "text"}}})
        delta = json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "text_delta", "text": text}}})
        return [start, delta]

    brain.set_stream_runner(ScriptedStreamRunner(_answer_lines))
    fake_gather, calls = _stub_gather()
    monkeypatch.setattr("utah.product.researcher.gather", fake_gather)

    events = list(core.tell_stream(_EIFFEL_Q))

    assert ("source", "learned") in events
    assert calls["n"] == 1
    answer = "".join(c for ch, c in events if ch == "answer")
    assert "330 metres" in answer
    assert any(r.source == "turn" and "330 metres" in r.content for r in mem.store.rows.values())


# --- CLI entrypoint -------------------------------------------------------------------

def test_cli_remember(mem, fake_brain, capsys):
    code = core.main(["--remember", "Michael", "lives", "in", "Utah"])
    assert code == 0
    out = capsys.readouterr().out
    assert "stored id" in out
    rows = list(mem.store.rows.values())
    assert rows and rows[0].source == "fact"


def test_cli_remember_empty_is_an_error(mem, capsys):
    assert core.main(["--remember"]) == 2
    assert "nothing to remember" in capsys.readouterr().err


def test_cli_init(mem, capsys):
    assert core.main(["--init"]) == 0
    assert mem.store.schema_inits == 1
    assert "schema ready" in capsys.readouterr().out


def test_cli_question(mem, fake_brain, capsys):
    fake_brain.respond = "All systems green."
    assert core.main(["how", "are", "things?"]) == 0
    assert "[brain] All systems green." in capsys.readouterr().out


def test_cli_decay(mem, capsys):
    assert core.main(["--decay"]) == 0
    assert "archived 0" in capsys.readouterr().out


def test_cli_consolidate(mem, fake_brain, capsys):
    fake_brain.respond = "[]"
    assert core.main(["--consolidate"]) == 0
    assert "utah consolidate:" in capsys.readouterr().out


def test_cli_structured_failure_exits_nonzero(mem, capsys):
    mem.store.fail = True
    assert core.main(["--decay"]) == 1
    assert "utah:" in capsys.readouterr().err


def test_cli_help(capsys):
    assert core.main(["--help"]) == 0
    assert "usage" in capsys.readouterr().out


def test_cli_closes_the_backend(mem):
    core.main(["--decay"])
    assert mem.store.closed is True
