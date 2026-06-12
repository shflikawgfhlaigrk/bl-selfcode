"""migrations package — discovery + allowlisted load + never-raises run dispatch.

The package init is the front door for one-time migrations: ``available()`` must list
exactly the real migration modules (no iCloud "name 2.py" conflict copies, no private
helpers), ``load()`` must refuse anything outside that allowlist (no import injection),
and ``run()`` must be a never-raises boundary returning ``{"ok": bool, ...}``.
"""
from __future__ import annotations

import types

import pytest

import migrations


# ---------------------------------------------------------------------------
# available() — discovery
# ---------------------------------------------------------------------------

def test_available_lists_real_migrations_sorted():
    names = migrations.available()
    assert "ace_knowledge" in names
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_available_excludes_init_private_and_icloud_conflict_copies(tmp_path, monkeypatch):
    pkg = tmp_path / "migrations"
    pkg.mkdir()
    for fname in ("__init__.py", "_helper.py", "ace_knowledge 2.py",
                  "good_one.py", "another.py", "notes.txt"):
        (pkg / fname).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(migrations, "_PKG_DIR", pkg)
    assert migrations.available() == ["another", "good_one"]


def test_available_never_raises_on_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "_PKG_DIR", tmp_path / "gone")
    assert migrations.available() == []


# ---------------------------------------------------------------------------
# load() — allowlisted import
# ---------------------------------------------------------------------------

def test_load_returns_the_real_module():
    mod = migrations.load("ace_knowledge")
    assert callable(mod.migrate) and callable(mod.main)


@pytest.mark.parametrize("name", ["os", "../evil", "utah.core", "", "ace_knowledge 2"])
def test_load_refuses_names_outside_the_allowlist(name):
    with pytest.raises(ValueError):
        migrations.load(name)


# ---------------------------------------------------------------------------
# run() — never-raises dispatch boundary
# ---------------------------------------------------------------------------

def _fake_module(migrate=None):
    mod = types.SimpleNamespace()
    if migrate is not None:
        mod.migrate = migrate
    return mod


def test_run_wraps_stats_from_the_entrypoint():
    loader = lambda name: _fake_module(lambda **kw: {"read": 3, "ok": True})
    out = migrations.run("ace_knowledge", loader=loader)
    assert out == {"ok": True, "migration": "ace_knowledge",
                   "stats": {"read": 3, "ok": True}, "error": None}


def test_run_honest_false_when_migration_reports_not_ok():
    loader = lambda name: _fake_module(lambda **kw: {"ok": False, "aborted": True})
    out = migrations.run("ace_knowledge", loader=loader)
    assert out["ok"] is False and out["stats"]["aborted"] is True


def test_run_passes_kwargs_through_to_migrate():
    seen = {}

    def migrate(**kw):
        seen.update(kw)
        return {"ok": True}

    migrations.run("ace_knowledge", loader=lambda n: _fake_module(migrate),
                   batch_size=7)
    assert seen == {"batch_size": 7}


def test_run_never_raises_when_migrate_blows_up():
    loader = lambda name: _fake_module(
        lambda **kw: (_ for _ in ()).throw(RuntimeError("store exploded")))
    out = migrations.run("ace_knowledge", loader=loader)
    assert out["ok"] is False and "store exploded" in out["error"]
    assert out["stats"] is None


def test_run_never_raises_when_module_has_no_entrypoint():
    out = migrations.run("ace_knowledge", loader=lambda n: _fake_module(None))
    assert out["ok"] is False and "migrate" in out["error"]


def test_run_refuses_unknown_migration_honestly():
    out = migrations.run("totally_bogus")
    assert out["ok"] is False and "unknown migration" in out["error"]
