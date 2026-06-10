"""Web bridge — serves the Black Gold deck and feeds it live spine state.

The deck (a React SPA) queries ``GET /<route>`` for ``state``/``health``/
``status``/``memory``/``engines``/``agents``/``risk``/``voice``/``leads``/
``events``/``tick``/``consolidate`` and throws on any non-200. So the bridge:
serves the deck, answers every data route 200 with LIVE data where a producer
exists (the spine: daemon status, pool, governor, bus) and an honest empty shape
where a program has not landed yet, and streams the bus over ``/events`` (SSE).
Backend produces → surface reflects; nothing faked, nothing crashes.
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
import uuid

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from utah import config, failures
from utah.daemon import client as ctl
from utah.integrations import discord as discord_mod
from utah.voice import state as voice_state

log = logging.getLogger("utah.interface.web")
DASH = pathlib.Path(__file__).resolve().parents[2] / "dashboard"
LIVE = pathlib.Path(__file__).resolve().parent / "static" / "live.html"


async def _daemon_status() -> dict | None:
    try:
        return await ctl.call("status", timeout=5.0)
    except Exception:
        return None


async def index(request):
    return FileResponse(LIVE)  # honest deck: real data, dormant where no producer, never simulated


async def sim(request):
    return FileResponse(DASH / "index.html")  # Black Gold design simulation (reference only)


async def favicon(request):
    return FileResponse(DASH / "favicon.svg")


def _discord_invite() -> str:
    """The configured invite link — config env wins, else the secrets file."""
    return (config.DISCORD_INVITE_URL or discord_mod.invite_url() or "").strip()


async def discord_redirect(request):
    """Stable ``/discord`` link → the live invite (so the URL on the deck never rots)."""
    url = _discord_invite()
    if url:
        return RedirectResponse(url)
    return JSONResponse({"error": "no invite configured",
                         "hint": "set invite_url in ~/.utah/secrets/discord.json"}, status_code=404)


async def api_discord(request):
    """Deck panel feed: is the server wired, the invite link, and the mirrored shape."""
    try:
        plan = await run_in_threadpool(discord_mod.provision, dry_run=True)
    except Exception:  # noqa: BLE001 — panel must never crash the deck
        plan = {"totals": {}}
    return JSONResponse({
        "invite": _discord_invite(),
        "available": discord_mod.available(),
        "totals": plan.get("totals", {}),
    })


async def api_status(request):
    try:
        return JSONResponse(await ctl.call("status", timeout=5.0))  # flat live daemon status
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_memory(request):
    try:
        return JSONResponse(await ctl.call("memory_stats", timeout=5.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_memory_list(request):
    """The actual live memory rows behind the gauge — deck drill-down (transparency)."""
    try:
        limit = int(request.query_params.get("limit", "60"))
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        return JSONResponse({"error": "limit/offset must be integers"}, status_code=400)
    try:
        return JSONResponse(await ctl.call("memory_list", {"limit": limit, "offset": offset}, timeout=8.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_memory_entities(request):
    """The actual entities behind the gauge — deck drill-down (transparency)."""
    try:
        return JSONResponse(await ctl.call("memory_entities", {"limit": 300}, timeout=8.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_panel(request):
    """Real detail behind ANY deck panel — the transparency drill-down (every panel
    clickable -> underlying truth). pool/governor/spine/leads/probate/outreach/engines/
    audit/memory/voice."""
    name = request.path_params["name"]
    try:
        return JSONResponse(await ctl.call("panel_detail", {"panel": name}, timeout=8.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_tell(request):
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    try:
        result = await ctl.call("tell", {"text": text}, timeout=180.0)
        # Path-B parity: the non-streaming reply must SPEAK the answer too (the
        # streaming /api/tell/stream path does — without this, a chat turn over
        # /api/tell came back silent). Detached so the JSON returns at once.
        answer = (result.get("text") or "").strip() if isinstance(result, dict) else ""
        if answer:
            threading.Thread(target=_speak_answer, args=(answer,), daemon=True).start()
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


def _speak_answer(text: str) -> None:
    """Speak a chat answer aloud with the REAL Piper voice (same engine as voice)."""
    try:
        from utah.voice import tts

        tts.speak(text)
    except Exception as exc:  # noqa: BLE001 — never let TTS break a chat turn
        log.warning("chat TTS failed: %s", exc)


async def api_speak(request):
    """Speak arbitrary text aloud with the REAL Piper voice (the chat box re-speak
    button). Detached thread so the response returns at once; never fabricated audio."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    threading.Thread(target=_speak_answer, args=(text,), daemon=True).start()
    return JSONResponse({"speaking": True, "chars": len(text)})


# --- SELF-CODE page · chat-box "ask for an edit, he does it" -----------------
# A web-typed edit triggers a REAL governed self-code attempt (claude coding cycle +
# the suite gate) — ~minutes — so it can't run inside a request. We ENQUEUE it on a
# detached thread and the page POLLs the job. PROPOSE-ONLY by construction (run_edit
# calls propose_governed with auto_merge=False): a web-triggered edit NEVER merges to
# main. Jobs live in-process (the deck is single-owner, loopback-only); bounded count.
_EDIT_JOBS: dict[str, dict] = {}
_EDIT_JOBS_MAX = 40


def _run_edit_job(job_id: str, text: str) -> None:
    from utah.product import selfcode_web

    job = _EDIT_JOBS.get(job_id)
    if job is None:
        return
    job["status"] = "working"
    try:
        result = selfcode_web.run_edit(text)             # the real blocking governed run
        job["result"] = result
        job["status"] = "error" if result.get("ran") is False else "done"
    except Exception as exc:  # noqa: BLE001 — never crash the worker thread
        failures.record("selfcode", "web_edit_failed", f"{text[:60]}: {exc}")
        job["result"] = {"ran": False, "error": str(exc)[:300], "task": text}
        job["status"] = "error"
    finally:
        job["finished"] = time.time()


def _run_console_job(job_id: str, text: str) -> None:
    """Console /code: the governed propose-only edit, but STREAM the agent's live actions
    into ``job['log']`` (assistant text + every bash/edit/read) so the deck console shows
    Ace working in real time. Propose-only — never merges main."""
    from utah.product import console as con
    from utah.product import selfcode_web

    job = _EDIT_JOBS.get(job_id)
    if job is None:
        return
    job["status"] = "working"
    job.setdefault("log", [])

    def _sink(line: str) -> None:
        log = job.get("log")
        if log is not None:
            log.append(line)
            if len(log) > 800:
                del log[:-800]

    try:
        # /effort mode shapes the job: budget (timeout) + a rigor instruction suffix.
        result = selfcode_web.run_edit(
            text + con.effort_suffix(),
            run_claude=lambda t: con.run_claude_streamed(
                t, cwd=str(selfcode_web.REPO_DIR), on_line=_sink,
                timeout=con.effort_budget()),
        )
        job["result"] = result
        job["status"] = "error" if result.get("ran") is False else "done"
    except Exception as exc:  # noqa: BLE001 — never crash the worker thread
        failures.record("selfcode", "console_code_failed", f"{text[:60]}: {exc}")
        job["result"] = {"ran": False, "error": str(exc)[:300], "task": text}
        job["status"] = "error"
    finally:
        job["finished"] = time.time()


async def api_selfcode_edit(request):
    """Enqueue a web-typed edit as a REAL governed (propose-only) self-code attempt.
    Returns a job id at once; the page polls ``/api/selfcode/job/<id>`` for the result.
    The run is slow (minutes) and PROPOSE-ONLY — auto_merge=False, never merges main."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty edit request"}, status_code=400)
    # bound the job table: drop the oldest finished jobs first
    if len(_EDIT_JOBS) >= _EDIT_JOBS_MAX:
        for k in sorted(_EDIT_JOBS, key=lambda j: _EDIT_JOBS[j].get("finished", 0))[:10]:
            _EDIT_JOBS.pop(k, None)
    job_id = uuid.uuid4().hex[:12]
    _EDIT_JOBS[job_id] = {"id": job_id, "task": text, "status": "queued",
                          "started": time.time(), "result": None}
    threading.Thread(target=_run_edit_job, args=(job_id, text), daemon=True).start()
    return JSONResponse({"job": job_id, "status": "queued", "task": text})


async def api_selfcode_job(request):
    """Poll a self-code edit job. ``status`` ∈ queued|working|done|error; ``result`` is
    the shaped propose_governed outcome (task/passed/branch/tier/utility/diff) once done."""
    job = _EDIT_JOBS.get(request.path_params["job"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    elapsed = round(time.time() - job.get("started", time.time()), 1)
    return JSONResponse({**job, "elapsed_s": elapsed})


async def api_console(request):
    """The SELF-CODE CONSOLE — one slash-command line in, terminal output out.

    FAST commands (``/help`` ``/status`` ``/findings`` ``/cycles`` ``/goals`` ``/diff``)
    answer inline; the SLOW coding commands (``/code`` ``/do``) enqueue a REAL governed
    PROPOSE-ONLY self-code job (poll ``/api/selfcode/job/<id>``) on the isolated clone — a
    console edit never merges to main. A bare line or ``/ask`` relays to the grounded brain.
    Nothing fabricated; honest on any failure.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    line = str(body.get("line", body.get("text", ""))).strip()
    from utah.product import console as con

    cmd, arg = con.parse(line)
    if not cmd:
        return JSONResponse({"kind": "instant", "output": ""})

    # /ask (and a bare line) → the grounded brain, inline (no-fab; persist=False so a
    # console probe never pollutes recall).
    if cmd == "ask":
        if not arg:
            return JSONResponse({"kind": "instant",
                                 "output": "usage: /ask <question>  (or just type a line)"})
        try:
            reply = await ctl.call("tell", {"text": arg, "persist": False}, timeout=180.0)
            txt = reply.get("text", "") if isinstance(reply, dict) else str(reply)
            src = reply.get("source", "") if isinstance(reply, dict) else ""
            return JSONResponse({"kind": "instant", "output": txt, "source": src})
        except Exception as exc:  # noqa: BLE001 — honest, never fabricate
            return JSONResponse({"kind": "instant", "output": f"[brain unreachable: {exc}]"})

    # /code, /do → a real propose-only governed coding cycle (minutes) on the job runner.
    if con.is_slow(cmd):
        task, rec = con.resolve_slow_task(cmd, arg)
        if not task:
            hint = "usage: /code <task>" if cmd == "code" else "usage: /do <n>   (see /findings)"
            return JSONResponse({"kind": "instant", "output": hint})
        if rec is not None:  # consume the finding so it doesn't re-list
            try:
                from utah import sica_discover

                sica_discover.mark_used(rec)
            except Exception:  # noqa: BLE001
                pass
        if len(_EDIT_JOBS) >= _EDIT_JOBS_MAX:
            for k in sorted(_EDIT_JOBS, key=lambda j: _EDIT_JOBS[j].get("finished", 0))[:10]:
                _EDIT_JOBS.pop(k, None)
        job_id = uuid.uuid4().hex[:12]
        _EDIT_JOBS[job_id] = {"id": job_id, "task": task, "status": "queued",
                              "started": time.time(), "result": None, "log": []}
        threading.Thread(target=_run_console_job, args=(job_id, task), daemon=True).start()
        return JSONResponse({"kind": "job", "job": job_id, "status": "queued", "task": task})

    # FAST commands → inline terminal text.
    try:
        out = con.run_fast(cmd, arg)
    except Exception as exc:  # noqa: BLE001
        out = {"output": f"[console error: {exc}]"}
    return JSONResponse({"kind": "instant", **out})


async def api_tell_stream(request):
    """Stream a turn to the browser as SSE: one event per (channel, chunk) so the
    chat box shows the brain's reasoning live like Claude. GET ?q=<text> (EventSource
    is GET-only). Relays the daemon's ``tell_stream`` RPC; nothing fabricated. Every
    chat answer is ALSO spoken (real Piper) on a detached thread — all paths reply by voice."""
    text = request.query_params.get("q", "").strip()

    async def gen():
        if not text:
            yield {"event": "done", "data": ""}
            return
        parts: list[str] = []
        try:
            async for ev in ctl.tell_stream(text):
                channel = ev.get("channel", "answer")
                chunk = ev.get("chunk", "")
                if channel == "answer":
                    parts.append(chunk)
                yield {"event": channel, "data": chunk}
        except Exception as exc:  # daemon unreachable etc. — honest, logged, no fabrication
            failures.record("chat", "stream_failed", f"{text[:60]}: {exc}")
            yield {"event": "answer", "data": f"[brain unreachable: {exc}]"}
            yield {"event": "done", "data": ""}
            return
        answer = "".join(parts).strip()
        if answer:  # speak it (detached so a browser close can't cut it off)
            threading.Thread(target=_speak_answer, args=(answer,), daemon=True).start()

    return EventSourceResponse(gen())


async def events(request):
    async def gen():
        try:
            async for ev in ctl.subscribe():
                if await request.is_disconnected():
                    break
                yield {"event": ev["channel"], "data": json.dumps(ev["event"])}
        except Exception:
            return

    return EventSourceResponse(gen())


# Panel domains carried as feed lists (rows). DORMANT = honest-empty [] until a
# producer publishes; a producer lighting one of these needs NO frontend change.
DECK_LIST_DOMAINS = (
    "engines", "agents", "leads", "probate", "outreach", "audit", "tick", "sync", "events",
)


def _deck_state(st: dict | None) -> dict:
    """The ONE live deck feed covering every panel domain.

    Real where a producer exists (spine/daemon/memory come straight from the live
    daemon status); honest-empty everywhere else (real-or-DORMANT, never faked).
    """
    state: dict = {
        "health": "live" if st is not None else "down",
        "spine": st or {},
        "daemon": st or {},
        "memory": (st or {}).get("memory", {}),  # contract key; real counts via /memory
        "risk": {},
        "voice": voice_state.status(),  # REAL live voice-loop state (or 'down'), never faked
    }
    for domain in DECK_LIST_DOMAINS:
        state[domain] = []
    return state


def _audit_rows() -> list[str]:
    """Recent failures formatted for the AUDIT LEDGER panel (real-or-empty)."""
    return [
        f"{r.ts} {r.source}/{r.kind}: {r.detail}"[:140]
        for r in failures.recent(20)
    ]


async def _ledger_snapshot() -> dict:
    """Live product-ledger snapshot (revenue domains) — real-or-empty, never fabricated."""
    try:
        return await ctl.call("ledger_snapshot", {"limit": 8}, timeout=5.0)
    except Exception:
        return {}


async def deck_data(request):
    """Every deck data route, served from the single live ``_deck_state`` feed.
    Known routes are always 200 (the SPA throws on non-200); ``/state`` returns
    the whole feed, other routes return that domain's slice. UNKNOWN routes are
    404 — the old 200+``{}`` made external monitors (and one whole audit)
    conclude the deck was data-dead when it was healthy."""
    parts = request.path_params["route"].strip("/").split("/")
    # Alias /api/<x> → /<x>: both spellings serve the same feed.
    if parts and parts[0] == "api":
        parts = parts[1:]
    route = parts[0] if parts and parts[0] else "state"
    st = await _daemon_status()
    log.info("deck GET /%s (daemon=%s)", route, "up" if st is not None else "down")
    state = _deck_state(st)
    # Memory counts — daemon ``status`` is spine-only; pull real counts for /state.
    try:
        mem = await ctl.call("memory_stats", timeout=5.0)
        if mem:
            state["memory"] = mem
    except Exception:
        pass
    # AUDIT LEDGER panel — live from the durable failure log (off-loop; empty on error)
    state["audit"] = await run_in_threadpool(_audit_rows)
    # Revenue panels — live from the Postgres product ledger (real rows or empty).
    snap = await _ledger_snapshot()
    if snap:
        state["leads"] = snap.get("leads", [])
        state["probate"] = snap.get("probate", [])
        state["outreach"] = snap.get("outreach", [])
        state["engines"] = snap.get("fires", [])      # 'trading'/'engines' panel = fires
        state["ledger"] = snap.get("counts", {})

    if route in ("status", "state"):
        return JSONResponse(state)
    if route == "health":
        return JSONResponse({"status": state["health"], "ok": st is not None})
    if route in state:
        return JSONResponse(state[route])
    return JSONResponse(
        {"error": f"unknown route /{route}", "known": sorted(state.keys())}, status_code=404
    )


def build_app() -> Starlette:
    routes = [
        Route("/", index),
        Route("/sim", sim),
        Route("/favicon.svg", favicon),
        Route("/discord", discord_redirect),
        Route("/api/discord", api_discord),
        Route("/status", api_status),
        Route("/memory", api_memory),
        Route("/memory/list", api_memory_list),
        Route("/memory/entities", api_memory_entities),
        Route("/panel/{name}", api_panel),
        Route("/api/tell", api_tell, methods=["POST"]),
        Route("/api/tell/stream", api_tell_stream),
        Route("/api/speak", api_speak, methods=["POST"]),
        Route("/api/selfcode/edit", api_selfcode_edit, methods=["POST"]),
        Route("/api/selfcode/job/{job}", api_selfcode_job),
        Route("/api/console", api_console, methods=["POST"]),
        Route("/events", events),
        Mount("/assets", StaticFiles(directory=str(DASH / "assets"))),
        Route("/{route:path}", deck_data),  # catch-all data routes (last)
    ]
    return Starlette(routes=routes)


app = build_app()


def main(host: str = "127.0.0.1", port: int = 8766) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
