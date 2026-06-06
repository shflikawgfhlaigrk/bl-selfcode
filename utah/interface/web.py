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

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from utah.daemon import client as ctl

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


async def api_tell(request):
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    try:
        return JSONResponse(await ctl.call("tell", {"text": text}, timeout=180.0))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def api_tell_stream(request):
    """Stream a turn to the browser as SSE: one event per (channel, chunk) so the
    chat box shows the brain's reasoning live like Claude. GET ?q=<text> (EventSource
    is GET-only). Relays the daemon's ``tell_stream`` RPC; nothing fabricated."""
    text = request.query_params.get("q", "").strip()

    async def gen():
        if not text:
            yield {"event": "done", "data": ""}
            return
        try:
            async for ev in ctl.tell_stream(text):
                yield {"event": ev.get("channel", "answer"), "data": ev.get("chunk", "")}
        except Exception as exc:  # daemon unreachable etc. — honest, no fabrication
            yield {"event": "answer", "data": f"[brain unreachable: {exc}]"}
            yield {"event": "done", "data": ""}

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
        "voice": {"status": "idle", "listening": False},
    }
    for domain in DECK_LIST_DOMAINS:
        state[domain] = []
    return state


async def deck_data(request):
    """Every deck data route, served from the single live ``_deck_state`` feed.
    Always 200 (the SPA throws on non-200); ``/state`` returns the whole feed,
    other routes return that domain's slice."""
    route = request.path_params["route"].strip("/").split("/")[0] or "state"
    st = await _daemon_status()
    log.info("deck GET /%s (daemon=%s)", route, "up" if st is not None else "down")
    state = _deck_state(st)

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
