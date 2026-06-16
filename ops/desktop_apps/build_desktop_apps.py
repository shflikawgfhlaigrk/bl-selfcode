"""Build the 5 Black Label products as standalone, double-clickable macOS .app bundles on the
Desktop. Each app launches its local test UI (server.py) using the working ~/ProjectUtah venv,
so it runs the REAL product code. Drop a logo with set-logo.sh to set the icon.

Run:  ~/ProjectUtah/.venv/bin/python ~/ProjectUtah/ops/desktop_apps/build_desktop_apps.py
"""
from __future__ import annotations

import plistlib
import shutil
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = Path.home() / "Desktop"

APPS = [
    ("Black Label Leads", "black-label-leads", 8911),
    ("Black Label Real Estate", "black-label-real-estate", 8912),
    ("Black Label Marketing", "black-label-marketing", 8913),
    ("Black Label Trading", "black-label-trading", 8914),
    ("Sovereign", "sovereign", 8915),
]

LAUNCHER = """#!/bin/bash
# Black Label desktop test app — runs the real product via the ProjectUtah venv.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
PORT={port}
PY="$HOME/ProjectUtah/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
( sleep 1.5; open "http://127.0.0.1:$PORT/" ) &
exec env PYTHONPATH="$HOME/ProjectUtah" "$PY" "$RES/server.py" "$PORT" "{app_id}"
"""


def build_app(label: str, app_id: str, port: int) -> Path:
    app = DESKTOP / f"{label}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    res = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    # launcher (executable)
    exe = macos / label
    exe.write_text(LAUNCHER.format(port=port, app_id=app_id))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # the real test server
    shutil.copy2(HERE / "server.py", res / "server.py")

    # Info.plist
    info = {
        "CFBundleName": label,
        "CFBundleDisplayName": label,
        "CFBundleExecutable": label,
        "CFBundleIdentifier": f"com.blacklabel.{app_id}",
        "CFBundleIconFile": "app",            # app.icns (added by set-logo.sh)
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    return app


def main() -> None:
    DESKTOP.mkdir(parents=True, exist_ok=True)
    built = [build_app(*a) for a in APPS]
    print(f"Built {len(built)} apps on {DESKTOP}:")
    for app in built:
        print(f"  • {app.name}")
    print("\nDouble-click any to open its test UI. Set a logo:")
    print(f"  {HERE}/set-logo.sh \"{built[0]}\" ~/path/to/logo.png")


if __name__ == "__main__":
    main()
