"""The ``utah.knowledge`` package surface: the curated packs are enumerable
(``PACKS``/``__all__``) and lazily importable (PEP 562), and ``answer()`` fans a
query across every pack — verbatim pack text on a match, ``None`` otherwise, and
one broken pack can never take down the router's knowledge route."""
from __future__ import annotations

import pytest

import utah.knowledge as knowledge


def test_douglas_resolves_lazily_and_is_cached():
    douglas = knowledge.douglas
    assert douglas.__name__ == "utah.knowledge.douglas"
    assert knowledge.douglas is douglas


def test_packs_enumerates_every_curated_pack():
    assert "douglas" in knowledge.PACKS
    assert set(knowledge.PACKS) <= set(knowledge.__all__)


def test_answer_fans_out_to_matching_pack():
    out = knowledge.answer("what are mark douglas's trading rules")
    assert out is not None and "Anything can happen." in out


def test_answer_returns_none_when_no_pack_matches():
    assert knowledge.answer("what's the weather in atlanta") is None
    assert knowledge.answer("") is None
    assert knowledge.answer(None) is None


def test_answer_survives_a_broken_pack(monkeypatch):
    """One pack import/answer failure degrades to no-match — the knowledge route
    must never crash the turn."""
    monkeypatch.setattr(knowledge, "PACKS", ("definitely_not_a_module",))
    assert knowledge.answer("mark douglas rules") is None


def test_unknown_attribute_raises_named_attribute_error():
    with pytest.raises(AttributeError, match="utah.knowledge"):
        knowledge.nope
