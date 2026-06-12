"""Build and launch ``Ace.app`` — the visible setup app that requests all macOS permissions.

Unlike ``UtahVoice.app`` (background, mic-only, ``LSUIElement``), Ace shows in the Dock,
runs :mod:`utah.permissions` bootstrap on double-click, and owns Automation TCC for
Notes/Contacts/notifications when Utah drives the Mac on Michael's behalf.

Kept built by the 5-minute operator sweep (:func:`utah.operator.ensure_app`).

Usage::

    # project root is derived from this file; override with UTAH_PROJECT_ROOT
    ~/.utah/venv/bin/python -m utah.operator_app ensure

    # build + open (double-click equivalent)
    ... -m utah.operator_app launch
"""
from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from utah.daemon import runtime
from utah.voice import macapp

log = logging.getLogger("utah.operator_app")

BUNDLE_ID = "com.utah.ace"
APP_PATH: Path = runtime.UTAH_HOME / "Ace.app"
EXEC_NAME = "Ace"
PYTHON_RESOURCE = "Python"
MARKER_PATH = runtime.RUN_DIR / "aceapp.source"


def _info_plist() -> dict:
    return {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": EXEC_NAME,
        "CFBundleDisplayName": "Ace",
        "CFBundleExecutable": EXEC_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        # Visible in Dock — this is the app Michael opens to grant everything.
        "LSUIElement": False,
        "NSMicrophoneUsageDescription": (
            'Ace listens for the wake word "hey ace" and your spoken commands.'
        ),
        "NSAppleEventsUsageDescription": (
            "Ace automates Notes, Contacts, notifications, and shortcuts on your Mac."
        ),
    }


def _launcher_script(*, python_home: str, pythonpath: str) -> str:
    return f"""#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/../Resources/{PYTHON_RESOURCE}"
export PYTHONHOME={python_home!r}
export PYTHONPATH={pythonpath!r}
export PYTHONUNBUFFERED=1
exec "$PY" -m utah.permissions bootstrap
"""


def _needs_rebuild(stub_src: str, pythonpath: str) -> bool:
    exe = APP_PATH / "Contents" / "MacOS" / EXEC_NAME
    plist = APP_PATH / "Contents" / "Info.plist"
    py = APP_PATH / "Contents" / "Resources" / PYTHON_RESOURCE
    if not (exe.is_file() and plist.is_file() and py.is_file() and MARKER_PATH.is_file()):
        return True
    try:
        want = macapp._sha256(stub_src)  # noqa: SLF001
        marker = MARKER_PATH.read_text().strip().split("\n")
        if marker[0] != want or (len(marker) > 1 and marker[1] != pythonpath):
            return True
        data = plistlib.loads(plist.read_bytes())
        if data.get("CFBundleIdentifier") != BUNDLE_ID or data.get("LSUIElement") is not False:
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        verify = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(APP_PATH)],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return verify.returncode != 0


def _build(stub_src: str, python_home: str, pythonpath: str) -> None:
    import shutil

    contents = APP_PATH / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    if APP_PATH.exists():
        shutil.rmtree(APP_PATH)
    macos.mkdir(parents=True)
    resources.mkdir()

    shutil.copy2(stub_src, resources / PYTHON_RESOURCE)
    os.chmod(resources / PYTHON_RESOURCE, 0o755)

    launcher = macos / EXEC_NAME
    launcher.write_text(_launcher_script(python_home=python_home, pythonpath=pythonpath), encoding="utf-8")
    os.chmod(launcher, 0o755)

    (contents / "Info.plist").write_bytes(plistlib.dumps(_info_plist()))
    (contents / "PkgInfo").write_text("APPL????")
    MARKER_PATH.write_text(f"{macapp._sha256(stub_src)}\n{pythonpath}\n")  # noqa: SLF001

    res = subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(APP_PATH)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(f"codesign failed: {res.stderr.strip()}")
    log.info("built signed Ace bundle %s (id=%s)", APP_PATH, BUNDLE_ID)


def _project_root() -> Path | None:
    """The ProjectUtah checkout (the tree that imports ``utah``).

    ``UTAH_PROJECT_ROOT`` wins when it points at a real directory; otherwise the
    root is derived from this file's location. Returns ``None`` honestly when
    neither resolves (e.g. running from a copied site-packages tree).
    """
    env = os.environ.get("UTAH_PROJECT_ROOT", "").strip()
    if env:
        root = Path(env).expanduser()
        if root.is_dir():
            return root
        log.warning("UTAH_PROJECT_ROOT=%s is not a directory — deriving instead", env)
    root = Path(__file__).resolve().parents[1]
    return root if (root / "utah").is_dir() else None


def _pythonpath() -> str:
    parts: list[str] = []
    for key in ("PYTHONPATH",):
        val = os.environ.get(key, "").strip()
        if val:
            parts.extend(val.split(os.pathsep))
    # Default to the ProjectUtah tree the supervisor uses (env override > derived).
    default = _project_root()
    if default is not None:
        parts.insert(0, str(default))
    site = macapp._venv_site_packages()  # noqa: SLF001
    if site:
        parts.append(site)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


def ensure(*, force: bool = False) -> Path | None:
    """Build or refresh ``~/.utah/Ace.app``. Returns the app path or ``None`` on failure."""
    if sys.platform != "darwin":
        log.warning("Ace.app is macOS-only")
        return None
    runtime.ensure_runtime()
    src = macapp._source_stub_and_home()  # noqa: SLF001
    if not src:
        log.warning("Ace.app: framework Python stub not found")
        return None
    stub_src, python_home = src
    pythonpath = _pythonpath()
    try:
        if force or _needs_rebuild(stub_src, pythonpath):
            _build(stub_src, python_home, pythonpath)
    except Exception:  # noqa: BLE001
        log.warning("Ace.app build failed", exc_info=True)
        return None
    return APP_PATH


def launch(*, force_build: bool = False) -> int:
    """Build (if needed) and open Ace.app."""
    path = ensure(force=force_build)
    if path is None:
        return 1
    subprocess.run(["open", str(path)], check=False, timeout=10)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = list(argv or sys.argv[1:])
    cmd = (args[0] if args else "launch").lower()
    if cmd == "ensure":
        path = ensure(force="--force" in args)
        print(path or "")
        return 0 if path else 1
    if cmd == "launch":
        return launch(force_build="--force" in args)
    if cmd == "path":
        path = ensure()
        print(path or "")
        return 0 if path else 1
    print("usage: python -m utah.operator_app [ensure|launch|path] [--force]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
