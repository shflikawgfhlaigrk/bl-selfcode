"""The ``utah.interface`` package surface: lazy submodule export (PEP 562) so
``import utah.interface`` stays light (no Starlette/SSE import at package import
time — the daemon imports the package without paying the web stack's cost), while
``utah.interface.web`` resolves on first touch and unknown attributes still raise
a proper AttributeError naming the package."""
from __future__ import annotations

import importlib
import sys

import pytest

import utah.interface as interface


def test_package_import_is_light_no_web_stack():
    """A FRESH package import must not drag in the web bridge (Starlette etc.)."""
    saved = {k: sys.modules.pop(k) for k in list(sys.modules)
             if k == "utah.interface" or k.startswith("utah.interface.")}
    try:
        importlib.import_module("utah.interface")
        assert "utah.interface.web" not in sys.modules, \
            "importing the package must NOT eagerly import the web bridge"
    finally:
        sys.modules.update(saved)


def test_web_resolves_lazily_on_attribute_access():
    web = interface.web
    assert web.__name__ == "utah.interface.web"
    assert hasattr(web, "build_app")
    assert interface.web is web                  # cached — one import, stable identity


def test_all_and_dir_advertise_the_surface():
    assert "web" in interface.__all__
    assert "web" in dir(interface)


def test_unknown_attribute_raises_named_attribute_error():
    with pytest.raises(AttributeError, match="utah.interface"):
        interface.does_not_exist
