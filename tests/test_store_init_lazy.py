"""utah.store lazy-loading contract — PROVE the laziness claim, not just the
happy path. The docstring's whole reason to exist is "importing the package
never pays the OLAP import cost"; that is only true if a fresh interpreter
really does defer the submodule import until first attribute touch.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


def test_package_import_really_is_lazy_in_a_fresh_interpreter():
    """The load-bearing claim: `import utah.store` must NOT import the OLAP tier;
    touching `.olap` must. Proven in a clean child interpreter (this process has
    long since imported olap via other tests)."""
    code = (
        "import sys\n"
        "import utah.store\n"
        "assert 'utah.store.olap' not in sys.modules, 'olap imported eagerly'\n"
        "_ = utah.store.olap\n"
        "assert 'utah.store.olap' in sys.modules, 'lazy attribute did not import'\n"
        "print('LAZY-OK')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "LAZY-OK" in out.stdout


def test_repeated_access_yields_the_same_module_object():
    import utah.store as store
    assert store.olap is store.olap            # cached by the import system, not re-resolved


def test_attribute_error_names_the_available_tiers():
    import utah.store as store
    with pytest.raises(AttributeError) as exc:
        store.nope
    msg = str(exc.value)
    assert "utah.store" in msg and "nope" in msg
    assert "olap" in msg                       # the error teaches what IS available


def test_dunder_probes_do_not_false_resolve():
    """Tooling probes (copy/pickle/inspect) hit __getattr__ with dunder names —
    those must raise AttributeError cleanly, never attempt a submodule import."""
    import utah.store as store
    # (__getstate__ exists on every object since 3.11 — probe only true misses)
    for name in ("__deepcopy__", "__wrapped__", "_private_probe"):
        with pytest.raises(AttributeError):
            getattr(store, name)


def test_dir_is_sorted_and_deduplicated():
    import utah.store as store
    listing = dir(store)
    assert listing == sorted(set(listing))
    assert "olap" in listing
