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

from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse,
                                 RedirectResponse, Response)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from utah import config, failures
from utah.daemon import client as ctl
from utah.integrations import discord as discord_mod
from utah.voice import state as voice_state

log = logging.getLogger("utah.interface.web")
DASH = pathlib.Path(__file__).resolve().parents[2] / "dashboard"
STATIC = pathlib.Path(__file__).resolve().parent / "static"
LIVE = STATIC / "live.html"
TERMINAL = STATIC / "terminal.html"
ROUTE_PAGE = STATIC / "route.html"


async def _daemon_status() -> dict | None:
    try:
        return await ctl.call("status", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        # The deck's core-up probe: swallowing this silently made "CORE OFFLINE"
        # untraceable (timeout-under-load vs daemon-dead vs bug all looked alike).
        log.warning("daemon status probe failed: %s", exc)
        return None


#: PWA head tags injected into the live deck so the tailnet deck installs to the iPhone
#: home screen as a real app icon (Michael 2026-06-10: "PWA"). Add-to-Home-Screen then
#: opens Ace full-screen, standalone, no Safari chrome.
_PWA_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest"/>'
    '<meta name="theme-color" content="#08080c"/>'
    '<meta name="apple-mobile-web-app-capable" content="yes"/>'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>'
    '<meta name="apple-mobile-web-app-title" content="Ace"/>'
    '<link rel="apple-touch-icon" href="/icon-180.png"/>'
    "<script>if('serviceWorker' in navigator)"
    "navigator.serviceWorker.register('/sw.js').catch(()=>{});</script>"
)


def _serve_deck(path):
    """Serve a deck HTML file with the PWA <head> injected exactly once (manifest, apple
    meta, service worker) so it installs to the home screen. Keeps the source files clean.
    Honest fallback: if read/decode fails, serve the file bytes unchanged."""
    try:
        html = path.read_text(encoding="utf-8")
        if "/manifest.webmanifest" not in html:
            html = html.replace("<head>", "<head>" + _PWA_HEAD, 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
        )
    except (OSError, UnicodeDecodeError) as exc:  # never let PWA injection break the deck
        log.warning("PWA injection skipped (%s) — serving %s raw", exc, path.name)
        return FileResponse(path)


async def index(request):
    """The classic server-rendered deck (live.html), preserved at /classic and /live."""
    return _serve_deck(LIVE)


async def terminal(request):
    """The standalone Ace Terminal — a dark monospace TUI into the same grounded brain
    the deck uses (POSTs /api/tell, streams /api/tell/stream). No new brain; just a shell."""
    return FileResponse(TERMINAL)


async def route_page(request):
    """Route Optimizer page — paste addresses (or pull our own probate/leads) and get the
    shortest drive order + miles saved + a Google Maps link. The two sellable products
    (Canvasser / Fleet) ride the one engine in ``utah.product.route``."""
    return FileResponse(ROUTE_PAGE)


#: Hard ceiling on stops per request — keeps geocoding (1 req/sec) and the O(n²) 2-opt
#: polish snappy, and bounds the POST as an abuse surface.
_ROUTE_MAX_STOPS = 60


async def api_route(request):
    """Optimize a set of stops. Body: ``{source, addresses|limit, config, vehicles, depot}``.
    ``source`` = ``probate`` (uses enriched lat/lng + ARV, no geocoding), ``leads`` (geocoded
    live), or ``paste`` (newline addresses, geocoded live). Returns ordered routes, totals,
    a naive-vs-optimized comparison, and per-driver Maps links. Never fabricates a coord."""
    from utah.product import route as R
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is the caller's 400, never our 500
        body = {}

    source = str(body.get("source", "paste")).lower()
    cfg_name = str(body.get("config", "canvasser")).lower()
    try:
        limit = min(_ROUTE_MAX_STOPS, int(body.get("limit") or 20))
    except (TypeError, ValueError):
        limit = 20

    geocoder = None  # default: live OSM geocode
    if source == "probate":
        stops = await run_in_threadpool(R.stops_from_probate, limit=limit)
        geocoder = lambda _addr: None  # coords already enriched; no network
    elif source == "leads":
        stops = await run_in_threadpool(R.stops_from_leads, limit=limit)
    else:
        raw = body.get("addresses") or []
        if isinstance(raw, str):
            raw = raw.splitlines()
        lines = [str(s).strip() for s in raw if str(s).strip()][:_ROUTE_MAX_STOPS]
        if (err := _oversized("\n".join(lines))):
            return err
        stops = [R.Stop(label=ln, address=ln) for ln in lines]

    if not stops:
        return JSONResponse({"error": "no stops to route"}, status_code=400)

    if cfg_name == "fleet":
        try:
            vehicles = max(2, min(int(body.get("vehicles") or 2), 8))
        except (TypeError, ValueError):
            vehicles = 2
        cfg = R.Config(name="fleet", vehicles=vehicles, round_trip=True)
    else:
        cfg = R.CANVASSER

    depot = None
    depot_addr = str(body.get("depot", "")).strip()
    if depot_addr:
        depot = R.Stop(label="depot", address=depot_addr)

    res = await run_in_threadpool(R.optimize, stops, cfg, depot=depot, geocoder=geocoder)

    routable = [i for i, s in enumerate(stops) if s.lat is not None]
    naive_miles = None
    if cfg.vehicles == 1 and len(routable) >= 2:
        pts = [(stops[i].lat, stops[i].lng) for i in routable]
        naive_miles = R.path_miles(R.distance_matrix(pts), list(range(len(pts))),
                                   round_trip=cfg.round_trip)

    routes_out = [{
        "driver": n,
        "miles": round(vr.total_miles, 1),
        "minutes": round(vr.minutes),
        "maps_url": vr.maps_url,
        "stops": [{"label": stops[i].label,
                   "arv": float(stops[i].meta["arv"]) if stops[i].meta.get("arv") is not None else None}
                  for i in vr.stops],
    } for n, vr in enumerate(res.routes, 1)]

    return JSONResponse({
        "config": cfg.name,
        "vehicles": cfg.vehicles,
        "stops_in": len(stops),
        "routable": len(routable),
        "unroutable": [stops[i].label for i in res.unroutable],
        "total_miles": round(res.total_miles, 1),
        "total_minutes": round(res.total_minutes),
        "max_driver_minutes": round(max((vr.minutes for vr in res.routes), default=0)),
        "naive_miles": round(naive_miles, 1) if naive_miles else None,
        "saved_miles": round(naive_miles - res.total_miles, 1) if naive_miles else None,
        "saved_pct": round((naive_miles - res.total_miles) / naive_miles * 100)
                     if naive_miles else None,
        "routes": routes_out,
    })


def _png_icon(size: int) -> bytes:
    """A solid Black-Gold 'A' app icon, generated (no binary asset in the repo). Pure
    PIL if present; else a tiny valid 1x1 PNG placeholder so the route never 500s."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (size, size), "#08080c")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, size - 1, size - 1], outline="#c9a961", width=max(2, size // 40))
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia.ttf",
                                      int(size * 0.62))
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        d.text((size / 2, size / 2), "A", fill="#c9a961", anchor="mm", font=font)
        import io
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — PIL absent: 1x1 gold pixel, still a valid icon
        import base64
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYPgPAAEEAQDk"
            "YgQ8AAAAAElFTkSuQmCC")


async def manifest(request):
    return JSONResponse({
        "name": "Ace — Command Deck", "short_name": "Ace",
        "start_url": "/", "display": "standalone",
        "background_color": "#08080c", "theme_color": "#08080c",
        "description": "Your AI operator — live command deck.",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
                   "purpose": "any maskable"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
                   "purpose": "any maskable"}],
    }, media_type="application/manifest+json")


async def service_worker(request):
    # Minimal SW: required for installability; network-first (the deck must show LIVE
    # data, never a stale cache), with a graceful offline note.
    sw = (
        "self.addEventListener('install',e=>self.skipWaiting());"
        "self.addEventListener('activate',e=>self.clients.claim());"
        "self.addEventListener('fetch',e=>{e.respondWith("
        "fetch(e.request).catch(()=>new Response("
        "'<h1 style=\"font-family:sans-serif;background:#08080c;color:#c9a961;"
        "padding:2rem\">Ace is offline — your Mac may be asleep.</h1>',"
        "{headers:{'Content-Type':'text/html'}})));});"
    )
    return Response(sw, media_type="application/javascript")


async def app_icon(request):
    size = {"180": 180, "192": 192, "512": 512}.get(request.path_params.get("size"), 192)
    return Response(_png_icon(size), media_type="image/png")


async def hud(request):
    """The ACE OS Black Gold Command Deck — the live operator HUD. Served same-origin so
    its per-domain fetches (/status, /memory, /daemon, /voice, /engines, /agents, /risk,
    /audit, /sync) resolve against THIS daemon's live feed — that's what turns it from the
    static design mock into the real dash. The DEFAULT deck; the prior server-rendered deck
    is preserved at /classic (and /live)."""
    return _serve_deck(DASH / "index.html")


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
    """Memory gauges; under a shed serves the last good counts labeled ``degraded``
    so the deck shows BUSY (real data, machine under load) instead of DOWN."""
    try:
        mem = await ctl.call("memory_stats", timeout=5.0)
        if mem:
            _last_good["memory"] = mem
        return JSONResponse(mem)
    except Exception as exc:
        cached = _last_good.get("memory")
        if cached:
            return JSONResponse({**cached, "degraded": str(exc)})
        return JSONResponse({"error": str(exc)}, status_code=503)


#: Hard ceiling on one memory drill-down page — the deck asks for 60; nothing
#: should be able to point the daemon at a million-row read through this route.
_MEMORY_LIST_MAX = 500


async def api_memory_list(request):
    """The actual live memory rows behind the gauge — deck drill-down (transparency).
    ``limit``/``offset`` are clamped to sane bounds before they reach the daemon."""
    try:
        limit = int(request.query_params.get("limit", "60"))
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        return JSONResponse({"error": "limit/offset must be integers"}, status_code=400)
    limit = max(1, min(limit, _MEMORY_LIST_MAX))
    offset = max(0, offset)
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


async def api_marketing_status(request):
    """Marketing channel truth for the public site and operator console."""
    from utah.integrations import social_post

    payload = await run_in_threadpool(social_post.status)
    return JSONResponse(
        payload,
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def api_panel(request):
    """Real detail behind ANY deck panel — the transparency drill-down (every panel
    clickable -> underlying truth). pool/governor/spine/leads/probate/outreach/engines/
    audit/memory/voice."""
    name = request.path_params["name"]
    try:
        return JSONResponse(await ctl.call("panel_detail", {"panel": name}, timeout=8.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


def _oversized(text: str) -> JSONResponse | None:
    """413 for input over the content cap — an unbounded POST body is a DoS/abuse
    surface, rejected before any brain/console/self-code work begins."""
    if len(text) > config.MAX_CONTENT_CHARS:
        return JSONResponse(
            {"error": f"input too large (max {config.MAX_CONTENT_CHARS} chars)"},
            status_code=413)
    return None


async def api_tell(request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is the CALLER's 400, never our 500
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    if (err := _oversized(text)):
        return err
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
    if (err := _oversized(text)):
        return err
    threading.Thread(target=_speak_answer, args=(text,), daemon=True).start()
    return JSONResponse({"speaking": True, "chars": len(text)})


async def api_speak_stop(request):
    """Stop Ace mid-sentence and arm the mic (button barge)."""
    from utah.voice.barge_control import stop_and_arm_barge

    return JSONResponse(await run_in_threadpool(stop_and_arm_barge))


async def api_voice_barge(request):
    """Alias for speak/stop — explicit barge control from the deck."""
    return await api_speak_stop(request)


# --- SELF-CODE page · chat-box "ask for an edit, he does it" -----------------
# A web-typed edit triggers a REAL governed self-code attempt (claude coding cycle +
# the suite gate) — ~minutes — so it can't run inside a request. We ENQUEUE it on a
# detached thread and the page POLLs the job. PROPOSE-ONLY by construction (run_edit
# calls propose_governed with auto_merge=False): a web-triggered edit NEVER merges to
# main. Jobs live in-process (the deck is single-owner, loopback-only); bounded count.
_EDIT_JOBS: dict[str, dict] = {}
_EDIT_JOBS_MAX = 40
_EDIT_JOBS_LOCK = threading.Lock()  # enqueue/evict is a read-modify-write across requests


def _enqueue_job(task: str, *, with_log: bool = False) -> dict:
    """Register one self-code job in the bounded in-process table and return it.

    When the table is full the oldest FINISHED jobs are evicted first; a table
    full of still-running jobs sheds the longest-running instead (the worker
    thread keeps its own dict reference, so an evicted job finishes harmlessly —
    only its poll URL goes 404). The lock serializes concurrent enqueues so two
    requests can't double-evict or race the insert."""
    job: dict = {"id": uuid.uuid4().hex[:12], "task": task, "status": "queued",
                 "started": time.time(), "result": None}
    if with_log:
        job["log"] = []
    with _EDIT_JOBS_LOCK:
        if len(_EDIT_JOBS) >= _EDIT_JOBS_MAX:
            finished = sorted((k for k, j in _EDIT_JOBS.items() if "finished" in j),
                              key=lambda k: _EDIT_JOBS[k].get("finished") or 0)
            victims = finished[:10] or sorted(
                _EDIT_JOBS, key=lambda k: _EDIT_JOBS[k].get("started", 0))[:10]
            for k in victims:
                _EDIT_JOBS.pop(k, None)
        _EDIT_JOBS[job["id"]] = job
    return job


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
    if (err := _oversized(text)):
        return err
    job = _enqueue_job(text)
    threading.Thread(target=_run_edit_job, args=(job["id"], text), daemon=True).start()
    return JSONResponse({"job": job["id"], "status": "queued", "task": text})


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
    if (err := _oversized(line)):
        return err
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
            except Exception:  # noqa: BLE001 — usage telemetry is a nicety; the edit job already ran
                pass
        job = _enqueue_job(task, with_log=True)
        threading.Thread(target=_run_console_job, args=(job["id"], task), daemon=True).start()
        return JSONResponse({"kind": "job", "job": job["id"], "status": "queued", "task": task})

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
        except Exception as exc:  # noqa: BLE001 — stream ends (client gone / daemon restart)
            log.debug("SSE stream ended: %s", exc)
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


#: route → last good payload. When the daemon sheds under load, the deck serves
#: this (labeled ``degraded``) instead of {} — stale-but-real beats blank panels
#: that read as "no producer wired" while 4,666 real rows sit in Postgres.
_last_good: dict[str, dict] = {}


async def _ledger_snapshot() -> dict:
    """Live product-ledger snapshot (revenue domains) — real-or-empty, never fabricated.
    On shed/error: last good snapshot with a ``degraded`` reason, never silent {}."""
    try:
        snap = await ctl.call("ledger_snapshot", {"limit": 8}, timeout=5.0)
    except Exception as exc:
        cached = _last_good.get("ledger")
        log.warning(
            "ledger_snapshot unavailable (%s)%s",
            exc,
            " — serving last good snapshot" if cached else "",
        )
        return {**cached, "degraded": str(exc)} if cached else {}
    if snap:
        _last_good["ledger"] = snap
    return snap


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
            _last_good["memory"] = mem
    except Exception as exc:
        cached = _last_good.get("memory")
        log.warning(
            "memory_stats unavailable (%s)%s",
            exc,
            " — serving last good counts" if cached else "",
        )
        if cached:
            state["memory"] = cached
            state.setdefault("degraded", {})["memory"] = str(exc)
    # AUDIT LEDGER panel — live from the durable failure log (off-loop; empty on error)
    state["audit"] = await run_in_threadpool(_audit_rows)
    # Revenue panels — live from the Postgres product ledger (real rows or empty).
    snap = await _ledger_snapshot()
    if snap:
        if snap.get("degraded"):
            state.setdefault("degraded", {})["ledger"] = snap["degraded"]
        state["leads"] = snap.get("leads", [])
        state["probate"] = snap.get("probate", [])
        state["outreach"] = snap.get("outreach", [])
        state["engines"] = snap.get("fires", [])      # 'trading'/'engines' panel = fires
        state["ledger"] = snap.get("counts", {})
        state["lab"] = snap.get("lab", {})            # WC feed gate truth for the lab card

    if route in ("status", "state"):
        return JSONResponse(state)
    if route == "health":
        return JSONResponse({"status": state["health"], "ok": st is not None})
    if route in state:
        return JSONResponse(state[route])
    return JSONResponse(
        {"error": f"unknown route /{route}", "known": sorted(state.keys())}, status_code=404
    )


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class OriginGuard(BaseHTTPMiddleware):
    """CSRF defense for the deck's state-changing endpoints. The deck POSTs to
    self-code, console, voice and tell — a drive-by page in the user's browser
    could otherwise POST to ``http://127.0.0.1:8766/api/selfcode/edit`` and drive
    those. Browsers ALWAYS attach an ``Origin`` header on a cross-origin POST, so:
    a request whose ``Origin`` host doesn't match its ``Host`` is rejected 403.
    Native callers (curl, the daemon, a tailnet tap) send no ``Origin`` and pass —
    this is the standard token-less CSRF guard, not auth."""

    async def dispatch(self, request, call_next):
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            # Absent Origin = native caller (curl/daemon/tailnet tap) — allowed.
            # A PRESENT Origin must parse to a host that matches Host; anything else
            # (cross-origin, empty/unparseable "http://", "null") is refused — a
            # present-but-unclear Origin is treated as hostile, not waved through.
            if origin is not None:
                # Hostnames are case-insensitive (RFC 3986/7230) — normalize both
                # sides so a mixed-case Origin neither bypasses the guard nor
                # false-rejects a legitimate same-origin request.
                origin_host = urlsplit(origin).netloc.split("@")[-1].lower()
                host = request.headers.get("host", "").lower()
                matches = bool(origin_host) and (
                    origin_host == host or origin_host.split(":")[0] == host.split(":")[0])
                if not matches:
                    return JSONResponse(
                        {"error": f"cross-origin request refused (Origin {origin!r})"},
                        status_code=403)
        return await call_next(request)


async def api_truth(request):
    """The Proof Ledger scoreboard: every claim's live tier, grouped by department."""
    from utah import proof
    with proof._pool().connection() as c:
        rows = c.execute(
            "SELECT id,claim,system,artifact,proof_kind,last_result,last_run,owner,how,link "
            "FROM proof_ledger ORDER BY system,id").fetchall()
    by_system, score = {}, {"total": 0, "proven": 0, "promoted": 0, "red": 0}
    for rid, claim, system, artifact, kind, lr, lrun, owner, how, link in rows:
        tier = proof.effective_tier(rid)
        score["total"] += 1
        score[tier] = score.get(tier, 0) + 1
        if tier not in proof.GREEN_TIERS:
            score["red"] += 1
        by_system.setdefault(system, []).append(
            {"id": rid, "claim": claim, "tier": tier, "artifact": artifact,
             "kind": kind, "owner": owner, "how": how, "link": link, "last_result": lr,
             "last_run": lrun.isoformat() if lrun else None})
    return JSONResponse({"scoreboard": score, "by_system": by_system})


async def api_truth_detail(request):
    """Drill-down: the proof command, the captured output, and the LIVE source file."""
    from utah import proof
    pid = request.path_params["pid"]
    with proof._pool().connection() as c:
        row = c.execute(
            "SELECT claim,system,artifact,proof_kind,proof_cmd,stress_cmd,last_result,"
            "last_output,owner FROM proof_ledger WHERE id=%s", (pid,)).fetchone()
    if not row:
        return JSONResponse({"error": "unknown proof id"}, status_code=404)
    claim, system, artifact, kind, cmd, stress, lr, out, owner = row
    fpath = (artifact or "").split(":", 1)[0]
    src = ""
    if fpath:
        base = pathlib.Path(__file__).resolve().parents[2]
        p = (pathlib.Path(fpath).expanduser() if fpath.startswith(("/", "~")) else base / fpath)
        try:
            src = p.read_text()[:20000] if p.exists() else f"(artifact not found on disk: {fpath})"
        except Exception as exc:  # noqa: BLE001
            src = f"(unreadable: {exc})"
    return JSONResponse({"id": pid, "claim": claim, "system": system, "owner": owner,
                         "tier": proof.effective_tier(pid), "artifact": artifact,
                         "proof_kind": kind, "proof_cmd": cmd, "stress_cmd": stress,
                         "last_result": lr, "last_output": out, "source": src})


async def truth_page(request):
    return FileResponse(STATIC / "truth.html")


def build_app() -> Starlette:
    routes = [
        Route("/", index),                # THE REAL DECK: live.html — actively maintained, full live
                                          # data (failures feed, PIPELINE, SYSTEM MAP, panels). The
                                          # Jun-6 React SPA shell lacks all of this; it's at /hud.
        Route("/hud", hud),               # the Jun-6 React SPA design shell (stale build, reference only)
        Route("/deck", index),            # the real deck
        Route("/sim", hud),               # legacy alias (was the static "design sim")
        Route("/classic", index),         # live.html (same as /)
        Route("/live", index),
        Route("/terminal", terminal),
        Route("/terminal.html", terminal),
        Route("/route", route_page),
        Route("/api/route", api_route, methods=["POST"]),
        Route("/favicon.svg", favicon),
        Route("/discord", discord_redirect),
        Route("/api/discord", api_discord),
        Route("/api/marketing/status", api_marketing_status),
        Route("/status", api_status),
        Route("/memory", api_memory),
        Route("/memory/list", api_memory_list),
        Route("/memory/entities", api_memory_entities),
        Route("/panel/{name}", api_panel),
        Route("/api/tell", api_tell, methods=["POST"]),
        Route("/api/tell/stream", api_tell_stream),
        Route("/api/speak", api_speak, methods=["POST"]),
        Route("/api/speak/stop", api_speak_stop, methods=["POST"]),
        Route("/api/voice/barge", api_voice_barge, methods=["POST"]),
        Route("/api/selfcode/edit", api_selfcode_edit, methods=["POST"]),
        Route("/api/selfcode/job/{job}", api_selfcode_job),
        Route("/api/console", api_console, methods=["POST"]),
        Route("/events", events),
        Route("/manifest.webmanifest", manifest),
        Route("/sw.js", service_worker),
        Route("/icon-{size}.png", app_icon),
        Mount("/assets", StaticFiles(directory=str(DASH / "assets"))),
        Route("/truth", truth_page),         # Proof Ledger — the nervous-system truth page
        Route("/api/truth", api_truth),
        Route("/api/truth/{pid:path}", api_truth_detail),
        Route("/{route:path}", deck_data),  # catch-all data routes (last)
    ]
    return Starlette(routes=routes, middleware=[Middleware(OriginGuard)])


app = build_app()


def main(host: str = "127.0.0.1", port: int = 8766) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
