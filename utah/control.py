"""Ace CONTROL — the proof-of-execution layer.

WHY THIS EXISTS (Michael, 2026-06-17): Ace kept *narrating* actions it never ran —
"still running", "firing up all 27", "the capability is mine" — confident prose with
no tool result behind it. The fix is not motivational, it is structural:

    PROOF-OF-EXECUTION LAW
    ----------------------
    A capability has "done" something ONLY if it returns a real value AND writes an
    activity-ledger row carrying that value as evidence. No row, no evidence => it did
    not happen. "I acted" must mean a function returned a result, never that Ace
    intended to. A failure is reported with its real reason; a missing prerequisite
    (no API key, TCC-blocked) is reported as "I can't, here's why" — NEVER faked.

This is the same spirit as ``utah.actions`` (the HONESTY LAW for capability re-runs);
``control`` covers the OS / account ACTUATORS Michael names by voice/chat — worker
health, Stripe sales, email, alarms, screen recording — plus ``heal`` (close the
detect->act loop the canary leaves open) and ``improve_apps`` (dispatch the per-app
engineer workers). Every public function returns a dict shaped:

    {"ok": bool, "summary": str (speakable, real numbers only), "evidence": <raw>, ...}

and logs it. The conversational brain speaks ``summary``; ``evidence`` is the receipt.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

try:  # real Utah home when imported in-tree; degrade gracefully in isolation
    from utah import config

    HOME = Path(config.UTAH_HOME)
except Exception:  # noqa: BLE001
    HOME = Path(os.path.expanduser("~/.utah"))

ACTIVITY_DIR = HOME / "activity"
LEDGER = ACTIVITY_DIR / "actions.jsonl"
RUN_DIR = HOME / "run"
LAUNCHD = Path(os.path.expanduser("~/Library/LaunchAgents"))
OPS_LAUNCHD = Path(os.path.expanduser("~/ProjectUtah/ops/launchd"))


# --------------------------------------------------------------------------- ledger
def _now() -> float:
    return time.time()


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _log(action: str, *, ok: bool, summary: str, evidence=None, **extra) -> dict:
    """Append one activity row. This IS the proof — the row is only written with the
    real result in hand. Never raises (a dead ledger must not mask a real action)."""
    ts = _now()
    row = {
        "id": f"{int(ts*1000):x}",
        "ts": ts,
        "at": _stamp(ts),
        "action": action,
        "ok": bool(ok),
        "status": "done" if ok else "failed",
        "summary": summary,
        "evidence": evidence,
        **extra,
    }
    try:
        ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001 — proof write is best-effort; the value is still returned
        pass
    return row


def feed(n: int = 20) -> list[dict]:
    """The last *n* activity rows — what Michael reads to SEE Ace actually did things."""
    try:
        lines = LEDGER.read_text().splitlines()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            pass
    return out


# --------------------------------------------------------------------------- helpers
def _sh(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 127, str(exc)


def _uid() -> str:
    return str(os.getuid())


def _launchctl_list() -> list[dict]:
    """Live per-job state from launchctl: pid, last-exit, label. The empty pid '-'
    means not currently running (a cron between ticks, or a crashed KeepAlive job)."""
    rc, out = _sh(["launchctl", "list"])
    jobs = []
    for ln in out.splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        pid, last, label = parts[0], parts[1], parts[2]
        if not (label.startswith("com.utah") or label.startswith("com.blacklabel")):
            continue
        jobs.append({
            "label": label,
            "pid": None if pid == "-" else int(pid),
            "last_exit": None if last == "-" else int(last),
        })
    return jobs


def _port(port: int, timeout: float = 3.0) -> int | None:
    rc, out = _sh(["curl", "-s", "-m", str(int(timeout)), "-o", "/dev/null",
                   "-w", "%{http_code}", f"http://127.0.0.1:{port}/"], timeout=int(timeout) + 2)
    try:
        code = int(out)
        return code or None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- WORKERS
def workers_status() -> dict:
    """REAL per-worker health — the readout Ace must produce instead of 'all 27 running'.
    UP = has a pid; idle(ok) = cron between ticks, last exit 0; DEAD = nonzero last exit
    and not running. Returns the raw list as evidence; summary counts the dead ones."""
    jobs = _launchctl_list()
    up, idle, dead = [], [], []
    for j in jobs:
        if j["pid"]:
            up.append(j["label"])
        elif (j["last_exit"] or 0) == 0:
            idle.append(j["label"])
        else:
            dead.append({"label": j["label"], "exit": j["last_exit"]})
    ports = {name: _port(p) for name, p in
             {"deck": 8766, "sovereign": 8765, "wcfeed": 9223}.items()}
    ok = not dead
    deadtxt = (", ".join(f"{d['label']}(exit {d['exit']})" for d in dead)
               if dead else "none")
    summary = (f"{len(up)} up, {len(idle)} idle-ok, {len(dead)} DEAD. "
               f"Dead: {deadtxt}. deck:{ports['deck']} wcfeed:{ports['wcfeed']} "
               f"sovereign:{ports['sovereign'] or 'down'}.")
    return _log("workers_status", ok=ok, summary=summary,
                evidence={"up": up, "idle": idle, "dead": dead, "ports": ports,
                          "total": len(jobs)})


# --------------------------------------------------------------------------- STRIPE
def check_stripe() -> dict:
    """Read REAL Stripe sales — or say honestly that it can't. Michael: "do we have any
    sales" must trigger a real look, never a fabricated number. No key wired => report
    that and what to drop in; never invent $0 as if it were read."""
    key_file = HOME / "secrets" / "stripe.json"
    if not key_file.exists():
        return _log("check_stripe", ok=False,
                    summary="Can't read Stripe — no API key wired "
                            "(~/.utah/secrets/stripe.json absent). Drop a key with "
                            "\"secret_key\" there and I'll read live sales. Not faking $0.",
                    evidence={"key": "absent", "path": str(key_file)})
    try:
        from utah.product import stripe_sync
        k = stripe_sync._secret_key()
        if not k:
            return _log("check_stripe", ok=False,
                        summary="Stripe key file present but unreadable/empty — can't read sales.",
                        evidence={"key": "malformed"})
        charges = stripe_sync.fetch_charges(k)
        real = [c for c in charges if stripe_sync._is_real_sale(c)]
        gross = sum(int(c.get("amount", 0)) for c in real) / 100.0
        live = any(c.get("livemode") for c in charges)
        summary = (f"Stripe (live read): {len(real)} real sale(s), ${gross:,.2f} gross "
                   f"across last {len(charges)} charges. livemode={live}.")
        return _log("check_stripe", ok=True, summary=summary,
                    evidence={"sales": len(real), "gross": gross,
                              "charges_scanned": len(charges), "livemode": live})
    except Exception as exc:  # noqa: BLE001
        return _log("check_stripe", ok=False,
                    summary=f"Tried to read Stripe and it failed: {exc}",
                    evidence={"error": str(exc)})


# --------------------------------------------------------------------------- EMAIL
def read_email(n: int = 5) -> dict:
    """Read REAL recent inbox messages and report findings. Honest on auth failure."""
    try:
        import imaplib
        import email as _email
        accounts = []
        biz = HOME / "secrets" / "business.json"
        if biz.exists():
            d = json.loads(biz.read_text())
            if d.get("email") and d.get("email_password"):
                accounts.append(d)
        gj = HOME / "secrets" / "gmail.json"
        if gj.exists():
            d = json.loads(gj.read_text())
            if d.get("user") and d.get("app_password"):
                accounts.append({"email": d["user"], "email_password": d["app_password"],
                                 "imap_host": "imap.gmail.com"})
        if not accounts:
            return _log("read_email", ok=False,
                        summary="Can't read email — no mailbox creds wired "
                                "(~/.utah/secrets/business.json or gmail.json). Not faking it.",
                        evidence={"creds": "absent"})
        acct = accounts[0]
        host = acct.get("imap_host") or "imap.gmail.com"
        conn = imaplib.IMAP4_SSL(host, timeout=30)
        conn.login(acct["email"], acct["email_password"])
        conn.select("INBOX")
        typ, data = conn.search(None, "ALL")
        ids = data[0].split()[-n:]
        msgs = []
        for i in reversed(ids):
            typ, raw = conn.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            hdr = _email.message_from_bytes(raw[0][1])
            msgs.append({"from": str(hdr.get("From", ""))[:80],
                         "subject": str(hdr.get("Subject", ""))[:120],
                         "date": str(hdr.get("Date", ""))[:40]})
        conn.logout()
        summary = f"Read {len(msgs)} most-recent in {acct['email']}: " + \
                  "; ".join(f"\"{m['subject']}\" from {m['from']}" for m in msgs[:3])
        return _log("read_email", ok=True, summary=summary,
                    evidence={"mailbox": acct["email"], "messages": msgs})
    except Exception as exc:  # noqa: BLE001
        return _log("read_email", ok=False,
                    summary=f"Tried to read email and it failed: {exc}",
                    evidence={"error": str(exc)})


# --------------------------------------------------------------------------- ALARM
def set_alarm(when: str, label: str = "Ace alarm") -> dict:
    """Create a REAL macOS reminder/alarm via Reminders. Evidence = the created item.
    TCC-honest: if Automation permission is denied, report it (don't pretend it set)."""
    safe_label = label.replace('"', "'")
    script = (
        f'tell application "Reminders" to make new reminder '
        f'with properties {{name:"{safe_label}", remind me date:(current date) + {_secs(when)}}}'
    )
    rc, out = _sh(["osascript", "-e", script], timeout=20)
    if rc == 0:
        return _log("set_alarm", ok=True,
                    summary=f"Set a reminder \"{label}\" for {when} (in {_secs(when)}s).",
                    evidence={"label": label, "when": when, "raw": out[:120]})
    return _log("set_alarm", ok=False,
                summary=f"Couldn't set the alarm — Reminders refused: {out[:140]} "
                        f"(likely needs Automation permission, grant once in System Settings).",
                evidence={"error": out[:200], "rc": rc})


def _secs(when: str) -> int:
    """Parse '10 min' / '2h' / '30s' / '1 hour' into seconds (default 600)."""
    m = re.search(r"(\d+)\s*(s|sec|m|min|h|hour)", (when or "").lower())
    if not m:
        return 600
    n, unit = int(m.group(1)), m.group(2)
    return n * (1 if unit.startswith("s") else 3600 if unit.startswith("h") else 60)


# --------------------------------------------------------------------------- SCREEN RECORD
def record_start(path: str | None = None) -> dict:
    """Start a REAL screen recording (screencapture -v) detached; write its pid so the
    STOP control can finalize it. Evidence = pid + output path. Needs Screen-Recording TCC."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    out = path or str(ACTIVITY_DIR / f"recording_{int(_now())}.mov")
    ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    pidfile = RUN_DIR / "record.pid"
    try:
        proc = subprocess.Popen(["screencapture", "-v", out],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pidfile.write_text(json.dumps({"pid": proc.pid, "path": out, "started": _now()}))
        return _log("record_start", ok=True,
                    summary=f"Recording the screen now (pid {proc.pid}). "
                            f"Say 'stop recording' or hit Stop to finalize.",
                    evidence={"pid": proc.pid, "path": out}, status="running")
    except Exception as exc:  # noqa: BLE001
        return _log("record_start", ok=False,
                    summary=f"Couldn't start recording: {exc} "
                            f"(may need Screen Recording permission in System Settings).",
                    evidence={"error": str(exc)})


def record_stop() -> dict:
    """Stop the active recording (SIGINT lets screencapture finalize the .mov) and report
    the real file size as proof it captured."""
    pidfile = RUN_DIR / "record.pid"
    if not pidfile.exists():
        return _log("record_stop", ok=False, summary="No recording is running.",
                    evidence={"state": "none"})
    info = json.loads(pidfile.read_text())
    try:
        os.kill(info["pid"], signal.SIGINT)
    except ProcessLookupError:
        pass
    for _ in range(20):
        if not _alive(info["pid"]):
            break
        time.sleep(0.2)
    pidfile.unlink(missing_ok=True)
    p = Path(info["path"])
    size = p.stat().st_size if p.exists() else 0
    ok = size > 0
    return _log("record_stop", ok=ok,
                summary=(f"Stopped recording — saved {size/1e6:.1f} MB to {info['path']}."
                         if ok else "Stopped, but no video file was produced "
                                    "(Screen Recording permission likely not granted)."),
                evidence={"path": info["path"], "bytes": size})


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- HEAL (close the loop)
def heal() -> dict:
    """Close the detect->ACT loop the canary leaves open. Real repairs, each verified:
      1. install any ops/launchd plist that's in-repo but NOT installed (drift);
      2. kickstart KeepAlive jobs that are dead (nonzero last exit, no pid).
    Reports exactly what it did and the after-state — never 'fixed' without re-checking."""
    if (RUN_DIR / "heal.disabled").exists():
        return _log("heal", ok=True, summary="heal paused (kill switch).", evidence={"state": "disabled"})
    actions = []
    # 0. daemon stale (source newer than running daemon) -> reload, cooldown-guarded
    actions += _reload_if_stale()
    # 1. drift: repo plists not installed
    if OPS_LAUNCHD.is_dir():
        installed = {p.stem for p in LAUNCHD.glob("com.utah.*.plist")}
        for src in sorted(OPS_LAUNCHD.glob("com.utah.*.plist")):
            if src.stem not in installed:
                dst = LAUNCHD / src.name
                rc, _ = _sh(["cp", str(src), str(dst)])
                _sh(["launchctl", "bootstrap", f"gui/{_uid()}", str(dst)])
                now_listed = src.stem in {j["label"] for j in _launchctl_list()}
                actions.append({"target": src.stem, "fix": "installed missing plist",
                                "verified": now_listed})
    # 2. dead KeepAlive jobs -> kickstart, then re-check
    before = {j["label"]: j for j in _launchctl_list()}
    for label, j in before.items():
        if j["pid"] is None and (j["last_exit"] or 0) not in (0,):
            if _job_target_missing(label):
                ok = _archive_job(label)
                actions.append({"target": label,
                                "fix": "orphan (program target deleted) -> archived + unloaded",
                                "verified": ok})
                continue
            if not _has_keepalive(label):
                actions.append({"target": label, "fix": "skipped (not KeepAlive; "
                                f"crashed exit {j['last_exit']})", "verified": False})
                continue
            _sh(["launchctl", "kickstart", "-k", f"gui/{_uid()}/{label}"])
            time.sleep(1.0)
            after = {x["label"]: x for x in _launchctl_list()}.get(label, {})
            actions.append({"target": label, "fix": "kickstarted",
                            "verified": bool(after.get("pid"))})
    # 3. Sovereign down -> start it (com.utah.heal re-runs this whenever it dies = autonomy)
    actions += _ensure_sovereign()
    fixed = sum(1 for a in actions if a.get("verified"))
    summary = (f"Healed {fixed}/{len(actions)} target(s): "
               + ("; ".join(f"{a['target']} -> {a['fix']}"
                            f"{' ✓' if a['verified'] else ''}" for a in actions)
                  if actions else "nothing to heal — all jobs installed and running.")) \
        if actions else "Nothing to heal — all launchd jobs installed and healthy."
    return _log("heal", ok=True, summary=summary, evidence={"actions": actions})


def _has_keepalive(label: str) -> bool:
    pl = LAUNCHD / f"{label}.plist"
    try:
        return "KeepAlive" in pl.read_text()
    except Exception:  # noqa: BLE001
        return False


def _job_target_missing(label: str) -> bool:
    """True if a launchd job points at a script file that no longer exists (an orphan that
    exits 127 every schedule). Conservative: only flags a concrete file-path arg with a
    script extension — never a `-m module` job we can't resolve to a file."""
    rc, out = _sh(["/usr/libexec/PlistBuddy", "-c", "Print :ProgramArguments",
                   str(LAUNCHD / f"{label}.plist")])
    if rc != 0:
        return False
    for line in out.splitlines():
        a = line.strip()
        if a.startswith("/") and a.rsplit(".", 1)[-1] in ("sh", "py", "command") and not Path(a).exists():
            return True
    return False


def _archive_job(label: str) -> bool:
    """Unload an orphan job and move its plist to _archived-ace (recoverable, not deleted)."""
    _sh(["launchctl", "bootout", f"gui/{_uid()}/{label}"])
    pl = LAUNCHD / f"{label}.plist"
    try:
        dst = LAUNCHD / "_archived-ace"
        dst.mkdir(parents=True, exist_ok=True)
        pl.rename(dst / pl.name)
        return not pl.exists()
    except Exception:  # noqa: BLE001
        return False


def _reload_if_stale() -> list[dict]:
    """Reload the daemon when source is newer than the running daemon (drift.stale_runtime),
    at most once per 10-min cooldown so heal never reload-loops. Verifies the deck returns."""
    try:
        from utah import drift
        stale = list(drift.stale_runtime())
    except Exception as exc:  # noqa: BLE001
        return [{"target": "daemon", "fix": f"drift probe failed: {exc}", "verified": False}]
    if not stale:
        return []
    last = RUN_DIR / "heal.last_reload"
    try:
        if last.exists() and _now() - float(last.read_text()) < 600:
            return [{"target": "daemon", "fix": "stale but within reload cooldown (skipped)",
                     "verified": False}]
    except Exception:  # noqa: BLE001
        pass
    _sh(["launchctl", "kickstart", "-k", f"gui/{_uid()}/com.utah.supervisor"])
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    last.write_text(str(_now()))
    ok = False
    for _ in range(10):
        time.sleep(1.0)
        if _port(8766) == 200:
            ok = True
            break
    return [{"target": "daemon", "fix": f"reloaded (stale: {str(stale[0])[:80]})", "verified": ok}]


def _sov_up() -> bool:
    """True only if Sovereign's API actually serves ok — JSON-parsed, not a loose substring
    match (an unactivated daemon can return ok:false; that is NOT up)."""
    for p in (8765, 8775):
        rc, out = _sh(["curl", "-s", "-m", "4", f"http://127.0.0.1:{p}/api/status"], timeout=7)
        if rc != 0 or not out:
            continue
        try:
            if json.loads(out).get("ok") is True:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _ensure_sovereign() -> list[dict]:
    """Restart Sovereign if its API is down — with NO human (com.utah.heal re-runs heal() on
    a heartbeat). HONEST: it distinguishes "process already up but API down" (needs
    `sov activate` — a license/EULA step only Michael can do; do NOT relaunch and spam GUIs)
    from "fully down" (start it once, detached, and verify it STAYS up before claiming ✓)."""
    if _sov_up():
        return []
    # The API (sock/:8765) is the real health signal — NOT whether some Sovereign.app GUI
    # happens to be running. `sov start` is the daemon that serves it (verified: no license
    # needed for the builder tier); if a daemon already holds the sock it errors harmlessly.
    sov = Path(os.path.expanduser("~/sovereign-live/.venv/bin/sov"))
    if not sov.exists():
        return [{"target": "sovereign", "fix": f"down and no sov binary at {sov}", "verified": False}]
    try:
        subprocess.Popen([str(sov), "start"], cwd=os.path.expanduser("~/sovereign-live"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)  # detach so it outlives this ephemeral job
    except Exception as exc:  # noqa: BLE001
        return [{"target": "sovereign", "fix": f"start raised {exc}", "verified": False}]
    up = False
    for _ in range(12):
        time.sleep(1.0)
        if _sov_up():
            up = True
            break
    if up:  # must STILL be up after a settle — catch the flap that reaped before
        time.sleep(3)
        up = _sov_up()
    return [{"target": "sovereign",
             "fix": "started ✓ (API answering)" if up
                    else "start attempted — API still down (needs `sov activate`: license/EULA)",
             "verified": up}]


# --------------------------------------------------------------------------- IMPROVE APPS
APP_QUEUE = RUN_DIR / "app_improve.queue"
BL_APPS = ["leads", "realestate", "marketing", "trading", "sovereign"]


def improve_apps(app: str | None = None) -> dict:
    """Enqueue the per-app engineer worker(s) to find+fix bugs autonomously. The
    com.blacklabel.improve launchd job drains this queue one app per cycle (serial build).
    Evidence = what got queued and the trigger sentinel bumped."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    targets = [app.lower()] if app else BL_APPS
    targets = [t for t in targets if t in BL_APPS]
    if not targets:
        return _log("improve_apps", ok=False,
                    summary=f"Don't know app {app!r}. Known: {', '.join(BL_APPS)}.",
                    evidence={"known": BL_APPS})
    with APP_QUEUE.open("a") as fh:
        for t in targets:
            fh.write(t + "\n")
    trig = RUN_DIR / "app_improve.trigger"
    trig.write_text(str(_now()))
    return _log("improve_apps", ok=True,
                summary=f"Queued autonomous improvement for: {', '.join(targets)}. "
                        f"The improve worker drains one app per cycle (find->fix->build->verify->deploy).",
                evidence={"queued": targets})


# --------------------------------------------------------------------------- ROUTER FACE
#: command pattern -> capability. Order matters: 'stop recording' before 'record'.
_INTENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(stop|end|finish)\s+(the\s+)?(recording|screen[\s-]?record)", re.I), "record_stop"),
    (re.compile(r"\b(record|capture)\s+(my\s+)?screen\b|\bscreen[\s-]?record(ing)?\b", re.I), "record_start"),
    (re.compile(r"\b(set|create|make|start)\s+(an?\s+)?(alarm|timer|reminder)\b|\bremind me\b|\bwake me\b", re.I), "alarm"),
    (re.compile(r"\b(stripe|sales|revenue|did we (make|sell|earn)|any (sales|revenue|money)|"
                r"have we (made|sold)|made any money)\b", re.I), "stripe"),
    (re.compile(r"\b(read|check|show|open|look at|go (in|into).*read)\b[^.]*\b(e-?mails?|inbox|messages?)\b|"
                r"\bwhat('?s| is| are)\b[^.]*\b(e-?mails?|inbox)\b|\bmy e-?mails?\b", re.I), "email"),
    (re.compile(r"\b(self[\s-]?heal|heal yourself|heal the system|fix (yourself|the daemon|the drift|the system)|"
                r"reload the daemon|repair yourself|close the loop)\b|\bheal\b", re.I), "heal"),
    (re.compile(r"\bdeploy workers?\b|\b(improve|work on|harden|fix)\b[^.]*\bapps?\b|"
                r"\b(improve|work on|fix|harden)\s+(the\s+)?(leads|real ?estate|marketing|trading|sovereign)\b", re.I), "improve"),
    (re.compile(r"\b(worker|workers)\b[^.]*\b(status|up|running|health|alive|down|state)\b|"
                r"\b(are|all)\s+(the\s+)?workers\b|\bactivate (all )?workers\b|"
                r"\b(system|daemon)\s+(status|health)\b|\bare you (up|running|alive)\b", re.I), "workers"),
]


def classify(text: str) -> str | None:
    for pat, intent in _INTENTS:
        if pat.search(text or ""):
            return intent
    return None


def is_control(text: str) -> bool:
    """True when *text* is an OS/account ACTUATOR command the router should send here."""
    return classify(text) is not None


def run(text: str) -> str:
    """Router entrypoint: execute the matched actuator and return its speakable summary
    (a REAL result or an honest 'I can't, here's why'). Mirrors actions.run()."""
    intent = classify(text)
    if intent is None:
        return "I didn't catch which thing to do."
    try:
        if intent == "record_start":
            return record_start()["summary"]
        if intent == "record_stop":
            return record_stop()["summary"]
        if intent == "alarm":
            when = (re.search(r"\b(in|for|after)\s+([\w\s]+?)(?:\s+to\b|$)", text, re.I) or [None, None, "10 min"])[2]
            lab = (re.search(r"\bto\s+(.+)$", text, re.I) or [None, "Ace alarm"])[1]
            return set_alarm((when or "10 min").strip(), lab.strip())["summary"]
        if intent == "stripe":
            return check_stripe()["summary"]
        if intent == "email":
            return read_email()["summary"]
        if intent == "heal":
            return heal()["summary"]
        if intent == "improve":
            m = re.search(r"\b(leads|real ?estate|marketing|trading|sovereign)\b", text, re.I)
            app = m.group(1).replace(" ", "").lower() if m else None
            return improve_apps(app)["summary"]
        if intent == "workers":
            return workers_status()["summary"]
    except Exception as exc:  # noqa: BLE001 — a failed actuator is REPORTED, never painted
        return f"I tried to {intent.replace('_', ' ')} and it failed: {exc}"
    return "I didn't catch which thing to do."


# --------------------------------------------------------------------------- CLI
_CAPS = {
    "workers": lambda a: workers_status(),
    "status": lambda a: workers_status(),
    "stripe": lambda a: check_stripe(),
    "sales": lambda a: check_stripe(),
    "email": lambda a: read_email(int(a[0]) if a else 5),
    "alarm": lambda a: set_alarm(a[0] if a else "10 min", " ".join(a[1:]) or "Ace alarm"),
    "record": lambda a: (record_stop() if a and a[0] == "stop" else record_start()),
    "heal": lambda a: heal(),
    "improve": lambda a: improve_apps(a[0] if a else None),
    "feed": lambda a: {"feed": feed(int(a[0]) if a else 20)},
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m utah.control <%s> [args]" % "|".join(_CAPS))
        return 0
    cmd, rest = argv[0], argv[1:]
    fn = _CAPS.get(cmd)
    if not fn:
        print(json.dumps({"ok": False, "summary": f"unknown capability {cmd!r}",
                          "known": list(_CAPS)}))
        return 2
    res = fn(rest)
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
