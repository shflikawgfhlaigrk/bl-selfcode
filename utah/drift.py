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
pages the same way an outage does. Every probe is injectable (roots/ports
passed as keywords) so the outage classes are provable hermetically, and
total: an unreadable file is a finding, never an unhandled crash.
"""
from __future__ import annotations

import logging
import os
import pathlib
import plistlib
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping

log = logging.getLogger("utah.drift")

REPO = pathlib.Path(__file__).resolve().parents[1]
AGENTS = pathlib.Path(os.path.expanduser("~/Library/LaunchAgents"))
RUN_DIR = pathlib.Path(os.path.expanduser("~/.utah/run"))
SECRETS_DIR = pathlib.Path(os.path.expanduser("~/.utah/secrets"))

#: The port map — who is ALLOWED to listen where. 876x = Ace, 877x = Sovereign.
#: (8765 stayed ambiguous for a day: Sovereign locally, Ace via tailnet proxy.)
EXPECTED_LISTENERS: dict[int, str] = {
    8766: "utah web deck",
    5433: "utah postgres",
}

#: Per-port connect budget — a drift scan must stay canary-cheap even when a
#: probed port is firewalled into a black hole instead of refusing.
PORT_PROBE_TIMEOUT_S = 1.5

#: iCloud numbers conflict copies arbitrarily ("core 2.py", "brain 13.py") —
#: the old `* 2.py` glob missed everything past the first collision.
_CONFLICT_COPY = re.compile(r" \d+\.py$")

#: Vendored/metadata trees where a numbered .py is not OUR drift.
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})

#: Repo root spellings that resolve to the same tree (Desktop symlink → ~/ProjectUtah).
_REPO_ROOT_TOKEN = "__UTAH_ROOT__"


def _repo_root_aliases(repo: pathlib.Path) -> tuple[str, ...]:
    """Every filesystem spelling of the Utah tree on this host."""
    candidates = [
        repo,
        repo.resolve(),
        pathlib.Path.home() / "ProjectUtah",
        pathlib.Path.home() / "Desktop" / "ProjectUtah",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        for spelling in (str(p), str(p.resolve())):
            if spelling not in seen:
                seen.add(spelling)
                out.append(spelling)
    return tuple(out)


def _normalize_repo_paths(value: object, aliases: tuple[str, ...]) -> object:
    """Rewrite equivalent repo-root paths to one token for semantic compare."""
    if isinstance(value, str):
        normalized = value
        for root in aliases:
            normalized = normalized.replace(root, _REPO_ROOT_TOKEN)
        return normalized
    if isinstance(value, dict):
        return {k: _normalize_repo_paths(v, aliases) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_repo_paths(v, aliases) for v in value]
    return value


def _plist_payload(raw: bytes) -> object | None:
    """Parse plist bytes; strip XML comments first (launchd ignores them)."""
    stripped = re.sub(rb"<!--.*?-->", b"", raw, flags=re.DOTALL)
    try:
        return plistlib.loads(stripped)
    except Exception:
        return None


def plists_semantically_equal(
    installed_raw: bytes,
    repo_raw: bytes,
    *,
    repo: pathlib.Path,
) -> bool:
    """True when two plists carry the same launchd job (ignoring comments/format/alias paths)."""
    aliases = _repo_root_aliases(repo)
    installed = _plist_payload(installed_raw)
    repo_pl = _plist_payload(repo_raw)
    if installed is not None and repo_pl is not None:
        return _normalize_repo_paths(installed, aliases) == _normalize_repo_paths(repo_pl, aliases)
    # Unparseable (tests / hand-edited junk): fall back to alias-normalized bytes.
    def norm_bytes(raw: bytes) -> bytes:
        text = raw.decode("utf-8", "replace")
        for root in aliases:
            text = text.replace(root, _REPO_ROOT_TOKEN)
        return text.encode("utf-8")

    return norm_bytes(installed_raw) == norm_bytes(repo_raw)


def plist_drift(*, repo: pathlib.Path = REPO, agents: pathlib.Path = AGENTS) -> list[str]:
    """Installed com.utah.* plists must match the repo's ops/launchd copies.

    Compares parsed plist payloads (not raw bytes) so ``~/ProjectUtah`` vs
    ``~/Desktop/ProjectUtah`` symlink spellings and XML comments do not false-alarm.

    An unreadable copy (perms, iCloud eviction) is reported as its own finding —
    a probe that crashes on it would hide every OTHER plist's drift too.
    """
    out = []
    for repo_plist in sorted((repo / "ops" / "launchd").glob("com.utah.*.plist")):
        installed = agents / repo_plist.name
        if not installed.exists():
            out.append(f"{repo_plist.name}: in repo but NOT installed")
            continue
        try:
            repo_raw = repo_plist.read_bytes()
            installed_raw = installed.read_bytes()
        except OSError as exc:
            out.append(f"{repo_plist.name}: unreadable ({exc.__class__.__name__}) — cannot verify")
            continue
        if not plists_semantically_equal(installed_raw, repo_raw, repo=repo):
            out.append(f"{repo_plist.name}: installed copy differs from repo")
    # Bidirectional: an installed com.utah.* job with NO repo copy is UNMANAGED — drift was
    # previously blind to it, so the healer itself (com.utah.heal) could be booted out, or a
    # crash-looping orphan could run, and nothing would notice. Every running job belongs in
    # the repo; an installed-but-unmanaged one is real drift to canonicalize.
    try:
        for inst in sorted(agents.glob("com.utah.*.plist")):
            if not (repo / "ops" / "launchd" / inst.name).exists():
                out.append(f"{inst.name}: installed but UNMANAGED (no repo copy in ops/launchd)")
    except OSError as exc:
        out.append(f"installed-plist scan failed ({exc.__class__.__name__}) — cannot verify unmanaged jobs")
    return out


def stale_runtime(
    pidfile_age_grace_s: float = 90.0,
    *,
    run_dir: pathlib.Path = RUN_DIR,
    repo: pathlib.Path = REPO,
) -> list[str]:
    """Code newer than the running daemon = a restart is pending somewhere.

    Utah imports straight off this tree (PYTHONPATH), so 'live code drift' is
    simply: the newest .py in utah/ is younger than the daemon process. We read
    the supervisor's start via its pid file age proxy — the run dir's pid files
    are recreated on every boot. *pidfile_age_grace_s* suppresses the marginal
    case of a file written seconds after boot (deploy scripts touch both).

    Grace defaults to 90s because the voice subsystem rewrites utah/voice/loop.py
    ~4-5s AFTER every daemon boot (its self-rebuild) — with grace 0 that bumped the
    newest-source mtime just past the boot mtime on every single boot, so this probe
    reported a permanent false 'restart pending' and the autonomous healer reload-
    looped the daemon every cooldown. A real deploy is minutes-to-hours newer than the
    last boot, so 90s cleanly separates the post-boot self-touch from genuine drift.
    """
    try:
        pids = list(run_dir.glob("*.pid"))
    except OSError:
        pids = []
    if not pids:
        return ["no pid files in ~/.utah/run — supervisor state unknown"]
    try:
        booted = max(p.stat().st_mtime for p in pids)
        newest_src = max((p.stat().st_mtime for p in (repo / "utah").rglob("*.py")), default=0.0)
    except OSError as exc:  # a pid/source file vanished mid-scan — re-probe next tick
        return [f"stale-runtime probe could not stat: {exc.__class__.__name__}: {exc}"]
    if newest_src > booted + pidfile_age_grace_s:
        age_h = (time.time() - booted) / 3600
        return [f"utah/ source newer than the running daemon (booted {age_h:.1f}h ago) — restart pending"]
    return []


def port_squatters(*, listeners: Mapping[int, str] = EXPECTED_LISTENERS) -> list[str]:
    """Reserved ports must answer (their owner is up) — a closed expected port
    is its own outage signal and a hijacked one is worse."""
    out = []
    for port, owner in listeners.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(PORT_PROBE_TIMEOUT_S)
        try:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                out.append(f":{port} ({owner}) not listening")
        finally:
            s.close()
    return out


def icloud_conflicts(*, repo: pathlib.Path = REPO) -> list[str]:
    """iCloud `<name> N.py` conflict copies shadow live modules and RED-gate verify.

    Any trailing ` <digits>.py` counts (not just ` 2.py` — repeat conflicts climb),
    while vendored trees (.venv, .git, node_modules) are someone else's problem.
    """
    hits: list[str] = []
    for p in repo.rglob("*.py"):
        if not _CONFLICT_COPY.search(p.name):
            continue
        rel = p.relative_to(repo)
        if _SKIP_DIRS.intersection(rel.parts[:-1]):
            continue
        hits.append(str(rel))
        if len(hits) >= 5:  # enough to page on; the cleanup command finds the rest
            break
    return [f"iCloud conflict copies present: {', '.join(hits)}"] if hits else []


def tailserve_job(*, gui: str | None = None) -> list[str]:
    """com.utah.tailserve must stay loaded — it re-asserts tailnet :8765 → Utah :8766
    every 2 min so Ace/Sovereign can't clobber the hook again."""
    gui = gui or f"gui/{os.getuid()}"
    r = subprocess.run(
        ["launchctl", "print", f"{gui}/com.utah.tailserve"],
        capture_output=True,
        timeout=5,
    )
    if r.returncode != 0:
        return ["com.utah.tailserve not loaded — tailnet :8765 may drift off Utah"]
    return []


def tailserve_hook(*, want_target: str = "127.0.0.1:8766") -> list[str]:
    """Tailscale serve on :8765 must proxy the Utah deck, not Ace/Sovereign."""
    ts = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if not os.path.isfile(ts) or not os.access(ts, os.X_OK):
        return []  # no Tailscale — tailnet hook N/A on this host
    try:
        status = subprocess.run(
            [ts, "serve", "status"],
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"tailserve status unreadable ({exc.__class__.__name__})"]
    if want_target not in status:
        return [f"tailnet :8765 not proxying {want_target} — run com.utah.tailserve"]
    return []


def sovereign_hijack(*, utah_port: int = 8766) -> list[str]:
    """Utah must own :8766 — Sovereign answering there is the classic hijack."""
    try:
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{utah_port}/", timeout=PORT_PROBE_TIMEOUT_S
        ).read(1024).decode("utf-8", "replace").lower()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return [f":{utah_port} (utah web deck) not listening ({exc.__class__.__name__})"]
    if "sovereign" in body and "ace os" not in body:
        return [f":{utah_port} is serving Sovereign, not Utah"]
    return []


def empty_secrets(*, secrets_dir: pathlib.Path = SECRETS_DIR) -> list[str]:
    """Integrations that claim 'ready' on the deck must have non-empty creds.

    Only PRESENT-but-empty files are drift — never configured is a known gap,
    a zeroed file is a lane that silently went dark."""
    out = []
    for name in ("gmail.json", "pushover.json"):
        p = secrets_dir / name
        try:
            effectively_empty = p.exists() and p.stat().st_size < 10
        except OSError:  # vanished between exists() and stat() — not drift
            continue
        if effectively_empty:
            out.append(f"{name} present but effectively empty — its lane is silently dark")
    return out


#: The wired probe set — each entry models one historical outage class.
DEFAULT_PROBES: tuple[Callable[[], list[str]], ...] = (
    plist_drift,
    stale_runtime,
    port_squatters,
    tailserve_job,
    tailserve_hook,
    sovereign_hijack,
    icloud_conflicts,
    empty_secrets,
)

#: canary detail budget — findings beyond this are truncated, not dropped silently.
MAX_DETAIL = 400


def scan(*, probes: tuple[Callable[[], list[str]], ...] | None = None) -> tuple[bool, str]:
    """Canary-check contract: (ok, plain-language detail). Never raises — a
    probe that crashes is itself a finding (a broken detector must not read
    as 'no drift')."""
    if probes is None:
        # Resolve the default set by NAME at call time: a tuple bound at import
        # pins the original functions forever, so a swapped-in probe (test
        # monkeypatch, hot-patched module) would silently never run — the exact
        # "broken detector reads as no-drift" failure this scan exists to catch.
        probes = tuple(globals()[p.__name__] for p in DEFAULT_PROBES)
    findings: list[str] = []
    for probe in probes:
        try:
            findings.extend(probe())
        except Exception as exc:  # noqa: BLE001 — a broken probe is a finding too
            log.warning("drift probe %s crashed", probe.__name__, exc_info=True)
            findings.append(f"{probe.__name__} crashed: {type(exc).__name__}: {exc}")
    if findings:
        return False, "; ".join(findings)[:MAX_DETAIL]
    return True, "no drift: plists match, code==runtime, ports owned, no conflict copies"


__all__ = [
    "DEFAULT_PROBES",
    "EXPECTED_LISTENERS",
    "empty_secrets",
    "icloud_conflicts",
    "plist_drift",
    "plists_semantically_equal",
    "port_squatters",
    "scan",
    "sovereign_hijack",
    "stale_runtime",
    "tailserve_hook",
    "tailserve_job",
]
