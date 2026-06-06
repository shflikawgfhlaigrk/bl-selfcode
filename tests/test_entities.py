"""Entity extraction: deterministic, noise-free, total."""
from __future__ import annotations

from utah import entities


def test_extracts_proper_nouns_in_order():
    assert entities.extract("Michael lives in Gulf Shores") == ["Michael", "Gulf Shores"]


def test_deduplicates_keeping_first_occurrence():
    assert entities.extract("Newman met Newman in Utah") == ["Newman", "Utah"]


def test_drops_noise_words():
    assert entities.extract("Where does he live? I think The answer is No") == []


def test_strips_leading_and_trailing_noise_from_spans():
    assert entities.extract("Remember Michael moved") == ["Michael"]
    assert entities.extract("Does Michael Will") == ["Michael"]


def test_question_form_keeps_only_the_entity():
    assert entities.extract("Where does Michael live?") == ["Michael"]


def test_empty_and_degenerate_input():
    assert entities.extract("") == []
    assert entities.extract("   ") == []
    assert entities.extract("all lower case words") == []


def test_qa_turn_prefixes_are_not_entities():
    out = entities.extract("Q: where is Newman\nA: Newman is in Utah")
    assert out == ["Newman", "Utah"]


def test_normalize_folds_case_and_whitespace():
    assert entities.normalize("  Gulf   Shores ") == "gulf shores"
    assert entities.normalize("MICHAEL") == "michael"


def test_normalized_set_used_for_graph_matching():
    a = entities.normalized_set("Michael lives in Gulf Shores")
    b = entities.normalized_set("Michael lives in Utah")
    assert a & b == {"michael"}


def test_never_raises_on_weird_input():
    for text in ("////", "123 456", "A" * 10_000, "\x00\x01", "Ünïcödé Wörds"):
        entities.extract(text)  # must not raise
