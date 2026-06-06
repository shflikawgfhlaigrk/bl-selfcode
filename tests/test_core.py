"""The tell() loop: every failure branch handled, never crashes, never fabricates.
Plus the CLI entrypoint."""
from __future__ import annotations

import pytest

from utah import brain, core, memory
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
