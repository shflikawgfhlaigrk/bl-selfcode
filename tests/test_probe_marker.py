"""utah._probe_marker: the inert Tier-A landing spot for selfcode diff probes.

The module's whole contract is to exist, import cleanly, and carry its marker
docstring — ``utah/selfcode.py::_real_changed_files`` proves diff capture by
touching it. Nothing imports it, so when it goes missing no other test fails;
these tests make the suite notice (it has vanished from disk before).
"""
from __future__ import annotations

import importlib
from pathlib import Path

MARKER_LINE = (
    "Diff-capture probe marker — an inert target file for selfcode probe runs."
)


def test_module_imports_and_exposes_marker_docstring():
    mod = importlib.import_module("utah._probe_marker")
    assert mod.__doc__ is not None
    assert mod.__doc__.splitlines()[0] == MARKER_LINE


def test_module_is_inert():
    """No public names: a probe edit here must never gain runtime behavior."""
    mod = importlib.import_module("utah._probe_marker")
    assert [name for name in vars(mod) if not name.startswith("_")] == []


def test_module_lives_inside_the_utah_package():
    mod = importlib.import_module("utah._probe_marker")
    source = Path(mod.__file__)
    assert source.name == "_probe_marker.py"
    assert source.parent.name == "utah"
