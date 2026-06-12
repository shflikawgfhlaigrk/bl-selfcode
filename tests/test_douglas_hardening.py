"""Douglas pack hardening: the seed path is a true boundary (a dead memory
backend yields 0, never a traceback), the CLI exit code is honest (partial or
zero seeding is a nonzero exit, not a fake success), and the matcher/render
contracts hold on edge inputs."""
from __future__ import annotations

from utah.knowledge import douglas


# --- seed: never-raises boundary -------------------------------------------------

def test_seed_with_dead_store_returns_zero_not_traceback():
    def dead_store(content, *, source, confidence):
        raise RuntimeError("postgres down")

    assert douglas.seed(store=dead_store) == 0


def test_seed_survives_store_resolution_failure(monkeypatch):
    """Even RESOLVING the default store (importing utah.memory) can fail when the
    venv/db is broken — the seed must report 0, not crash the cron/CLI."""
    import builtins

    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "utah" and args and args[2] and "memory" in args[2]:
            raise ImportError("memory backend unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    assert douglas.seed() == 0


# --- honest CLI exit ---------------------------------------------------------------

def test_main_exits_zero_only_when_every_fact_seeded(monkeypatch):
    monkeypatch.setattr(douglas, "seed", lambda **kw: len(douglas.FACTS))
    assert douglas.main() == 0
    monkeypatch.setattr(douglas, "seed", lambda **kw: len(douglas.FACTS) - 2)
    assert douglas.main() == 1                  # partial seed must NOT report success
    monkeypatch.setattr(douglas, "seed", lambda **kw: 0)
    assert douglas.main() == 1


# --- matcher / render edges ----------------------------------------------------------

def test_matches_handles_none_and_empty_and_non_topics():
    assert douglas.matches(None) is False
    assert douglas.matches("") is False
    assert douglas.matches("douglas county property records") is False  # not the author


def test_facts_are_numbered_and_complete():
    truths = [f for f in douglas.FACTS if "fundamental truth" in f]
    principles = [f for f in douglas.FACTS if "principle of consistency" in f]
    assert [f"truth {i}" in t for i, t in enumerate(truths, 1)].count(True) == 5
    assert len(truths) == 5 and len(principles) == 7
    assert len(douglas.FACTS) == 13             # summary + 5 + 7, nothing else


def test_render_is_deterministic():
    assert douglas.render() == douglas.render()


def test_answer_is_none_off_topic_and_verbatim_on_topic():
    assert douglas.answer("recipe for pancakes") is None
    out = douglas.answer("explain trading in the zone")
    assert out is not None
    for p in douglas.SEVEN_PRINCIPLES:
        assert p in out
