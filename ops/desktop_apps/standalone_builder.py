"""Standalone app factory — vendor an app's FULL code into its own bundle, separate from Utah.

The hard requirement: each app must be its own files and run WITHOUT ~/ProjectUtah. So for a set
of entry modules we compute the transitive `utah.*` import closure (via AST), copy every reached
module into the app's private `core/utah/` tree (with the package __init__ chain), and the app
runs off its OWN copy. No symlinks, no shared repo. External pip deps still come from a Python
env, but the Utah *code* is fully owned per app.

This is the mechanism that scales to the full roster — build the closure once, stamp N apps.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

REPO = Path.home() / "ProjectUtah"


def _utah_imports(pyfile: Path) -> set[str]:
    """Dotted `utah.*` modules imported by *pyfile* (both `from utah.x import y` and `import utah.x`)."""
    mods: set[str] = set()
    try:
        tree = ast.parse(pyfile.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return mods
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "utah":
            mods.add(node.module)
            for a in node.names:                       # `from utah.x import y` — y may be a submodule
                mods.add(f"{node.module}.{a.name}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "utah":
                    mods.add(a.name)
    return mods


def _mod_to_file(dotted: str) -> Path | None:
    rel = dotted.replace(".", "/")
    f = REPO / f"{rel}.py"
    if f.exists():
        return f
    pkg = REPO / rel / "__init__.py"
    if pkg.exists():
        return pkg
    return None


def closure(entries: list[str]) -> set[Path]:
    """Every Utah .py file reachable from the *entries* (dotted module names), transitively."""
    files: set[Path] = set()
    stack = [p for e in entries if (p := _mod_to_file(e))]
    while stack:
        f = stack.pop()
        if f in files:
            continue
        files.add(f)
        for m in _utah_imports(f):
            nf = _mod_to_file(m)
            if nf and nf not in files:
                stack.append(nf)
    return files


def _vendor(files: set[Path], core: Path) -> int:
    """Copy each reached file into core/ preserving its utah/ path, plus the __init__ chain so
    every package along the way imports."""
    core.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in files:
        rel = f.relative_to(REPO)
        dest = core / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        count += 1
        # ensure every ancestor package has an __init__.py copied
        anc = f.parent
        while anc != REPO and anc.name:
            init = anc / "__init__.py"
            if init.exists():
                d = core / init.relative_to(REPO)
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(init, d)
            anc = anc.parent
    return count


def build(label: str, app_id: str, entries: list[str], port: int, *,
          desktop: Path | None = None) -> dict:
    """Stamp a standalone <label>.app on the Desktop: vendored core/ (own Utah copy) + the test
    server. Returns {app, vendored, standalone}."""
    desktop = desktop or (Path.home() / "Desktop")
    app = desktop / f"{label}.app"
    if app.exists():
        shutil.rmtree(app)
    res = app / "Contents" / "Resources"
    macos = app / "Contents" / "MacOS"
    res.mkdir(parents=True)
    macos.mkdir(parents=True)

    files = closure(entries)
    n = _vendor(files, res / "core")
    shutil.copy2(Path(__file__).resolve().parent / "server.py", res / "server.py")

    exe = macos / label
    exe.write_text(f"""#!/bin/bash
# Standalone {label} — runs off its OWN vendored core/ (separate from ~/ProjectUtah).
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
PORT={port}
# interpreter + pip deps: prefer a bundled venv, else the system python3
PY="$RES/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
( sleep 1.5; open "http://127.0.0.1:$PORT/" ) &
exec env PYTHONPATH="$RES/core" "$PY" "$RES/server.py" "$PORT" "{app_id}"
""")
    exe.chmod(0o755)

    import plistlib
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({
            "CFBundleName": label, "CFBundleDisplayName": label, "CFBundleExecutable": label,
            "CFBundleIdentifier": f"com.blacklabel.{app_id}", "CFBundleIconFile": "app",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1", "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        }, fh)
    return {"app": str(app), "vendored": n}


if __name__ == "__main__":
    # demo/proof entry — Leads, the most dependency-heavy app
    r = build("Black Label Leads", "black-label-leads",
              ["utah.product.leads", "utah.product.contacts", "utah.product.builders",
               "utah.mail", "utah.product.sitegen"], 8911)
    print(f"vendored {r['vendored']} Utah modules into {r['app']}")
