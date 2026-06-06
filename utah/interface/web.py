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

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from utah import failures
from utah.daemon import client as ctl
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


async def api_tell(request):
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    try:
        return JSONResponse(await ctl.call("tell", {"text": text}, timeout=180.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


def _speak_answer(text: str) -> None:
    """Speak a chat answer aloud with the REAL Piper voice (same engine as voice)."""
    try:
        from utah.voice import tts

        tts.speak(text)
    except Exception as exc:  # noqa: BLE001 — never let TTS break a chat turn
        log.warning("chat TTS failed: %s", exc)


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
        "memory": (st or {}).get("memory", {}),
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
    Always 200 (the SPA throws on non-200); ``/state`` returns the whole feed,
    other routes return that domain's slice."""
    route = request.path_params["route"].strip("/").split("/")[0] or "state"
    st = await _daemon_status()
    log.info("deck GET /%s (daemon=%s)", route, "up" if st is not None else "down")
    state = _deck_state(st)
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
    return JSONResponse({})


def build_app() -> Starlette:
    routes = [
        Route("/", index),
        Route("/sim", sim),
        Route("/favicon.svg", favicon),
        Route("/status", api_status),
        Route("/memory", api_memory),
        Route("/memory/list", api_memory_list),
        Route("/memory/entities", api_memory_entities),
        Route("/api/tell", api_tell, methods=["POST"]),
        Route("/api/tell/stream", api_tell_stream),
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
