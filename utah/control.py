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
import sys
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


_id_counter = 0


def _new_id(ts: float) -> str:
    """A collision-proof row id: millisecond stamp + a process-local counter, so two
    receipts opened in the same millisecond (rapid begin() calls) never share an id and
    collapse into one in the panel."""
    global _id_counter
    _id_counter += 1
    return f"{int(ts*1000):x}{_id_counter:x}"


def _write_row(row: dict) -> dict:
    """Append one ledger row. Never raises — a dead ledger must not mask a real action."""
    try:
        ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001 — proof write is best-effort; the value is still returned
        pass
    return row


def _log(action: str, *, ok: bool, summary: str, evidence=None, **extra) -> dict:
    """Append one COMPLETED activity row. This IS the proof — the row is only written with
    the real result in hand."""
    ts = _now()
    return _write_row({
        "id": _new_id(ts),
        "ts": ts,
        "at": _stamp(ts),
        "action": action,
        "ok": bool(ok),
        "status": "done" if ok else "failed",
        "summary": summary,
        "evidence": evidence,
        **extra,
    })


# --- in-progress receipts: prove Ace is working RIGHT NOW (the live deck panel) --------
def begin(action: str, summary: str = "", *, timeout: float | None = None, **extra) -> str:
    """Open an in-progress receipt and return its id. Pair with ``end`` (and optional
    ``beat`` heartbeats). Writes a ``running`` row carrying ``started`` + ``timeout`` so the
    deck's proof panel can tick elapsed-seconds and show the timeout countdown — the
    deploy-subagent view Michael asked for. The work is REAL; this only makes it visible
    while it happens instead of only after it finishes."""
    ts = _now()
    rid = _new_id(ts)
    _write_row({
        "id": rid, "ts": ts, "at": _stamp(ts), "action": action,
        "ok": None, "status": "running", "summary": summary or f"{action}…",
        "started": ts, "timeout": timeout, "evidence": None, **extra,
    })
    return rid


def beat(rec_id: str, note: str | None = None, **extra) -> None:
    """Heartbeat/progress update for an open receipt — refreshes the row so the panel shows
    the latest step and the elapsed clock stays proven-live. No-op-safe if the id is
    unknown (records a fresh running row so progress is never silently lost)."""
    ts = _now()
    row = _latest(rec_id) or {"id": rec_id, "action": "?", "started": ts}
    row.update({"ts": ts, "at": _stamp(ts), "status": "running", "ok": None})
    if note is not None:
        row["summary"] = note
    row.update(extra)
    _write_row(row)


def end(rec_id: str, *, ok: bool, summary: str, evidence=None, **extra) -> dict:
    """Close an open receipt with the REAL result, stamping ``duration`` (now - started).
    The reader collapses the begin/beat/end rows (same id) to this terminal row."""
    ts = _now()
    row = _latest(rec_id) or {"id": rec_id, "action": "?", "started": ts}
    started = float(row.get("started", ts))
    row.update({
        "ts": ts, "at": _stamp(ts), "ok": bool(ok),
        "status": "done" if ok else "failed", "summary": summary,
        "evidence": evidence, "duration": round(ts - started, 2), **extra,
    })
    return _write_row(row)


def _all_rows(limit: int = 4000) -> list[dict]:
    """Parsed ledger rows in file (chronological) order, capped to the last *limit*."""
    try:
        lines = LEDGER.read_text().splitlines()[-limit:]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            pass
    return out


def _latest(rec_id: str) -> dict | None:
    """The most recent physical row for *rec_id* (begin/beat all share one id)."""
    found = None
    for r in _all_rows():
        if r.get("id") == rec_id:
            found = r
    return found


def activity(n: int = 12, *, max_age: float = 3600.0, now: float | None = None) -> dict:
    """Split the ledger into ``{active, recent}`` for the live proof panel. Groups rows by
    id (begin/beat/end share one), keeps the latest per id. A row still ``running`` is
    ACTIVE with server-computed ``elapsed`` + ``timeout``; one past 1.5x its timeout (or
    ``max_age`` if it set none) is flagged ``stalled`` so a crashed ``begin()`` can't look
    alive forever. ``recent`` is the finished rows, newest first, capped to *n*."""
    now = _now() if now is None else now
    latest: dict[str, dict] = {}
    for r in _all_rows():
        rid = r.get("id")
        if rid is not None:
            latest[rid] = r
    active, recent = [], []
    for r in latest.values():
        if r.get("status") == "running":
            started = float(r.get("started", r.get("ts", now)))
            elapsed = max(0.0, now - started)
            to = r.get("timeout")
            stalled = elapsed > (to * 1.5 if to else max_age)
            active.append({**r, "elapsed": round(elapsed, 1), "stalled": stalled})
        else:
            if r.get("started") is not None and "duration" not in r:
                r = {**r, "duration": round(float(r.get("ts", now)) - float(r["started"]), 2)}
            recent.append(r)
    active.sort(key=lambda r: r.get("started", 0), reverse=True)
    recent.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return {"active": active, "recent": recent[:n], "now": now}


def feed(n: int = 20) -> list[dict]:
    """The last *n* RAW activity rows — what Michael reads to SEE Ace actually did things."""
    return _all_rows(limit=n)


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


# --------------------------------------------------------------------------- SELF-DIAGNOSIS
def _git_state() -> dict:
    """Which code is actually live: branch + short SHA + dirty count of the repo the daemon
    imports from. The daemon runs WHATEVER branch is checked out (PYTHONPATH), so a stranded
    feature branch or an uncommitted edit = Ace silently running code that isn't on main."""
    repo = os.path.expanduser("~/ProjectUtah")
    rc, branch = _sh(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"])
    _, sha = _sh(["git", "-C", repo, "rev-parse", "--short", "HEAD"])
    _, dirty = _sh(["git", "-C", repo, "status", "--porcelain"])
    return {"branch": branch.strip() if rc == 0 else "?",
            "sha": sha.strip(), "dirty": len([l for l in dirty.splitlines() if l.strip()])}


def diagnose(*, log: bool = True) -> dict:
    """"What is wrong with me right now" — Ace's real self-health, from LIVE state, not a
    hardcoded model. Aggregates dead jobs, code/plist drift, down services, recent recorded
    failures, and which git branch/SHA is actually live, so when Michael asks "are you
    healthy / what's broken / what code are you running" Ace answers from truth."""
    problems = []
    git = _git_state()
    if git["dirty"] > 0:
        problems.append({"area": "code", "detail": f"{git['dirty']} uncommitted file(s) on "
                         f"{git['branch']} ({git['sha']}) — live code may differ from git"})
    if git["branch"] in ("HEAD", "?"):
        problems.append({"area": "code", "detail": f"detached HEAD ({git['sha']}) — not on a branch"})
    w = workers_status()
    for d in w["evidence"]["dead"]:
        problems.append({"area": "job", "detail": f"{d['label']} dead (exit {d['exit']})"})
    for name, code in (w["evidence"]["ports"] or {}).items():
        if code != 200:
            problems.append({"area": "service", "detail": f"{name} not serving (:{code or 'down'})"})
    try:
        from utah import drift
        for s in list(drift.stale_runtime()) + list(drift.plist_drift()):
            problems.append({"area": "drift", "detail": str(s)[:120]})
    except Exception as exc:  # noqa: BLE001
        problems.append({"area": "drift", "detail": f"drift probe failed: {exc}"})
    # recent recorded failures (last 6h) from the failures store, if reachable
    try:
        from utah import failures
        for f in (failures.recent(hours=6) or [])[:6]:
            problems.append({"area": "failure", "detail": f"{f.get('source')}/{f.get('kind')}: {str(f.get('detail'))[:70]}"})
    except Exception:  # noqa: BLE001
        pass
    ok = not problems
    summary = ("I'm healthy — no dead jobs, no drift, services up, no recent failures."
               if ok else f"{len(problems)} problem(s): "
               + "; ".join(p["detail"] for p in problems[:5])
               + (f" (+{len(problems)-5} more)" if len(problems) > 5 else ""))
    if not log:  # watch() calls diagnose quietly and emits ONE consolidated receipt
        return {"ok": ok, "summary": summary, "evidence": {"problems": problems}}
    return _log("diagnose", ok=ok, summary=summary, evidence={"problems": problems})


# --------------------------------------------------------------------------- SHELL (deck terminal)
SHELL_TIMEOUT_S = float(os.environ.get("UTAH_SHELL_TIMEOUT", "120"))


def run_shell(cmd: str, cwd: str | None = None, timeout: float = SHELL_TIMEOUT_S) -> dict:
    """Run a REAL shell command from Ace's deck terminal so Michael never opens Terminal.app.
    Every command is a proof receipt (begin→end) — the live panel shows it running with its
    timeout and the real exit code after. Bounded by *timeout* so a hung command can't wedge the
    terminal. Runs as the user from *cwd* (default ~/ProjectUtah); a trailing pwd marker is
    captured so ``cd`` follows across the session (the terminal tracks the returned ``cwd``).
    Runs on the localhost, CSRF-guarded deck — the AceOS full-access context Michael authorized."""
    cmd = (cmd or "").strip()
    workdir = cwd or os.path.expanduser("~/ProjectUtah")
    if not os.path.isdir(workdir):
        workdir = os.path.expanduser("~")
    if not cmd:
        return {"ok": False, "rc": None, "out": "", "cmd": cmd, "cwd": workdir, "duration": 0.0}
    rid = begin("shell", f"$ {cmd[:160]}", timeout=timeout, cwd=workdir)
    # Append a sentinel that prints the FINAL working dir, so an interactive `cd` updates the
    # terminal's cwd for the next command (each request is its own subprocess otherwise).
    wrapped = f"{cmd}\n___rc=$?\nprintf '__CWD__:%s\\n' \"$(pwd)\"\nexit $___rc"
    t0 = _now()
    try:
        p = subprocess.run(wrapped, shell=True, cwd=workdir, capture_output=True, text=True,
                           timeout=timeout, executable="/bin/zsh")
        out, rc = (p.stdout + p.stderr), p.returncode
        new_cwd = workdir
        lines = out.splitlines()
        for i in range(len(lines) - 1, -1, -1):       # last __CWD__ line wins
            if lines[i].startswith("__CWD__:"):
                new_cwd = lines.pop(i)[len("__CWD__:"):].strip() or workdir
                break
        out = "\n".join(lines)
        ok = rc == 0
        summary = f"$ {cmd[:120]} → rc {rc}"
    except subprocess.TimeoutExpired:
        out, rc, ok, new_cwd = f"(timed out after {timeout:.0f}s)", 124, False, workdir
        summary = f"$ {cmd[:120]} → TIMEOUT {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 — a failed exec is reported, never painted as ok
        out, rc, ok, new_cwd = f"(failed to run: {exc})", 127, False, workdir
        summary = f"$ {cmd[:120]} → error"
    dur = round(_now() - t0, 2)
    end(rid, ok=ok, summary=summary,
        evidence={"cmd": cmd, "cwd": new_cwd, "rc": rc, "bytes": len(out)})
    return {"ok": ok, "rc": rc, "out": out, "cmd": cmd, "cwd": new_cwd, "duration": dur}


# --------------------------------------------------------------------------- WORKING ON
def working_on() -> dict:
    """What Ace is doing RIGHT NOW — read from the LIVE activity ledger (open receipts), never
    guessed. Reports each running action and how long it's been going; when nothing is running
    it says so honestly and names the last finished action. This is the self-knowledge answer to
    "what are you working on / what are you doing right now" (diagnose() covers health,
    introspect.self_model() covers "who/what are you")."""
    act = activity(n=5)
    live = act["active"]

    def _dur(e: float) -> str:
        return f"{e:.0f}s" if e < 90 else f"{e/60:.1f}m"

    if not live:
        recent = act["recent"][:1]
        tail = (f" Last finished: {recent[0].get('action')} — "
                f"{(recent[0].get('summary') or '')[:90]}." if recent else "")
        return _log("working_on", ok=True, summary="Nothing running right now — idle." + tail,
                    evidence={"active": [], "recent_count": len(act["recent"])})
    parts = [f"{a.get('action')} ({_dur(a.get('elapsed', 0))}"
             f"{', STALLED' if a.get('stalled') else ''})" for a in live]
    summary = f"Working on {len(live)} thing(s) right now: " + "; ".join(parts) + "."
    return _log("working_on", ok=True, summary=summary, evidence={"active": live})


# --------------------------------------------------------------------------- WATCH (self-loop)
def _watch_page(message: str, *, key: str) -> None:
    """Page Michael via the deduped alert lane (same one the canary uses, so a recurring issue
    can't storm). Best-effort — a dead pager never breaks the watch loop."""
    from utah import alerts

    alerts.critical_async("watch", message, key=key)


def watch(*, diagnose_fn=None, heal_fn=None, notify_fn=None) -> dict:
    """The unifying self-watch loop — runs on a cadence (com.utah.watch) + safe on demand:

        diagnose() → auto-heal the deterministic cases → page Michael for what needs a human →
        log proof ONLY when something was actually wrong (no all-clear spam in /activity).

    NO autonomous code edits — the safe shape Michael chose: it fixes the verified, mechanical
    things itself (dead jobs, drift, sovereign) and SURFACES everything else rather than letting
    the brain blindly "fix" novel issues (which is where degeneracy/churn live). Fns are
    injectable for tests; defaults call the real diagnose/heal quietly + the deduped pager."""
    dg = diagnose_fn or (lambda: diagnose(log=False))
    hl = heal_fn or (lambda: heal(log=False))
    notify = notify_fn or _watch_page

    problems = (dg().get("evidence") or {}).get("problems") or []
    if not problems:
        return {"ok": True, "summary": "All clear — nothing wrong.",
                "evidence": {"detected": [], "healed": [], "remaining": []}}
    rid = begin("watch", f"{len(problems)} issue(s) detected — healing + triaging", timeout=180)
    healed = (hl().get("evidence") or {}).get("actions") or []
    remaining = (dg().get("evidence") or {}).get("problems") or []   # what heal could NOT fix
    # Page only for things that NEED a human now (dead jobs, down services, dead brain, drift,
    # recorded failures). "code" drift (uncommitted/detached HEAD) is normal during dev — it's
    # surfaced in /activity but never paged, so the loop can't become alert-fatigue noise.
    paged = 0
    for p in remaining:
        if p.get("area") == "code":
            continue
        try:
            notify(f"{p.get('area')} — {p.get('detail')}",
                   key=f"watch:{p.get('area')}:{(p.get('detail') or '')[:40]}")
            paged += 1
        except Exception:  # noqa: BLE001 — paging is best-effort
            pass
    summary = (f"Detected {len(problems)} issue(s); auto-healed {len(healed)}; "
               f"{len(remaining)} still need attention"
               + (f" — paged you on {paged}" if paged else "") + ".")
    return end(rid, ok=not remaining, summary=summary,
               evidence={"detected": problems, "healed": healed, "remaining": remaining,
                         "paged": paged})


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
def read_email(n: int = 5, mailbox: str | None = None) -> dict:
    """Read REAL recent inbox messages via the canonical account pool (utah.mail.accounts():
    email_accounts.json / gmail.json — `from` + `app_password` + `smtp_host`) and the IMAP
    host derived by mail_replies.imap_host_for. *mailbox* picks one of the pool's `from`
    addresses; default is the first. Honest on auth failure — never faked."""
    try:
        import email as _email
        import imaplib

        from utah import mail
        from utah.mail_replies import imap_host_for

        accts = mail.accounts()
        if not accts:
            return _log("read_email", ok=False,
                        summary="Can't read email — no mailbox creds in the account pool "
                                "(~/.utah/secrets/email_accounts.json or gmail.json). Not faking it.",
                        evidence={"creds": "absent"})
        acct = next((a for a in accts if mailbox and mailbox.lower() in (a.get("from") or "").lower()), accts[0])
        host = imap_host_for(acct)
        conn = imaplib.IMAP4_SSL(host, timeout=30)
        conn.login(acct["from"], acct["app_password"])
        conn.select("INBOX")
        _typ, data = conn.search(None, "ALL")
        ids = data[0].split()[-n:]
        msgs = []
        for i in reversed(ids):
            _t, raw = conn.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            hdr = _email.message_from_bytes(raw[0][1])
            msgs.append({"from": str(hdr.get("From", ""))[:80],
                         "subject": str(hdr.get("Subject", ""))[:120],
                         "date": str(hdr.get("Date", ""))[:40]})
        conn.logout()
        summary = (f"Read {len(msgs)} most-recent in {acct['from']}: "
                   + "; ".join(f"\"{m['subject']}\" from {m['from']}" for m in msgs[:3]))
        return _log("read_email", ok=True, summary=summary,
                    evidence={"mailbox": acct["from"], "host": host, "messages": msgs})
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
        summary = (f"Recording the screen now (pid {proc.pid}). "
                   f"Say 'stop recording' or hit Stop to finalize.")
        # An OPEN receipt — closed by record_stop via its id, so the recording shows as a
        # live action while it runs and never lingers as a phantom 'running' row.
        rid = begin("record_start", summary, timeout=None, evidence={"pid": proc.pid, "path": out})
        pidfile.write_text(json.dumps({"pid": proc.pid, "path": out, "started": _now(), "rid": rid}))
        return {"ok": True, "summary": summary, "status": "running", "id": rid,
                "evidence": {"pid": proc.pid, "path": out}}
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
    summary = (f"Stopped recording — saved {size/1e6:.1f} MB to {info['path']}."
               if ok else "Stopped, but no video file was produced "
                          "(Screen Recording permission likely not granted).")
    evidence = {"path": info["path"], "bytes": size}
    rid = info.get("rid")
    if rid:  # close the open record_start receipt (duration = recording length)
        return end(rid, ok=ok, summary=summary, evidence=evidence)
    return _log("record_stop", ok=ok, summary=summary, evidence=evidence)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- HEAL (close the loop)
def _brain_health() -> list[dict]:
    """Probe the Claude CLI — Ace's reasoning. It's resolved off PATH as a symlink into a
    versioned dir (~/.local/bin/claude -> .../versions/X); an auto-update or GC can dangle
    it, and then EVERY brain turn fails (BrainUnavailable) with no detection. If `claude
    --version` fails, try to re-point the symlink at the newest versions/* dir; else report
    honestly (a dead brain is the worst silent failure there is)."""
    import shutil

    cli = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    rc, out = _sh([cli, "--version"], timeout=15)
    if rc == 0 and out.strip():
        return []  # reasoning healthy
    # broken — attempt to re-point a dangling ~/.local/bin/claude at the newest version
    link = Path(os.path.expanduser("~/.local/bin/claude"))
    try:
        versions = sorted((link.resolve().parent.parent / "versions").glob("*/claude"),
                          key=lambda p: p.name, reverse=True) if link.is_symlink() else []
    except Exception:  # noqa: BLE001
        versions = []
    for cand in versions:
        if cand.exists():
            try:
                link.unlink(missing_ok=True)
                link.symlink_to(cand)
                rc2, _ = _sh([str(link), "--version"], timeout=15)
                if rc2 == 0:
                    return [{"target": "brain", "fix": f"re-pointed dangling claude symlink -> {cand}",
                             "verified": True}]
            except Exception:  # noqa: BLE001
                pass
    return [{"target": "brain",
             "fix": f"BRAIN DOWN — `claude` at {cli} not runnable (rc={rc}); reasoning is offline, "
                    f"could not auto-repair. Reinstall/relink the Claude CLI.", "verified": False}]


def heal(*, log: bool = True) -> dict:
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
    # 4. brain (claude CLI) reachable? it's Ace's reasoning — a dangling symlink kills EVERY
    # brain turn with zero detection. Probe + best-effort repair.
    actions += _brain_health()
    fixed = sum(1 for a in actions if a.get("verified"))
    if not actions:
        # A no-op heal ran every few minutes; logging "nothing to heal" each time buries the
        # REAL repairs in the proof feed. Return the honest result, but only write a receipt
        # when heal actually DID something (a row = a real action, not a heartbeat).
        return {"ok": True, "summary": "Nothing to heal — all launchd jobs installed and healthy.",
                "evidence": {"actions": []}}
    summary = ("Healed {}/{} target(s): ".format(fixed, len(actions))
               + "; ".join(f"{a['target']} -> {a['fix']}{' ✓' if a['verified'] else ''}"
                           for a in actions))
    if not log:  # watch() heals quietly and rolls the result into its own receipt
        return {"ok": True, "summary": summary, "evidence": {"actions": actions}}
    return _log("heal", ok=True, summary=summary, evidence={"actions": actions})


def _has_keepalive(label: str) -> bool:
    pl = LAUNCHD / f"{label}.plist"
    try:
        return "KeepAlive" in pl.read_text()
    except Exception:  # noqa: BLE001
        return False


def _job_target_missing(label: str) -> bool:
    """True if a launchd job points at a target that no longer exists — a missing script
    FILE (exits 127), or a missing `-m module` (exits with ModuleNotFoundError and, under
    KeepAlive, crash-loops forever, e.g. com.utah.engine-bridge -> utah.integrations.
    engine_bridge which was deleted). Conservative: only flags a concrete file path with a
    script extension, or an unimportable `-m` module."""
    rc, out = _sh(["/usr/libexec/PlistBuddy", "-c", "Print :ProgramArguments",
                   str(LAUNCHD / f"{label}.plist")])
    if rc != 0:
        return False
    args = [ln.strip() for ln in out.splitlines() if ln.strip() not in ("Array {", "}")]
    for a in args:
        if a.startswith("/") and a.rsplit(".", 1)[-1] in ("sh", "py", "command") and not Path(a).exists():
            return True
    if "-m" in args:
        mod = args[args.index("-m") + 1] if args.index("-m") + 1 < len(args) else ""
        if mod and "." in mod:  # a dotted module path we can resolve
            chk = _sh([sys.executable, "-c",
                       f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec({mod!r}) else 1)"])
            if chk[0] == 1:  # find_spec returned None -> module gone
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
    (re.compile(r"\b(self[\s-]?heal|heal (yourself|the system|the daemon|the drift|the jobs|everything)|"
                r"fix (yourself|the daemon|the drift|the system)|reload the daemon|repair yourself|"
                r"close the loop)\b|^\s*heal( now| up| the system| yourself)?\s*[.!]?$", re.I), "heal"),
    (re.compile(r"\bdeploy workers?\b|\b(improve|work on|harden|fix)\b[^.]*\bapps?\b|"
                r"\b(improve|work on|fix|harden)\s+(the\s+)?(leads|real ?estate|marketing|trading|sovereign)\b", re.I), "improve"),
    (re.compile(r"\b(scan|sweep)\b[^.]*\b(issues?|problems?|everything|and (fix|heal))\b|"
                r"\bfix (anything|whatever('?s| is)?|everything)\s+(wrong|broken)\b|"
                r"\b(keep (an eye|watch)|self[\s-]?watch|watch (for issues|over (the )?system))\b|"
                r"\bcheck everything and (fix|heal)\b", re.I), "watch"),
    (re.compile(r"\bwhat (are|r) you (doing|working on|up to|busy with)\b|"
                r"\bwhat'?s ace (doing|working on)\b|"
                r"\bare you (doing|working on) (anything|something)\b|"
                r"\bwhat are you currently\b", re.I), "working_on"),
    (re.compile(r"\b(what('?s| is) wrong|are you (ok|okay|healthy|good|broken|fine)|"
                r"what('?s| is) broken|anything (wrong|broken)|diagnose( yourself)?|"
                r"self[\s-]?diagnos|health check|how('?re| are) you (doing|feeling))\b", re.I), "diagnose"),
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
        if intent == "diagnose":
            return diagnose()["summary"]
        if intent == "working_on":
            return working_on()["summary"]
        if intent == "watch":
            return watch()["summary"]
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
    "diagnose": lambda a: diagnose(),
    "health": lambda a: diagnose(),
    "working_on": lambda a: working_on(),
    "doing": lambda a: working_on(),
    "watch": lambda a: watch(),
    "improve": lambda a: improve_apps(a[0] if a else None),
    "feed": lambda a: {"feed": feed(int(a[0]) if a else 20)},
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m utah.control <%s> [args]" % "|".join(_CAPS))
        return 0
    cmd, rest = argv[0], argv[1:]
    # in-progress receipt CLI (used by the long autonomous workers, e.g. improve.sh):
    #   begin <action> [summary] [timeout]  -> prints the id (capture it for `end`)
    #   beat  <id> [note]
    #   end   <id> <ok:1|0> <summary...>
    if cmd == "begin":
        rid = begin(rest[0] if rest else "work",
                    rest[1] if len(rest) > 1 else "",
                    timeout=float(rest[2]) if len(rest) > 2 and rest[2] else None)
        print(rid)
        return 0
    if cmd == "beat":
        beat(rest[0], rest[1] if len(rest) > 1 else None)
        return 0
    if cmd == "end":
        ok = (rest[1].lower() in ("1", "true", "ok", "yes")) if len(rest) > 1 else False
        res = end(rest[0], ok=ok, summary=" ".join(rest[2:]) or ("done" if ok else "failed"))
        print(json.dumps(res, default=str))
        return 0 if ok else 1
    if cmd == "activity":
        print(json.dumps(activity(int(rest[0]) if rest else 12), indent=2, default=str))
        return 0
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
