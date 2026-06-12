"""The package facade (utah/memory/__init__.py) is the API every caller imports.
Its __all__ must be real (no phantom exports — a stale name breaks star-imports
with AttributeError), complete, and re-export the SAME objects the defining
modules own (no shadowing, so test injection via set_backend stays effective)."""
from __future__ import annotations

from utah import memory
from utah.memory import exceptions as exceptions_mod
from utah.memory import logic as logic_mod
from utah.memory import pipeline as pipeline_mod


def test_every_exported_name_resolves():
    missing = [n for n in memory.__all__ if not hasattr(memory, n)]
    assert missing == []


def test_no_duplicate_exports():
    assert len(memory.__all__) == len(set(memory.__all__))


def test_star_import_succeeds():
    """`from utah.memory import *` must not raise — phantom __all__ names do."""
    ns: dict = {}
    exec("from utah.memory import *", ns)  # noqa: S102 — constant import statement
    assert "recall" in ns and "store" in ns and "AdmissionDenied" in ns


def test_facade_reexports_are_the_defining_objects():
    """Identity, not equality: a copy would break monkeypatching/injection."""
    assert memory.recall is pipeline_mod.recall
    assert memory.store is pipeline_mod.store
    assert memory.answer is pipeline_mod.answer
    assert memory.set_backend is pipeline_mod.set_backend
    assert memory.core_recall is pipeline_mod.core_recall
    assert memory.rrf_fuse is logic_mod.rrf_fuse
    assert memory.decide_write is logic_mod.decide_write
    assert memory.AdmissionDenied is exceptions_mod.AdmissionDenied
    assert memory.MemoryUnavailable is exceptions_mod.MemoryUnavailable


def test_pipeline_all_is_real_and_complete():
    """The regression this file exists for: pipeline.__all__ listed five names the
    module never imported (star-import = AttributeError) and omitted three public
    functions it DOES define (core_recall, list_memories, list_entities)."""
    missing = [n for n in pipeline_mod.__all__ if not hasattr(pipeline_mod, n)]
    assert missing == []
    for public in ("core_recall", "list_memories", "list_entities"):
        assert public in pipeline_mod.__all__
