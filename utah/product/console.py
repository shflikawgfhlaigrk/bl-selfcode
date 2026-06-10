"""Self-code CONSOLE — a genuine slash-command terminal for the deck's SELF-CODE dash.

Michael drives Ace's OWN coding from the deck (ground-rule #4: never open a terminal
again — the surface does everything the terminal does). Every ``/`` command is wired to
a REAL capability and never fabricates; honest-empty on any failure.

  /help            list the commands
  /status          self-coder status (kill-switch, auto-merge, supervised count, load)
  /findings        pending browser-agent findings (frontend/research) + their tasks
  /cycles [n]      recent self-code cycles (task · domain · utility · passed · merged)
  /goals           the % goals (every number a real count)
  /diff <branch>   a proposal branch's diff vs main (from the isolated clone)
  /code <task>     run a REAL governed Claude coding cycle (Edit/Write/Read/Bash on the
                   isolated clone, suite-gated) — SLOW (minutes); PROPOSE-ONLY, never merges
  /do <n>          run pending finding #n's suggested task as ``/code``
  /ask <q>         ask the grounded brain (handled by the chat lane); a bare line = /ask

FAST commands return inline; the SLOW ones (``/code`` / ``/do``) are enqueued by the web
layer on the existing self-code job runner (``selfcode_web.run_edit`` — ``auto_merge=False``,
so a console edit NEVER reaches ``main``). Pure + injectable so it is unit-proven without a
DB, git tree, or a real ``claude`` process.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.product.console")

#: command -> one-line help. Order is display order in /help.
COMMANDS: dict[str, str] = {
    "help": "list the commands",
    "status": "self-coder status (kill-switch · auto-merge · supervised · load)",
    "findings": "pending browser-agent findings + their suggested tasks",
    "cycles": "recent self-code cycles (task · domain · utility · passed · merged)",
    "goals": "the % goals (real counts)",
    "diff": "/diff <branch> — a proposal branch's diff vs main",
    "code": "/code <task> — run a REAL Claude coding cycle (propose-only, isolated clone)",
    "do": "/do <n> — run pending finding #n as /code",
    "ask": "/ask <q> — ask the grounded brain (a bare line is also /ask)",
    "effort": "/effort [low|medium|high|ultracode] — coding-job effort (budget + rigor)",
    "skills": "everything Ace can do — daemon capabilities · selfcode domains · commands",
    "history": "/history [n] — the recent chat conversation (voice + deck), newest last",
}

#: Commands that run a real ``claude`` coding cycle → must be enqueued (minutes), not inline.
SLOW: frozenset[str] = frozenset({"code", "do"})


def parse(line: str) -> tuple[str, str]:
    """Split a console line into ``(command, argument)``.

    A leading ``/`` selects a command; a bare line (no slash) defaults to ``ask`` so the
    terminal is useful without memorizing commands. An unknown ``/word`` returns
    ``("unknown", word)`` so the caller can show a hint (never silently mis-runs).
    """
    s = (line or "").strip()
    if not s:
        return "", ""
    if not s.startswith("/"):
        return "ask", s
    head, _, rest = s[1:].partition(" ")
    cmd = head.strip().lower()
    arg = rest.strip()
    if cmd in COMMANDS:
        return cmd, arg
    return "unknown", head.strip()


def is_slow(cmd: str) -> bool:
    return cmd in SLOW


# --- fast command bodies (each returns terminal-ready text; never fabricated) -------------

def _help_text() -> str:
    width = max(len(c) for c in COMMANDS) + 1
    lines = ["UTAH SELF-CODE CONSOLE — genuine / commands:", ""]
    lines += [f"  /{c.ljust(width)} {COMMANDS[c]}" for c in COMMANDS]
    lines += ["", "A bare line (no slash) asks the grounded brain. /code & /do run a real",
              "Claude cycle on the isolated clone (propose-only — it never merges to main)."]
    return "\n".join(lines)


def _status_text(*, selfcode=None, loadavg=None) -> str:
    try:
        from utah import selfcode as _sc
        sc = selfcode or _sc
        killed = not sc.enabled()
        automerge = sc.automerge_enabled()
        try:
            supervised = sc._read_supervised()  # noqa: SLF001 — package-internal accessor
        except Exception:  # noqa: BLE001
            supervised = "?"
    except Exception as exc:  # noqa: BLE001
        return f"(self-coder status unavailable: {type(exc).__name__})"
    try:
        import os
        lpc = (loadavg or (lambda: os.getloadavg()[0] / (os.cpu_count() or 1)))()
        load = f"{lpc:.2f}/core"
    except Exception:  # noqa: BLE001
        load = "?"
    return ("SELF-CODER STATUS\n"
            f"  kill-switch : {'ON (self-coding DISABLED)' if killed else 'off (enabled)'}\n"
            f"  auto-merge  : {'armed (Tier-A on green)' if automerge else 'off (propose-only)'}\n"
            f"  supervised  : {supervised} green proposals (Tier-A unlocks at 3)\n"
            f"  load        : {load}\n"
            "  console     : PROPOSE-ONLY — /code never merges to main; review + merge stays yours")


def _findings_text(*, pending_fn=None, harvest_fn=None) -> str:
    try:
        (harvest_fn or sica_discover.harvest_failure_findings)()   # self-heal refresh
    except Exception as exc:  # noqa: BLE001 — harvest trouble never hides findings
        log.debug("selfheal harvest skipped: %s", exc)
    try:
        from utah import sica_discover
        pending = (pending_fn or sica_discover.pending_findings)()
    except Exception as exc:  # noqa: BLE001
        return f"(findings unavailable: {type(exc).__name__})"
    if not pending:
        return "No pending browser-agent findings. (Ace discovers them each self-code cycle:\n" \
               "  frontend = renders its OWN deck; research = web-searches its top failure.)"
    lines = [f"PENDING BROWSER-AGENT FINDINGS ({len(pending)}) — /do <n> to action one:", ""]
    for i, (domain, task, _rec) in enumerate(pending, 1):
        lines.append(f"  [{i}] ({domain}) {task[:140]}")
    return "\n".join(lines)


def _cycles_text(arg: str, *, cycles_fn=None) -> str:
    try:
        n = max(1, min(int(arg), 50)) if arg.strip().isdigit() else 8
    except Exception:  # noqa: BLE001
        n = 8
    try:
        from utah.product import selfcode_web
        rows = (cycles_fn or selfcode_web.recent_cycles)(n)
    except Exception as exc:  # noqa: BLE001
        return f"(cycles unavailable: {type(exc).__name__})"
    if not rows:
        return "No self-code cycles logged yet."
    lines = [f"RECENT SELF-CODE CYCLES ({len(rows)}):", ""]
    for c in rows:
        u = c.get("utility")
        flags = ("merged" if c.get("merged") else ("passed" if c.get("passed") else "—"))
        lines.append(f"  {c.get('domain','?'):9} U={u if u is not None else '—':<6} {flags:7} "
                     f"{(c.get('task') or '')[:90]}")
    return "\n".join(lines)


def _goals_text(*, goals_fn=None) -> str:
    try:
        from utah.product import selfcode_web
        gs = (goals_fn or selfcode_web.goals)()
    except Exception as exc:  # noqa: BLE001
        return f"(goals unavailable: {type(exc).__name__})"
    if not gs:
        return "(no goals)"
    lines = ["SELF-CODE GOALS (real counts):", ""]
    for g in gs:
        bar = "█" * (g.get("pct", 0) // 10) + "░" * (10 - g.get("pct", 0) // 10)
        lines.append(f"  {g.get('name',''):22} [{bar}] {g.get('pct',0):3}%  {g.get('detail','')}")
    return "\n".join(lines)


def _diff_text(branch: str, *, diff_fn=None) -> str:
    branch = (branch or "").strip()
    if not branch:
        return "usage: /diff <branch>  (a selfcode/<slug> branch on the isolated clone)"
    try:
        from utah.product import selfcode_web
        d = (diff_fn or (lambda b: selfcode_web._branch_diff(selfcode_web.REPO_DIR, b)))(branch)
    except Exception as exc:  # noqa: BLE001
        return f"(diff unavailable: {type(exc).__name__})"
    return d.strip()[:6000] if d and d.strip() else f"(no diff for {branch} — branch missing or empty)"


#: Console effort mode (Claude-style): budget + rigor for /code jobs. Module state —
#: the web process owns the console session, exactly like Claude Code's /effort.
EFFORT_LEVELS: dict[str, dict] = {
    "low": {"timeout": 300.0, "note": "fast, minimal"},
    "medium": {"timeout": 600.0, "note": "default balance"},
    "high": {"timeout": 1200.0, "note": "thorough — tests + edge cases"},
    "ultracode": {"timeout": 2400.0, "note": "maximum rigor — exhaustive, self-review pass"},
}
_EFFORT = "medium"


def effort() -> str:
    return _EFFORT


def effort_budget() -> float:
    return float(EFFORT_LEVELS[_EFFORT]["timeout"])


def effort_suffix() -> str:
    """Instruction appended to /code tasks so the agent's rigor matches the mode."""
    if _EFFORT == "low":
        return " (Effort LOW: smallest correct change, skip extras.)"
    if _EFFORT == "high":
        return (" (Effort HIGH: be thorough — cover edge cases, write/extend tests, "
                "verify before finishing.)")
    if _EFFORT == "ultracode":
        return (" (Effort ULTRACODE: maximum rigor — exhaustive edge cases, tests for "
                "every branch, then a self-review pass for bugs before finishing.)")
    return ""


def _effort_text(arg: str) -> str:
    global _EFFORT
    a = (arg or "").strip().lower()
    if not a:
        rows = [f"  {'>' if k == _EFFORT else ' '} {k.ljust(10)} {v['note']} "
                f"(budget {int(v['timeout'])}s)" for k, v in EFFORT_LEVELS.items()]
        return "effort mode (applies to /code · /do):\n" + "\n".join(rows)
    if a not in EFFORT_LEVELS:
        return f"unknown effort: {a} — pick " + "|".join(EFFORT_LEVELS)
    _EFFORT = a
    return (f"effort -> {a} ({EFFORT_LEVELS[a]['note']}; budget "
            f"{int(EFFORT_LEVELS[a]['timeout'])}s). Applies to the next /code · /do.")


def _skills_text() -> str:
    """The REAL skill surface — daemon RPC capabilities, selfcode domains, console
    commands. Pulled from the live registries, never a hardcoded brochure."""
    parts = ["ACE SKILL SURFACE", ""]
    try:
        from utah.daemon.handlers.core_handlers import REGISTRY
        parts.append(f"daemon capabilities ({len(REGISTRY)}):")
        parts.append("  " + " · ".join(sorted(REGISTRY)))
    except Exception as exc:  # noqa: BLE001
        parts.append(f"daemon capabilities: unavailable ({exc})")
    try:
        from utah import sica_autonomy
        domains = getattr(sica_autonomy, "DOMAINS", None)
        if domains:
            parts += ["", f"selfcode domains ({len(domains)}):",
                      "  " + " · ".join(sorted(domains))]
    except Exception:  # noqa: BLE001
        pass
    try:
        from utah.daemon.handlers.panels import PANEL_REGISTRY
        parts += ["", f"deck panels ({len(PANEL_REGISTRY)}):",
                  "  " + " · ".join(sorted(PANEL_REGISTRY))]
    except Exception:  # noqa: BLE001
        pass
    parts += ["", f"console commands ({len(COMMANDS)}):",
              "  " + " · ".join("/" + c for c in COMMANDS)]
    return "\n".join(parts)


def _history_text(arg: str, *, turns_fn=None) -> str:
    """The recent conversation (voice + deck chat), newest LAST — the whole-chat view."""
    try:
        n = max(1, min(int(arg or 12), 40))
    except ValueError:
        n = 12
    if turns_fn is None:
        def turns_fn(limit):
            import psycopg

            from utah import config
            with psycopg.connect(config.DB_DSN, autocommit=True) as c:
                return [(str(r[0])[:16], r[1]) for r in c.execute(
                    "SELECT ts, content FROM memory WHERE source='turn' "
                    "ORDER BY ts DESC LIMIT %s", (limit,))]
    try:
        rows = turns_fn(n)
    except Exception as exc:  # noqa: BLE001
        return f"history unavailable: {exc}"
    if not rows:
        return "no conversation recorded yet"
    out = []
    for ts, content in reversed(rows):
        out.append(f"[{ts}] {content}")
    return "\n".join(out)


def run_fast(cmd: str, arg: str = "", **inject) -> dict:
    """Run a FAST command and return ``{"output": text}`` (terminal-ready). Never raises;
    every body degrades to an honest message. ``inject`` carries test doubles."""
    if cmd == "help" or cmd == "":
        return {"output": _help_text()}
    if cmd == "status":
        return {"output": _status_text(selfcode=inject.get("selfcode"), loadavg=inject.get("loadavg"))}
    if cmd == "findings":
        return {"output": _findings_text(pending_fn=inject.get("pending_fn"))}
    if cmd == "cycles":
        return {"output": _cycles_text(arg, cycles_fn=inject.get("cycles_fn"))}
    if cmd == "goals":
        return {"output": _goals_text(goals_fn=inject.get("goals_fn"))}
    if cmd == "diff":
        return {"output": _diff_text(arg, diff_fn=inject.get("diff_fn"))}
    if cmd == "effort":
        return {"output": _effort_text(arg)}
    if cmd == "skills":
        return {"output": _skills_text()}
    if cmd == "history":
        return {"output": _history_text(arg, turns_fn=inject.get("turns_fn"))}
    if cmd == "unknown":
        return {"output": f"unknown command: /{arg}  —  try /help"}
    return {"output": f"/{cmd} is a coding command — it runs as a job (see /help)"}


def resolve_slow_task(cmd: str, arg: str, *, pending_fn=None, mark_fn=None) -> tuple[str, dict | None]:
    """Resolve a SLOW command to the actual task string the coder will run, plus the
    finding record to mark used (``/do``). Returns ``(task, record_or_None)``; ``("", None)``
    when nothing actionable. Injectable for tests; never raises."""
    if cmd == "code":
        return (arg.strip(), None)
    if cmd == "do":
        try:
            from utah import sica_discover
            pending = (pending_fn or sica_discover.pending_findings)()
        except Exception:  # noqa: BLE001
            return ("", None)
        try:
            idx = int(arg.strip()) - 1
        except (TypeError, ValueError):
            return ("", None)
        if not (0 <= idx < len(pending)):
            return ("", None)
        _domain, task, rec = pending[idx]
        return (task.strip(), rec)
    return ("", None)


# --- live agentic streaming: run claude WITH tools and stream every action ---------------

def _format_tool(tool: str, raw_input: str) -> str:
    """One tool-use → a readable terminal line. The Bash branch is the headline: you SEE
    the command Ace runs. Never raises."""
    import json as _json
    try:
        inp = _json.loads(raw_input) if raw_input.strip() else {}
    except Exception:  # noqa: BLE001
        inp = {}
    t = (tool or "").lower()
    if t == "bash":
        cmd = str(inp.get("command") or "").strip().replace("\n", " ⏎ ")
        return f"  $ {cmd[:240]}" if cmd else "  $ (bash)"
    if t in ("write", "edit", "multiedit"):
        return f"  ✎ {tool} {inp.get('file_path') or inp.get('path') or ''}".rstrip()
    if t == "read":
        return f"  📖 read {inp.get('file_path') or inp.get('path') or ''}".rstrip()
    if t in ("grep", "glob"):
        return f"  🔎 {tool} {inp.get('pattern') or inp.get('query') or ''}".rstrip()
    return f"  ⚙ {tool}"


def format_stream_event(evt: dict, state: dict) -> list[str]:
    """Turn one Claude CLI ``stream-json`` event into readable console line(s): assistant
    text, and every tool call (Bash/Read/Write/Edit/Grep). ``state`` carries the in-progress
    tool name + accumulated input-json + text across calls. Pure; never raises."""
    out: list[str] = []
    try:
        if evt.get("type") != "stream_event":
            if evt.get("type") == "result":
                sub = evt.get("subtype") or ""
                if sub and sub != "success":
                    out.append(f"  · {sub}")
            return out
        inner = evt.get("event") or {}
        etype = inner.get("type")
        if etype == "content_block_start":
            block = inner.get("content_block") or {}
            if block.get("type") == "tool_use":
                state["tool"] = block.get("name") or "tool"
                state["tin"] = ""
        elif etype == "content_block_delta":
            d = inner.get("delta") or {}
            dt = d.get("type")
            if dt == "text_delta":
                state["text"] = state.get("text", "") + (d.get("text") or "")
            elif dt == "input_json_delta":
                state["tin"] = state.get("tin", "") + (d.get("partial_json") or "")
        elif etype == "content_block_stop":
            if state.get("text"):
                out.append(state["text"].strip())
                state["text"] = ""
            tool = state.pop("tool", None)
            if tool:
                line = _format_tool(tool, state.pop("tin", "") or "")
                if line:
                    out.append(line)
    except Exception:  # noqa: BLE001 — a malformed event never breaks the stream
        return out
    return out


def run_claude_streamed(task: str, *, cwd: str, on_line, timeout: float = 600.0,
                        brain_cmd=None) -> None:
    """Run ``claude -p`` WITH coding tools and STREAM its live actions to ``on_line(str)`` —
    assistant text + every Bash/Read/Write/Edit. Bounded by *timeout* (kill + TimeoutExpired
    so the SICA τ-penalty applies); RuntimeError on a non-zero exit so the gate discards the
    change. The genuine agentic terminal: you SEE Ace work, including when he runs bash."""
    import json as _json
    import subprocess
    import time as _t

    from utah import config

    cmd = [brain_cmd or config.BRAIN_CMD, "-p", "--output-format", "stream-json",
           "--verbose", "--include-partial-messages",
           "--allowedTools", "Edit", "Write", "Read", "Bash"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd)
    try:
        if proc.stdin:
            proc.stdin.write(task)
            proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    state: dict = {}
    deadline = _t.monotonic() + timeout
    try:
        for raw in (proc.stdout or ()):
            if _t.monotonic() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd[0], timeout)
            raw = raw.strip()
            if not raw:
                continue
            try:
                evt = _json.loads(raw)
            except Exception:  # noqa: BLE001 — non-JSON line (rare) → skip
                continue
            for line in format_stream_event(evt, state):
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 — a slow/broken sink never kills the run
                    pass
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
    rc = proc.wait()
    if rc:
        err = (proc.stderr.read() if proc.stderr else "")[-300:]
        raise RuntimeError(f"claude coding run exited {rc}: {err or 'no output'}")


__all__ = ["COMMANDS", "SLOW", "parse", "is_slow", "run_fast", "resolve_slow_task",
           "format_stream_event", "run_claude_streamed"]
