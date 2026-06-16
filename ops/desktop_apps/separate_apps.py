"""Separate each Black Label app into its OWN self-contained folder.

For every app: collect ALL the files it needs (its full transitive Utah-import closure), copy
them into ~/Desktop/Black Label Apps/<App>/src/ (own copy, separate from ~/ProjectUtah), drop a
starter sign-in+dashboard UI, a requirements.txt computed from the code, and a CLAUDE.md build
brief. Then a fresh Claude Code opened in each folder has everything it needs to finish that app.
"""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from standalone_builder import closure, REPO  # noqa: E402

DEST = Path.home() / "Desktop" / "Black Label Apps"
STDLIB = set(getattr(sys, "stdlib_module_names", set()))

APPS = [
    {
        "label": "Black Label Leads", "id": "black-label-leads",
        "entries": ["utah.product.leads", "utah.product.contacts", "utah.product.builders",
                    "utah.product.enrich", "utah.product.outreach", "utah.product.pipeline",
                    "utah.product.leads_status", "utah.product.sitegen", "utah.mail"],
        "what": "Our Apollo.io — find anyone in any market anywhere in the US, verify their email, and send from the client's OWN inbox, autonomously.",
        "build": ["Sign-in + client onboarding (client enters their sending email)",
                  "Full dashboard: market search, contact discovery, verified emails, sequencer, reply/bounce inbox",
                  "Persistent contact DB UI", "Billing/subscription", "Package as a shippable app"],
    },
    {
        "label": "Black Label Real Estate", "id": "black-label-real-estate",
        "entries": ["utah.product.probate", "utah.product.probate_outreach",
                    "utah.product.probate_export", "utah.product.property",
                    "utah.product.route", "utah.product.builders"],
        "what": "Probate deal pipeline + builder finder + 3-mile comps/ownership/debt enrichment.",
        "build": ["Sign-in", "Dashboard: probate leads, builder finder, 3-mile radius map, route planner",
                  "Skip-trace / heir contact", "Export", "Package as a shippable app"],
    },
    {
        "label": "Black Label Marketing", "id": "black-label-marketing",
        "entries": ["utah.product.marketer", "utah.product.sitegen", "utah.product.reel_queue",
                    "utah.product.news"],
        "what": "Sites, reels, and Apple-grade imagery/video for a brand.",
        "build": ["Sign-in", "Dashboard: site generator, reel queue, spotlight outreach",
                  "Apple imagery/video pipeline (Image Playground / Apple Intelligence)",
                  "Post + analytics", "Package as a shippable app"],
    },
    {
        "label": "Black Label Trading", "id": "black-label-trading",
        "entries": ["utah.product.trading", "utah.product.signals", "utah.product.backtest",
                    "utah.product.engine_audit", "utah.product.engine_status",
                    "utah.product.fire_grader", "utah.product.research_signal",
                    "utah.product.researcher", "utah.product.trade_alert",
                    "utah.product.trade_lore", "utah.product.prop_accounts"],
        "what": "Take any WealthCharts login + any prop firm, run the engines, show the signals.",
        "build": ["Sign-in (any WealthCharts login + any prop firm intake)",
                  "Dashboard: live signals, engine status, per-trade reasoning, alerts",
                  "Per-client signal view", "Package as a shippable app"],
    },
    {
        "label": "Sovereign", "id": "sovereign",
        "entries": ["utah.brain", "utah.local_brain", "utah.fm_local", "utah.product.weather",
                    "utah.product.jobs_status", "utah.product.brief"],
        "what": "Your own AI assistant — voice, weather, brain on your Claude login, dashboard, self-coding.",
        "build": ["Sign-in to Claude (token → Keychain) — the populate-login screen",
                  "Voice + chat UI", "Weather, dashboard, the works",
                  "Bundle the voice/ package", "Package as a shippable app"],
    },
]


def _third_party(files: set[Path]) -> set[str]:
    """Top-level non-stdlib, non-utah imports across the closure → the pip dependencies."""
    deps: set[str] = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    deps.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                deps.add(node.module.split(".")[0])
    return {d for d in deps if d and d != "utah" and d not in STDLIB}


# pip package names differ from import names for a few
_PIP = {"psycopg": "psycopg[binary]", "dateutil": "python-dateutil", "yaml": "pyyaml",
        "PIL": "pillow", "bs4": "beautifulsoup4", "dotenv": "python-dotenv"}


def claude_md(app: dict, n_files: int, deps: list[str]) -> str:
    builds = "\n".join(f"- [ ] {b}" for b in app["build"])
    return f"""# {app['label']} — build this out

**What it is:** {app['what']}

This folder is a **self-contained copy** of everything this app needs ({n_files} files in `src/`),
separated from the main Utah repo. You can build it out here without touching anything else.

## Layout
- `src/` — all the app's code (its own copy; imports are `utah.*` resolving to `src/utah/`).
- `app/server.py` — a starter sign-in + dashboard UI that already runs the real code.
- `requirements.txt` — the pip dependencies this code imports.
- `logo/` — drop the app's logo here (PNG).

## Run the starter UI
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
PYTHONPATH=src python app/server.py 8911 {app['id']}
# open http://127.0.0.1:8911  (sign-in → dashboard)
```

## To finish (build out)
{builds}

Honesty rules carry over: never fabricate data; gate features that need a backend/DB/network
with an honest message; show real results only.
"""


def separate(app: dict) -> dict:
    out = DEST / app["label"]
    if out.exists():
        shutil.rmtree(out)
    (out / "logo").mkdir(parents=True)
    files = closure(app["entries"])
    src = out / "src"
    for f in files:
        rel = f.relative_to(REPO)
        d = src / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, d)
        # __init__ chain
        anc = f.parent
        while anc != REPO and anc.name:
            init = anc / "__init__.py"
            if init.exists():
                di = src / init.relative_to(REPO)
                di.parent.mkdir(parents=True, exist_ok=True)
                if not di.exists():
                    shutil.copy2(init, di)
            anc = anc.parent
    # starter UI
    (out / "app").mkdir()
    shutil.copy2(Path(__file__).resolve().parent / "server.py", out / "app" / "server.py")
    # requirements + brief
    deps = sorted(_PIP.get(d, d) for d in _third_party(files))
    (out / "requirements.txt").write_text("\n".join(deps) + "\n")
    (out / "CLAUDE.md").write_text(claude_md(app, len(files), deps))
    return {"app": app["label"], "files": len(files), "deps": deps}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"Separating apps into {DEST}\n")
    for app in APPS:
        r = separate(app)
        print(f"  {r['app']:26} {r['files']:3} files · deps: {', '.join(r['deps']) or 'none'}")


if __name__ == "__main__":
    main()
