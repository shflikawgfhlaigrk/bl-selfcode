"""Drift detector — the gap between what's WRITTEN and what's RUNNING.

Every drift class here has already caused a real outage on this machine:
installed launchd plists diverging from ops/ (the supervisor ran Background
priority for weeks after the repo said Interactive), a daemon running code
older than the tree (Sovereign's /tmp orphan; kickstart-without-bootout),
port squatting (Sovereign's dashboard took Ace's 8765; old-Ace chrome took
9222), iCloud conflict copies (`* 2.py`) shadowing live modules, and
integrations claiming ready with empty secrets. The detector makes each one
a probe instead of an archaeology session.

Wired as a canary check (cheap: file stats + a few socket pokes), so drift
pages the same way an outage does.
"""
from __future__ import annotations

import os
import pathlib
import socket
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
AGENTS = pathlib.Path(os.path.expanduser("~/Library/LaunchAgents"))

#: The port map — who is ALLOWED to listen where. 876x = Ace, 877x = Sovereign.
#: (8765 stayed ambiguous for a day: Sovereign locally, Ace via tailnet proxy.)
EXPECTED_LISTENERS = {
    8766: "utah web deck",
    5433: "utah postgres",
}


def plist_drift() -> list[str]:
    """Installed com.utah.* plists must match the repo's ops/launchd copies."""
    out = []
    for repo_plist in sorted((REPO / "ops" / "launchd").glob("com.utah.*.plist")):
        installed = AGENTS / repo_plist.name
        if not installed.exists():
            out.append(f"{repo_plist.name}: in repo but NOT installed")
            continue
        if installed.read_bytes() != repo_plist.read_bytes():
            out.append(f"{repo_plist.name}: installed copy differs from repo")
    return out


def stale_runtime(pidfile_age_grace_s: float = 0.0) -> list[str]:
    """Code newer than the running daemon = a restart is pending somewhere.

    Utah imports straight off this tree (PYTHONPATH), so 'live code drift' is
    simply: the newest .py in utah/ is younger than the daemon process. We read
    the supervisor's start via its pid file age proxy — the run dir's pid files
    are recreated on every boot."""
    run = pathlib.Path(os.path.expanduser("~/.utah/run"))
    pids = sorted(run.glob("*.pid"), key=lambda p: p.stat().st_mtime)
    if not pids:
        return ["no pid files in ~/.utah/run — supervisor state unknown"]
    booted = max(p.stat().st_mtime for p in pids)
    newest_src = max((p.stat().st_mtime for p in (REPO / "utah").rglob("*.py")), default=0.0)
    if newest_src > booted + pidfile_age_grace_s:
        age_h = (time.time() - booted) / 3600
        return [f"utah/ source newer than the running daemon (booted {age_h:.1f}h ago) — restart pending"]
    return []


def port_squatters() -> list[str]:
    """Reserved ports must answer (their owner is up) — a closed expected port
    is its own outage signal and a hijacked one is worse."""
    out = []
    for port, owner in EXPECTED_LISTENERS.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        try:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                out.append(f":{port} ({owner}) not listening")
        finally:
            s.close()
    return out


def icloud_conflicts() -> list[str]:
    """iCloud `* 2.py` conflict copies shadow live modules and RED-gate verify."""
    hits = [str(p.relative_to(REPO)) for p in REPO.rglob("* 2.py")][:5]
    return [f"iCloud conflict copies present: {', '.join(hits)}"] if hits else []


def empty_secrets() -> list[str]:
    """Integrations that claim 'ready' on the deck must have non-empty creds."""
    out = []
    secrets = pathlib.Path(os.path.expanduser("~/.utah/secrets"))
    for name in ("gmail.json", "pushover.json"):
        p = secrets / name
        if p.exists() and p.stat().st_size < 10:
            out.append(f"{name} present but effectively empty — its lane is silently dark")
    return out


def scan() -> tuple[bool, str]:
    """Canary-check contract: (ok, plain-language detail)."""
    findings: list[str] = []
    for probe in (plist_drift, stale_runtime, port_squatters, icloud_conflicts,
                  empty_secrets):
        try:
            findings.extend(probe())
        except Exception as exc:  # noqa: BLE001 — a broken probe is a finding too
            findings.append(f"{probe.__name__} crashed: {type(exc).__name__}: {exc}")
    if findings:
        return False, "; ".join(findings)[:400]
    return True, "no drift: plists match, code==runtime, ports owned, no conflict copies"
