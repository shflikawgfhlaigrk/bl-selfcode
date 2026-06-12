"""Runtime path invariants: every spine path derives from UTAH_HOME, the
isolation gate actually refuses ~/.ace (including via symlink), and
ensure_runtime ENFORCES owner-only dirs — a pre-existing 0755 ~/.utah must be
tightened, not silently accepted (mkdir's mode is ignored for existing dirs)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from utah.daemon import runtime

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point every runtime dir constant at a temp tree (never the live ~/.utah)."""
    home = tmp_path / "utah-home"
    monkeypatch.setattr(runtime, "UTAH_HOME", home)
    monkeypatch.setattr(runtime, "RUN_DIR", home / "run")
    monkeypatch.setattr(runtime, "LOG_DIR", home / "logs")
    monkeypatch.setattr(runtime, "VAULT_DIR", home / "vault")
    return home


# -- derivation ----------------------------------------------------------------

def test_every_spine_path_lives_under_utah_home():
    for p in (runtime.RUN_DIR, runtime.LOG_DIR, runtime.VAULT_DIR,
              runtime.CONTROL_SOCK, runtime.DATA_SOCK, runtime.LOCK_PATH,
              runtime.PID_PATH, runtime.LOG_PATH):
        assert runtime.UTAH_HOME in p.parents, p


def test_utah_home_env_override_is_honored_at_import():
    """UTAH_HOME is read at import — a fresh interpreter with the env set must
    derive every path from it (the tests/CI escape hatch)."""
    out = subprocess.run(
        [sys.executable, "-c",
         "from utah.daemon import runtime; print(runtime.UTAH_HOME); print(runtime.CONTROL_SOCK)"],
        env={**os.environ, "UTAH_HOME": "/tmp/utah-override-test",
             "PYTHONPATH": f"{REPO}:{os.environ.get('PYTHONPATH', '')}"},
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "/tmp/utah-override-test"
    assert lines[1] == "/tmp/utah-override-test/run/utahd.sock"


# -- isolation gate ------------------------------------------------------------

def test_assert_isolated_passes_for_the_default_tree():
    runtime.assert_isolated()  # ~/.utah is not ~/.ace


def test_assert_isolated_refuses_a_path_inside_dot_ace():
    poison = Path.home() / ".ace" / "ace.db"
    with pytest.raises(RuntimeError, match="isolation breach"):
        runtime.assert_isolated(poison)


def test_assert_isolated_refuses_dot_ace_itself():
    with pytest.raises(RuntimeError, match="isolation breach"):
        runtime.assert_isolated(Path.home() / ".ace")


def test_assert_isolated_sees_through_a_symlink_into_dot_ace(tmp_path):
    """A path that LOOKS clean but resolves into ~/.ace is still a breach —
    the gate works on resolved paths, not string prefixes."""
    sneaky = tmp_path / "innocent-looking"
    os.symlink(Path.home() / ".ace" / "vault", sneaky)
    with pytest.raises(RuntimeError, match="isolation breach"):
        runtime.assert_isolated(sneaky)


def test_assert_isolated_names_the_offending_path():
    poison = Path.home() / ".ace" / "secret"
    with pytest.raises(RuntimeError, match="secret"):
        runtime.assert_isolated(poison)


# -- ensure_runtime ------------------------------------------------------------

def test_ensure_runtime_creates_owner_only_dirs(tmp_home):
    runtime.ensure_runtime()
    for d in (tmp_home, tmp_home / "run", tmp_home / "logs", tmp_home / "vault"):
        assert d.is_dir()
        assert (d.stat().st_mode & 0o777) == 0o700, d


def test_ensure_runtime_is_idempotent(tmp_home):
    runtime.ensure_runtime()
    runtime.ensure_runtime()  # second run must not raise or loosen anything
    assert (tmp_home.stat().st_mode & 0o777) == 0o700


def test_ensure_runtime_tightens_a_preexisting_loose_dir(tmp_home):
    """mkdir(exist_ok=True) ignores mode for dirs that already exist — so a
    0755 vault (built before the invariant) stayed world-traversable forever.
    ensure_runtime must ENFORCE 0700, not just request it at creation."""
    vault = tmp_home / "vault"
    vault.mkdir(parents=True)
    vault.chmod(0o755)
    runtime.ensure_runtime()
    assert (vault.stat().st_mode & 0o777) == 0o700
