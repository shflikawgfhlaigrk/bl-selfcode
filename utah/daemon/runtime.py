"""Utah daemon runtime paths — everything under ``~/.utah`` ONLY.

Hard invariant (Phase-0 gate): the daemon never touches ``~/.ace`` or
``ace.db``. Every path used by the spine is derived here from ``UTAH_HOME``
(default ``~/.utah``); :func:`assert_isolated` fails loudly if any path ever
resolves inside ``~/.ace``. Directories are created mode 0700.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Root of the Utah runtime. Env-overridable for tests/CI; default ~/.utah.
UTAH_HOME: Path = Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah")))

RUN_DIR: Path = UTAH_HOME / "run"
LOG_DIR: Path = UTAH_HOME / "logs"
VAULT_DIR: Path = UTAH_HOME / "vault"

#: Control plane: JSON-RPC over a unix stream socket (owner-only, 0700 dir).
CONTROL_SOCK: Path = RUN_DIR / "utahd.sock"
#: Data plane (binary, length-prefixed) — bound when the WIN feed lands.
DATA_SOCK: Path = RUN_DIR / "utahd-data.sock"
#: flock singleton (kernel-held, auto-released on crash) — NEVER deleted.
LOCK_PATH: Path = RUN_DIR / "utahd.lock"
#: Pidfile — introspection only (liveness is a probe, never pid-presence).
PID_PATH: Path = RUN_DIR / "utahd.pid"
#: One structured, rotated log stream.
LOG_PATH: Path = LOG_DIR / "utahd.log"

_ACE_HOME = Path.home() / ".ace"


def assert_isolated(*paths: Path) -> None:
    """Raise if any path resolves inside ``~/.ace`` (the isolation gate)."""
    ace = _ACE_HOME.resolve()
    for p in (UTAH_HOME, RUN_DIR, LOG_DIR, VAULT_DIR, *paths):
        rp = p.expanduser().resolve()
        if rp == ace or ace in rp.parents:
            raise RuntimeError(f"isolation breach: {p} resolves inside ~/.ace")


def ensure_runtime() -> None:
    """Create the runtime directories and ENFORCE owner-only (0700).

    ``mkdir(mode=...)`` only applies at creation — a pre-existing 0755 tree
    (built before the invariant, or loosened by hand) would stay
    world-traversable forever, exposing the vault and the control socket dir.
    Idempotent; isolation-checked.
    """
    assert_isolated()
    for d in (UTAH_HOME, RUN_DIR, LOG_DIR, VAULT_DIR):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (d.stat().st_mode & 0o777) != 0o700:
            os.chmod(d, 0o700)
