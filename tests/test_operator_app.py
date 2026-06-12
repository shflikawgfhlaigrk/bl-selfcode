"""Tests for the Ace.app builder — project-root derivation + permissions wiring."""
from __future__ import annotations

import os
from pathlib import Path

from utah import operator_app


def test_no_hardcoded_user_path_in_source():
    """RUBRIC cap 62: live code must not bake in /Users/... paths."""
    src = Path(operator_app.__file__).read_text(encoding="utf-8")
    assert "/Users/" not in src


def test_project_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("UTAH_PROJECT_ROOT", str(tmp_path))
    assert operator_app._project_root() == tmp_path


def test_project_root_derived_from_module(monkeypatch):
    monkeypatch.delenv("UTAH_PROJECT_ROOT", raising=False)
    root = operator_app._project_root()
    assert root == Path(operator_app.__file__).resolve().parents[1]
    assert (root / "utah").is_dir()


def test_project_root_bad_env_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("UTAH_PROJECT_ROOT", str(tmp_path / "does-not-exist"))
    root = operator_app._project_root()
    assert root == Path(operator_app.__file__).resolve().parents[1]


def test_pythonpath_puts_project_root_first(tmp_path, monkeypatch):
    monkeypatch.setenv("UTAH_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    parts = operator_app._pythonpath().split(os.pathsep)
    assert parts[0] == str(tmp_path)


def test_launcher_execs_permissions_bootstrap():
    """Ace.app double-click = python -m utah.permissions bootstrap (the live exec path)."""
    script = operator_app._launcher_script(python_home="/ph", pythonpath="/pp")
    assert "-m utah.permissions bootstrap" in script
    assert "'/ph'" in script and "'/pp'" in script


def test_ensure_honest_when_stub_missing(monkeypatch):
    """No framework stub -> None (honest failure), never a half-built bundle."""
    monkeypatch.setattr(operator_app.runtime, "ensure_runtime", lambda *a, **k: None)
    monkeypatch.setattr(operator_app.macapp, "_source_stub_and_home", lambda: None)
    assert operator_app.ensure() is None


def test_ensure_skips_build_when_current(monkeypatch):
    """Happy path is cheap: _needs_rebuild short-circuits, no _build call."""
    built = []
    monkeypatch.setattr(operator_app.runtime, "ensure_runtime", lambda *a, **k: None)
    monkeypatch.setattr(
        operator_app.macapp, "_source_stub_and_home", lambda: ("/stub", "/home")
    )
    monkeypatch.setattr(operator_app, "_needs_rebuild", lambda *a, **k: False)
    monkeypatch.setattr(operator_app, "_build", lambda *a, **k: built.append(a))
    assert operator_app.ensure() == operator_app.APP_PATH
    assert built == []
