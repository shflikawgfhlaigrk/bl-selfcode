"""utah.product package surface — lazy submodule access (PEP 562), same pattern as
utah.store: importing the package stays free (no capability module is imported until
first touch), every real submodule resolves as an attribute, and an unknown name is an
AttributeError — while a submodule's own missing DEPENDENCY still propagates loudly
instead of masquerading as "no such attribute"."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


def test_lazy_attribute_resolves_to_the_real_submodule():
    import utah.product as product
    clock = product.clock
    assert clock is importlib.import_module("utah.product.clock")
    assert callable(clock.now_text)          # the real capability, not a stub


def test_classic_from_import_still_works():
    from utah.product import backtest
    assert backtest.__name__ == "utah.product.backtest"


def test_unknown_attribute_raises_attribute_error():
    import utah.product as product
    with pytest.raises(AttributeError, match="utah.product"):
        product.no_such_capability


def test_dir_and_all_advertise_the_submodules():
    import utah.product as product
    for mod in ("backtest", "clock", "enrich", "ledger", "outreach"):
        assert mod in product.__all__
        assert mod in dir(product)


def test_submodule_dependency_failure_is_not_swallowed(monkeypatch):
    # A capability module whose OWN import crashes (missing dep) must surface that
    # crash — converting it to AttributeError would hide a broken deploy.
    import utah.product as product
    monkeypatch.delitem(sys.modules, "utah.product.clock", raising=False)

    def boom(name, package=None):
        raise ModuleNotFoundError("No module named 'left_pad'", name="left_pad")

    monkeypatch.setattr(importlib, "import_module", boom)
    with pytest.raises(ModuleNotFoundError, match="left_pad"):
        product.__getattr__("clock")


def test_importing_the_package_pulls_no_capability_modules():
    # Fresh interpreter: `import utah.product` must not drag in leads/outreach/etc.
    code = ("import sys; import utah.product; "
            "loaded = sorted(m for m in sys.modules if m.startswith('utah.product.')); "
            "assert loaded == [], loaded")
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=60, env=env)
    assert r.returncode == 0, r.stderr
