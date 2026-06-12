"""utah._probe_marker: the inert Tier-A landing spot for selfcode diff probes.

The module's whole contract is to exist, import cleanly, and carry its marker
docstring — ``utah/selfcode.py::_real_changed_files`` proves diff capture by
touching it. Nothing imports it, so when it goes missing no other test fails;
these tests make the suite notice (it has vanished from disk before).
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

MARKER_LINE = (
    "Diff-capture probe marker — an inert target file for selfcode probe runs."
)


def test_module_imports_without_raising():
    """A fresh import must execute the module body without raising — the other
    tests can be satisfied by a cached ``sys.modules`` entry, which would mask
    an on-disk regression. Evicting the cache forces real re-execution; safe
    here because the module is inert and nothing else holds a reference."""
    sys.modules.pop("utah._probe_marker", None)
    mod = importlib.import_module("utah._probe_marker")
    assert mod is sys.modules["utah._probe_marker"]


def test_module_imports_and_exposes_marker_docstring():
    mod = importlib.import_module("utah._probe_marker")
    assert mod.__doc__ is not None
    assert mod.__doc__.splitlines()[0] == MARKER_LINE


def test_docstring_is_nonempty_and_at_least_10_chars():
    """A trivial stub docstring ("x", "todo") would defeat the marker's job of
    documenting why the file exists — demand at least a sentence's worth."""
    mod = importlib.import_module("utah._probe_marker")
    assert mod.__doc__ is not None
    assert mod.__doc__.strip()
    assert len(mod.__doc__.strip()) >= 10


def test_source_has_module_level_docstring():
    """The docstring must be a literal first statement in the source file,
    not merely a ``__doc__`` attribute set at import time — the file has been
    emptied on disk before while a cached import looked healthy."""
    mod = importlib.import_module("utah._probe_marker")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree)
    assert docstring is not None
    assert docstring.strip()


def test_module_is_inert():
    """No public names: a probe edit here must never gain runtime behavior."""
    mod = importlib.import_module("utah._probe_marker")
    assert [name for name in vars(mod) if not name.startswith("_")] == []


def test_module_lives_inside_the_utah_package():
    mod = importlib.import_module("utah._probe_marker")
    source = Path(mod.__file__)
    assert source.name == "_probe_marker.py"
    assert source.parent.name == "utah"


def test_defines_module_level_version_string():
    """``__version__`` must be a nonempty string assigned at module level in
    the source file — not injected into a cached module object at runtime.
    Being dunder-prefixed it stays within the inertness contract
    (``test_module_is_inert`` only forbids public names)."""
    sys.modules.pop("utah._probe_marker", None)
    mod = importlib.import_module("utah._probe_marker")
    assert isinstance(mod.__version__, str)
    assert mod.__version__.strip()

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "__version__" in assigned


def test_defines_module_level_all_list():
    """``__all__`` must be a list assigned at module level in the source file —
    not injected into a cached module object at runtime. It must be empty:
    the inertness contract (``test_module_is_inert``) means there are no
    public names to export, so any entry would advertise a name that does
    not exist."""
    sys.modules.pop("utah._probe_marker", None)
    mod = importlib.import_module("utah._probe_marker")
    assert isinstance(mod.__all__, list)
    assert mod.__all__ == []

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "__all__" in assigned


def test_exposes_a_constant_when_imported_via_importlib():
    """The module must expose a callable or constant once importlib loads it.
    Its inertness contract (``test_module_is_inert``) forbids public callables,
    so the exposure is the docstring: a string constant bound to ``__doc__``
    by the literal first statement of the file — not a function masquerading
    as documentation, and not None as it would be for a bare empty file."""
    sys.modules.pop("utah._probe_marker", None)
    mod = importlib.import_module("utah._probe_marker")
    assert any(
        callable(value) or isinstance(value, (str, bytes, int, float, complex))
        for value in vars(mod).values()
    )
    doc = vars(mod)["__doc__"]
    assert isinstance(doc, str) and doc.strip()
    assert not callable(doc)
