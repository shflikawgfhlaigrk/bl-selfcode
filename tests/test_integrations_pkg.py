"""utah.integrations package init: the declared roster matches the tree, the
locator is total on adversarial names, the inventory never reports a partial
tree green, and importing the package stays light (no submodule side-imports —
the property that keeps `import pushover` from booting the browser)."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import utah.integrations as integrations

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_every_declared_module_exists_on_disk():
    """The roster IS the tree — a rename/deletion must fail here, not at 3am."""
    on_disk = {
        p.stem
        for p in (REPO / "utah" / "integrations").glob("*.py")
        if p.stem != "__init__"
    }
    assert set(integrations.MODULES) == on_disk


def test_available_true_for_a_real_module():
    assert integrations.available("pushover") is True


def test_available_false_for_undeclared_names():
    assert integrations.available("not_a_module") is False
    # importable elsewhere is NOT enough — only the declared roster counts
    assert integrations.available("os") is False


def test_available_total_on_adversarial_names():
    for name in ("", "..", "a/b", "\x00", None, 42):
        assert integrations.available(name) is False  # never raises


def test_inventory_green_on_the_real_tree():
    inv = integrations.inventory()
    assert inv["ok"] is True
    assert inv["missing"] == []
    assert inv["present"] == list(integrations.MODULES)


def test_inventory_honest_when_a_module_is_missing(monkeypatch):
    """A declared-but-absent module (iCloud eviction class) must read red."""
    monkeypatch.setattr(
        integrations, "MODULES", integrations.MODULES + ("vanished_lane",)
    )
    inv = integrations.inventory()
    assert inv["ok"] is False
    assert "vanished_lane" in inv["missing"]
    assert "pushover" in inv["present"]  # the rest still reports truthfully


def test_package_import_is_light_no_submodule_side_imports():
    """Importing the package must not import any submodule — proven in a fresh
    interpreter (this process has long since imported them via other tests)."""
    code = (
        "import sys, utah.integrations as i; "
        "loaded = [m for m in sys.modules if m.startswith('utah.integrations.')]; "
        "assert not loaded, loaded; "
        "assert i.inventory()['ok'] is True"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(REPO),
    )
    assert res.returncode == 0, res.stderr
