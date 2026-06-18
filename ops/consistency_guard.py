"""consistency_guard — PREVENT, don't just heal.

Michael (2026-06-17): "it's good to have healers but nothing should break in the first
place." Right. control.heal() is the runtime SAFETY NET; this is the PREVENTION layer — one
source of truth for "is the running system consistent with the repo + the bindings". The
autonomous loops (selfcode, app-improve) gate on this BEFORE calling anything done, and it
runs pre-deploy, so the classes of breakage we've seen are never accepted in the first
place instead of being healed 10 minutes (or 36 hours) later.

Checks (each maps to a real outage we've actually had):
  1. plist_drift  — a launchd plist is in the repo but NOT installed (the stripe-sync fire)
  2. daemon_stale — the running daemon is older than its source (the 36h-drift fire)
  3. orphan_job   — a loaded job points at a program file that was deleted (the EXIT-127 fires)
  4. app_seed_data— a Black Label app source ships hardcoded sample/seed records (binding breach)

Usage:
  python -m ops.consistency_guard            # report; exit 1 if any violation
  python -m ops.consistency_guard --fix      # delegate healable classes to control.heal()
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

BL_APPS = ["BlackLabelLeads", "BlackLabelRealEstate", "BlackLabelMarketing",
           "BlackLabelTrading", "BlackLabelSovereign"]
#: deliberate seed-data identifiers that must not ship at non-debug scope
_SEED = re.compile(r"\b(sampleData|previewData|mockData|seedData|demoData|sampleTrades|"
                   r"sampleLeads|sampleContacts|placeholderData)\b")


def _seed_violations(app_dir: Path) -> list[dict]:
    src = app_dir / "Sources"
    if not src.is_dir():
        return []
    out = []
    for f in src.glob("*.swift"):
        if "Preview" in f.name:
            continue
        text = f.read_text(errors="ignore")
        # strip #if DEBUG ... #endif blocks (dev-only seeding is allowed there)
        bare = re.sub(r"#if\s+DEBUG.*?#endif", "", text, flags=re.S)
        for m in _SEED.finditer(bare):
            out.append({"class": "app_seed_data",
                        "detail": f"{app_dir.name}/{f.name}: ships '{m.group(1)}' outside #if DEBUG"})
            break
    return out


def check() -> dict:
    from utah import control, drift

    violations: list[dict] = []

    if control.OPS_LAUNCHD.is_dir():
        installed = {p.stem for p in control.LAUNCHD.glob("com.utah.*.plist")}
        for srcpl in control.OPS_LAUNCHD.glob("com.utah.*.plist"):
            if srcpl.stem not in installed:
                violations.append({"class": "plist_drift",
                                   "detail": f"{srcpl.stem}: in repo, not installed"})

    try:
        for s in drift.stale_runtime():
            violations.append({"class": "daemon_stale", "detail": str(s)[:140]})
    except Exception as exc:  # noqa: BLE001
        violations.append({"class": "daemon_stale", "detail": f"probe failed: {exc}"})

    for j in control._launchctl_list():
        if control._job_target_missing(j["label"]):
            violations.append({"class": "orphan_job",
                               "detail": f"{j['label']}: program target deleted"})

    for app in BL_APPS:
        violations += _seed_violations(Path(os.path.expanduser(f"~/{app}")))

    # 5. one canonical bundle per app — a duplicate LaunchServices registration is the
    # "apps working over each other" misroute (a stale DerivedData build, a Desktop copy).
    import subprocess
    for bid in ("com.blacklabel.leads", "com.blacklabel.marketing", "com.blacklabel.realestate",
                "com.blacklabel.trading", "com.blacklabel.sovereign"):
        try:
            out = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{bid}'"],
                                 capture_output=True, text=True, timeout=10).stdout
            copies = [ln for ln in out.splitlines() if ln.strip()]
            if len(copies) > 1:
                violations.append({"class": "dup_bundle",
                                   "detail": f"{bid}: {len(copies)} copies registered — misroute risk"})
        except Exception:  # noqa: BLE001
            pass

    return {"ok": not violations, "violations": violations,
            "by_class": {c: sum(1 for v in violations if v["class"] == c)
                         for c in {v["class"] for v in violations}}}


def main(argv: list[str]) -> int:
    res = check()
    if "--fix" in argv:
        from utah import control
        res["healed"] = control.heal()["summary"]
        res = {**check(), "healed": res["healed"]}  # re-check after heal
    print(json.dumps(res, indent=2, default=str))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
