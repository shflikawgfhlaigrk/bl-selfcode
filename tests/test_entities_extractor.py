"""The pluggable extractor seam in utah.entities: an injected NER is used,
its output is de-duped and length-filtered, a throwing NER falls back to the
regex pass (the graph never breaks), and None restores the default."""
from __future__ import annotations

import pytest

from utah import entities


@pytest.fixture(autouse=True)
def _restore_extractor():
    yield
    entities.set_extractor(None)


def test_injected_extractor_is_used_instead_of_regex():
    entities.set_extractor(lambda text: ["acme corp", "Michael"])
    assert entities.extract("anything lowercase at all") == ["acme corp", "Michael"]


def test_injected_output_is_deduped_and_length_filtered():
    entities.set_extractor(lambda text: ["Utah", "Utah", "X", "", None, "Ace"])
    assert entities.extract("whatever") == ["Utah", "Ace"]


def test_injected_empty_result_is_honored_not_overridden():
    """A real NER saying 'no entities' is the answer — no silent regex second-guess."""
    entities.set_extractor(lambda text: [])
    assert entities.extract("Michael lives in Gulf Shores") == []


def test_throwing_extractor_falls_back_to_regex():
    def broken(text):
        raise RuntimeError("model crashed")

    entities.set_extractor(broken)
    assert entities.extract("Michael lives in Gulf Shores") == ["Michael", "Gulf Shores"]


def test_extractor_returning_none_falls_back_to_empty():
    entities.set_extractor(lambda text: None)
    assert entities.extract("Michael moved") == []


def test_set_extractor_none_restores_the_regex_default():
    entities.set_extractor(lambda text: ["Injected"])
    entities.set_extractor(None)
    assert entities.extract("Michael moved") == ["Michael"]


def test_alphanumeric_spans_survive():
    assert entities.extract("the B2B SaaS pitch") == ["B2B SaaS"]
    assert entities.extract("Agent007 reported") == ["Agent007"]


def test_normalize_is_idempotent_and_total():
    assert entities.normalize("") == ""
    once = entities.normalize("  Gulf   SHORES ")
    assert entities.normalize(once) == once == "gulf shores"


def test_extract_total_on_adversarial_inputs_with_injected_extractor():
    entities.set_extractor(lambda text: ["ok"])
    for text in ("", "\x00", "Ü" * 5000):
        entities.extract(text)  # must never raise
