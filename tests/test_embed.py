"""The embedding boundary: injectable, validated, structured failures."""
from __future__ import annotations

import math

import pytest

from utah import config, embed
from tests.fakes import FakeEmbedder, basis


def test_injected_embedder_is_used(fake_embedder):
    fake_embedder.register("hello", basis(0))
    assert embed.embed("hello") == basis(0)
    assert fake_embedder.calls == ["hello"]


def test_empty_text_is_an_embed_error(fake_embedder):
    with pytest.raises(embed.EmbedError, match="empty"):
        embed.embed("")
    with pytest.raises(embed.EmbedError, match="empty"):
        embed.embed("   \n  ")


def test_wrong_dimension_is_an_embed_error(fake_embedder):
    fake_embedder.register("short", [1.0, 2.0])
    with pytest.raises(embed.EmbedError, match="dim"):
        embed.embed("short")


def test_non_finite_values_are_an_embed_error(fake_embedder):
    bad = basis(0)
    bad[5] = math.nan
    fake_embedder.register("nan", bad)
    with pytest.raises(embed.EmbedError, match="non-finite"):
        embed.embed("nan")


def test_arbitrary_embedder_exception_becomes_embed_error(fake_embedder):
    fake_embedder.fail = True
    with pytest.raises(embed.EmbedError, match="embedder raised"):
        embed.embed("anything")


def test_set_embedder_none_restores_default():
    fake = FakeEmbedder()
    embed.set_embedder(fake)
    assert embed.get_embedder() is fake
    embed.set_embedder(None)
    assert isinstance(embed.get_embedder(), embed.FastEmbedEmbedder)


def test_default_embedder_without_fastembed_is_structured():
    """In an env without fastembed, the failure is EmbedError, not ImportError."""
    embed.set_embedder(None)
    try:
        import fastembed  # noqa: F401
        pytest.skip("fastembed installed here; the lazy-import path is for bare envs")
    except ImportError:
        pass
    with pytest.raises(embed.EmbedError):
        embed.embed("hello world")


def test_hash_vectors_are_deterministic_and_unit_norm():
    fake = FakeEmbedder()
    v1 = fake.embed("some text")
    v2 = fake.embed("some text")
    assert v1 == v2
    assert len(v1) == config.EMBED_DIM
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9
