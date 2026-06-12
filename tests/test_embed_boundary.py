"""FastEmbedEmbedder internals + the process-global embedder seam: every model
failure mode surfaces as a structured EmbedError (never ImportError/RuntimeError
into the memory pipeline), output is coerced to plain floats, and the global
getter is race-free."""
from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from utah import UtahError, embed


class _Vec:
    """fastembed yields numpy arrays — all the wrapper relies on is .tolist()."""

    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return list(self._values)


class _StubModel:
    def __init__(self, vectors=None, error: Exception | None = None):
        self.vectors = vectors if vectors is not None else []
        self.error = error

    def embed(self, texts):
        if self.error is not None:
            raise self.error
        return iter(self.vectors)


def _embedder_with(model) -> embed.FastEmbedEmbedder:
    e = embed.FastEmbedEmbedder()
    e._model = model  # preset: exercises embed() without a model download
    return e


def test_embed_error_is_a_utah_error():
    assert issubclass(embed.EmbedError, UtahError)


def test_model_output_is_coerced_to_plain_floats():
    e = _embedder_with(_StubModel(vectors=[_Vec([1, 2, 3])]))
    out = e.embed("hi")
    assert out == [1.0, 2.0, 3.0]
    assert all(type(x) is float for x in out)


def test_model_yielding_nothing_is_a_structured_error():
    e = _embedder_with(_StubModel(vectors=[]))
    with pytest.raises(embed.EmbedError, match="no vector"):
        e.embed("hi")


def test_model_raising_is_a_structured_error():
    e = _embedder_with(_StubModel(error=RuntimeError("onnx session died")))
    with pytest.raises(embed.EmbedError, match="embedding failed"):
        e.embed("hi")


def test_missing_fastembed_is_a_structured_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)  # import → ImportError
    with pytest.raises(embed.EmbedError, match="not installed"):
        embed.FastEmbedEmbedder().embed("hi")


def test_model_init_failure_is_a_structured_error(monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("model download refused")

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=explode))
    with pytest.raises(embed.EmbedError, match="could not load"):
        embed.FastEmbedEmbedder("some-model").embed("hi")


def test_model_loads_once_across_calls(monkeypatch):
    loads = {"n": 0}

    class CountingModel(_StubModel):
        pass

    def make_model(*a, **k):
        loads["n"] += 1
        return CountingModel(vectors=[_Vec([1.0])])

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=make_model))
    e = embed.FastEmbedEmbedder("m")
    e.embed("a")
    e._model.vectors = [_Vec([2.0])]
    e.embed("b")
    assert loads["n"] == 1  # lazy-loaded exactly once, then reused


def test_get_embedder_is_one_instance_across_threads():
    embed.set_embedder(None)
    seen: list[object] = []
    threads = [threading.Thread(target=lambda: seen.append(embed.get_embedder()))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len({id(x) for x in seen}) == 1
