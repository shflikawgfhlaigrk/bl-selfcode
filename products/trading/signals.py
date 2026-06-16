"""Signals subscriber delivery (SITE-12) — the product's actual deliverable.

Fires land in Postgres (``ledger.record_fire``) and page Michael
(``alerts.trade_fire``), but until this lane existed NO subscriber ever received an
alert — the Signals product had a track record and no delivery. This module fans each
REAL fire out to the ``signals_subscribers`` table: one HONEST email per active
subscriber via :func:`utah.mail.send` (inherits its no-creds gate) plus ONE Discord
post via the existing webhook transport (:mod:`utah.integrations.discord_feed` →
``discord.post`` — no new HTTP client). The message carries only ledger facts:
engine, symbol, direction, entry, stop/target when present, timestamps, and — when
the grader has filled it — the graded outcome verbatim. Performance is NEVER invented
(the 2026-06-11 storefront fabrication is exactly what this lane must not repeat).

Doctrine (matches :mod:`utah.alerts`):

* **Never raises** — every entrypoint returns an honest dict; the ``record_fire``
  hook can never break a ledger write.
* **Honest gates** — ``UTAH_SIGNALS_DELIVERY=0``, an empty subscriber table, or an
  unreachable store all return ``{"sent": 0, "gated": True, "reason": ...}`` without
  faking a delivery. Gates do NOT burn the dedup key, so the first real subscriber
  still receives fires recorded after sign-up.
* **File-backed dedup per fire id** (``~/.utah/run/signals_seen.json``) — the
  2026-06-10 page-storm lesson: process-memory dedup re-delivered every live signal
  on every restart. A fire delivers at most once per STATE (the ungraded signal
  alert, then the graded result), never twice.
* **Bounded I/O** — reads ride the shared :mod:`utah.db_pool`
  (``connect_timeout=config.DB_CONNECT_TIMEOUT``, checkout capped at
  ``DB_POOL_TIMEOUT``) with LIMIT-bounded statements, the :mod:`utah.product.tasks`
  pattern.
* **Injectable seams** — ``mail_send`` / ``discord_post`` / ``subscribers_fn`` per
  call, plus :func:`set_transports` (the conftest pin) and :func:`set_seen_path`, so
  the whole suite runs with zero network and zero real sends.

Live wiring: ``ledger.record_fire`` calls :func:`deliver_fire_async` for every real
fire (non-blocking thread, the ``alerts.critical_async`` pattern); the cron lane
(``ops/launchd/com.utah.signals.plist``) runs ``python -m utah.product.signals
deliver-latest`` to pick up the newly-graded state of the most recent fire.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time as _time
from datetime import datetime, timezone

import psycopg

from utah import UtahError, config

log = logging.getLogger("utah.product.signals")


class SignalsError(UtahError):
    """The signals store could not be reached or a statement failed."""


_DDL = """
CREATE TABLE IF NOT EXISTS signals_subscribers (
  id bigserial PRIMARY KEY,
  email text NOT NULL,
  status text NOT NULL DEFAULT 'active',      -- active | paused
  ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (email)                              -- never the same subscriber twice
);
"""


@contextlib.contextmanager
def _conn():
    """An autocommit connection from the shared per-DSN pool (:mod:`utah.db_pool`) —
    every checkout is BOUNDED (``connect_timeout=DB_CONNECT_TIMEOUT``, checkout capped
    at ``DB_POOL_TIMEOUT``), the same pattern as :mod:`utah.product.tasks`. Any psycopg
    failure surfaces as :class:`SignalsError`."""
    from utah import db_pool

    try:
        with db_pool.get_pool(config.DB_DSN).connection() as c:
            yield c
    except psycopg.Error as exc:
        raise SignalsError(f"signals store unreachable: {exc}") from exc


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def subscribers(status: str = "active", limit: int = 500) -> list[dict]:
    """Subscriber rows with *status*, bounded (LIMIT + the pool's timeouts). A missing
    table on first read is created and returns the honest empty list (the lane simply
    has no subscribers yet); a real outage raises :class:`SignalsError`, which
    :func:`deliver_fire` converts into its own honest gate — a DB defect must never
    masquerade as "no subscribers"."""
    limit = max(1, min(int(limit), 1000))
    with _conn() as c:
        try:
            rows = c.execute(
                "SELECT id, email, status, to_char(ts,'YYYY-MM-DD HH24:MI') "
                "FROM signals_subscribers WHERE status=%s ORDER BY id LIMIT %s",
                (status, limit),
            ).fetchall()
        except psycopg.errors.UndefinedTable:
            c.execute(_DDL)                     # first touch: create, honest empty
            rows = []
    return [{"id": int(r[0]), "email": r[1], "status": r[2], "ts": r[3]} for r in rows]


def subscribe(email: str) -> bool:
    """Add (or reactivate) a subscriber; True if NEW (False = already on the list).
    UNIQUE(email) — the ledger's never-twice convention."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise SignalsError(f"invalid subscriber email: {email!r}")
    init_schema()
    with _conn() as c:
        row = c.execute(
            "INSERT INTO signals_subscribers (email) VALUES (%s) "
            "ON CONFLICT (email) DO NOTHING RETURNING id", (email,),
        ).fetchone()
        if row is None:                          # existing row: re-activate, not duplicate
            c.execute("UPDATE signals_subscribers SET status='active' WHERE email=%s",
                      (email,))
    return bool(row)


def set_status(email: str, status: str) -> bool:
    """Flip a subscriber active|paused. True if a row changed."""
    if status not in ("active", "paused"):
        raise SignalsError(f"status must be active|paused, got {status!r}")
    with _conn() as c:
        cur = c.execute(
            "UPDATE signals_subscribers SET status=%s WHERE email=%s",
            (status, (email or "").strip().lower()),
        )
        return (cur.rowcount or 0) > 0          # read INSIDE the checkout (pooled reset)


# --- file-backed dedup (the alerts.py 2026-06-10 page-storm pattern) -------------

_SEEN: dict[str, float] = {}    # delivered: dedup key -> delivery epoch seconds
_SEEN_LOADED = False            # lazy one-time load of the persisted state
_LOCK = threading.Lock()
_SEEN_PATH = None               # injectable override (tests isolate to a tmp file)
_SEEN_RETENTION_S = 30 * 86400  # fire ids never repeat; 30d covers any re-grade window


def set_seen_path(p) -> None:
    """Point the durable dedup file somewhere else (tests). ``None`` = the live path."""
    global _SEEN_PATH
    _SEEN_PATH = p


def _seen_path():
    return _SEEN_PATH or (config.UTAH_HOME / "run" / "signals_seen.json")


def _load_seen_locked() -> None:
    """One-time lazy load of the persisted dedup map. Held under ``_LOCK``."""
    global _SEEN_LOADED
    if _SEEN_LOADED:
        return
    _SEEN_LOADED = True
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            disk = json.load(f)
        now = _time.time()
        _SEEN.update({k: float(v) for k, v in disk.items()
                      if isinstance(v, (int, float)) and now - float(v) < _SEEN_RETENTION_S})
    except Exception:  # noqa: BLE001 — no file / corrupt file = fresh start
        pass


def _save_seen_locked() -> None:
    """Write-behind persistence (atomic rename). Held under ``_LOCK``."""
    try:
        p = _seen_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_SEEN, f)
        tmp.replace(p)
    except Exception:  # noqa: BLE001 — persistence is best-effort, never blocks a send
        pass


def _reset_seen_for_tests() -> None:
    """Drop in-memory + loaded state so each test starts from its own tmp file."""
    global _SEEN_LOADED
    with _LOCK:
        _SEEN.clear()
        _SEEN_LOADED = False


def _already_delivered(key: str) -> bool:
    with _LOCK:
        _load_seen_locked()
        return key in _SEEN


def _mark_delivered(key: str) -> None:
    with _LOCK:
        _load_seen_locked()
        _SEEN[key] = _time.time()
        _save_seen_locked()


def _fire_id(fire) -> int | None:
    """The fire's ledger id, or None when the dict is malformed (no dedup key = no
    safe delivery — gated, never guessed)."""
    try:
        fid = int(fire.get("id"))
        return fid if fid > 0 else None
    except Exception:  # noqa: BLE001 — None/str/garbage all mean "not deliverable"
        return None


def _dedup_key(fid: int, fire: dict) -> str:
    """One delivery per fire STATE: the ungraded signal alert and the graded result
    each deliver exactly once — "newly-graded" is a new state, a re-run is not."""
    graded = isinstance(fire, dict) and fire.get("outcome") is not None
    return f"fire:{fid}:graded" if graded else f"fire:{fid}"


# --- the honest message ------------------------------------------------------------

def compose(fire: dict) -> tuple[str, str]:
    """``(subject, body)`` from ledger facts ONLY. A graded outcome/pnl is included
    verbatim when present; an ungraded fire says so explicitly. No win rates, no
    projections, no invented performance — ever."""
    fire = fire if isinstance(fire, dict) else {}
    eng = str(fire.get("engine") or "?")
    direction = str(fire.get("direction") or "?").upper()
    sym = str(fire.get("symbol") or "").strip()
    graded = fire.get("outcome") is not None
    head = f"{eng} {direction}" + (f" {sym}" if sym else "")
    subject = (f"Signals result — {head}: {fire.get('outcome')}" if graded
               else f"Signals fire — {head} @ {fire.get('entry')}")
    if fire.get("id") is not None:
        subject += f" (fire #{fire['id']})"
    lines = [f"Engine: {eng}", f"Direction: {direction}"]
    if sym:
        lines.append(f"Symbol: {sym}")
    for label, k in (("Entry", "entry"), ("Stop", "stop"), ("Target", "target")):
        if fire.get(k) is not None:
            lines.append(f"{label}: {fire[k]}")
    if fire.get("rationale"):
        lines.append(f"Rationale: {fire['rationale']}")
    if fire.get("ts"):
        lines.append(f"Fired: {fire['ts']}")
    if graded:
        out = f"Graded outcome: {fire['outcome']}"
        pnl = fire.get("pnl")
        if pnl is not None:
            try:
                out += f" ({float(pnl):+g} pts)"
            except (TypeError, ValueError):
                out += f" (pnl {pnl})"
        lines.append(out)
    else:
        lines.append("Outcome: not yet graded — the grader fills outcome/pnl after the "
                     "trade closes; no result is implied until then.")
    lines.append(f"Delivered: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    return subject, "\n".join(lines)


# --- transports (injectable; conftest pins fakes suite-wide) ------------------------

_MAIL_SEND = None       # (to, subject, body) -> mail.send-shaped dict
_DISCORD_POST = None    # (title, content) -> bool | {"posted": ...}
_SUBSCRIBERS_FN = None  # () -> list[dict]


def set_transports(*, mail_send=None, discord_post=None, subscribers_fn=None) -> None:
    """Pin the default transports (tests); all-``None`` restores the real ones."""
    global _MAIL_SEND, _DISCORD_POST, _SUBSCRIBERS_FN
    _MAIL_SEND, _DISCORD_POST, _SUBSCRIBERS_FN = mail_send, discord_post, subscribers_fn


def _real_mail_send(to: str, subject: str, body: str) -> dict:
    from utah import mail

    return mail.send(to, subject, body)         # inherits mail's honest no-creds gate


def _real_discord_post(title: str, content: str) -> dict:
    """ONE post to the fires channel via the EXISTING webhook transport
    (``discord_feed.publish`` → ``discord.post`` — its session/User-Agent handling and
    failure logging come along; no new HTTP client)."""
    from utah.integrations import discord_feed

    if not discord_feed.available("fires"):
        return {"posted": False, "gated": True, "reason": "no fires webhook"}
    return {"posted": bool(discord_feed.publish("fires", content, title=title)),
            "gated": False}


def _normalize_discord(res) -> dict:
    if isinstance(res, dict):
        out = {"posted": bool(res.get("posted"))}
        out.update({k: res[k] for k in ("gated", "reason", "error") if k in res})
        return out
    return {"posted": bool(res)}


# --- delivery -----------------------------------------------------------------------

def deliver_fire(fire: dict, *, mail_send=None, discord_post=None,
                 subscribers_fn=None) -> dict:
    """Fan one fire out to every active subscriber (email each + ONE Discord post).
    Never raises. Returns per-channel honest results:
    ``{"sent": n, "gated": bool, "fire_id": id, "email": [...], "discord": {...}}``.
    Gates (flag off / malformed / deduped / no subscribers / dead store) return
    ``sent=0, gated=True`` with the reason — and only the dedup gate itself consumes
    the dedup key, so a gated fire stays deliverable once the gate lifts."""
    try:
        if not getattr(config, "SIGNALS_DELIVERY", True):
            return {"sent": 0, "gated": True,
                    "reason": "signals delivery disabled (UTAH_SIGNALS_DELIVERY=0)"}
        fid = _fire_id(fire)
        if fid is None:
            return {"sent": 0, "gated": True, "reason": "malformed fire (no usable id)"}
        key = _dedup_key(fid, fire)
        if _already_delivered(key):
            return {"sent": 0, "gated": True, "reason": "already delivered",
                    "fire_id": fid}
        subs_fn = subscribers_fn or _SUBSCRIBERS_FN or subscribers
        try:
            subs = list(subs_fn() or [])
        except Exception as exc:  # noqa: BLE001 — a dead store gates, never crashes
            return {"sent": 0, "gated": True, "fire_id": fid,
                    "reason": f"subscriber store unreachable: {exc}"}
        active = [str(s["email"]) for s in subs
                  if isinstance(s, dict) and s.get("email")
                  and str(s.get("status", "active")) == "active"]
        if not active:
            return {"sent": 0, "gated": True, "reason": "no active subscribers",
                    "fire_id": fid}
        # Mark BEFORE the fan-out (the alerts durable-dedup direction): a crash or
        # restart mid-send must never re-spam every inbox; a partial send is visible
        # in the per-channel results below, never silently retried wholesale.
        _mark_delivered(key)
        subject, body = compose(fire)
        send = mail_send or _MAIL_SEND or _real_mail_send
        email_results, sent = [], 0
        for to in active:
            try:
                r = send(to, subject, body) or {}
            except Exception as exc:  # noqa: BLE001 — one dead inbox never blocks the rest
                r = {"sent": False, "gated": False, "error": str(exc)}
            one = {"to": to, "sent": bool(r.get("sent"))}
            one.update({k: r[k] for k in ("gated", "reason", "error") if k in r})
            sent += 1 if one["sent"] else 0
            email_results.append(one)
        post = discord_post or _DISCORD_POST or _real_discord_post
        try:
            discord_res = _normalize_discord(post(subject, body))
        except Exception as exc:  # noqa: BLE001 — Discord must never block email delivery
            discord_res = {"posted": False, "error": str(exc)}
        return {"sent": sent, "gated": False, "fire_id": fid,
                "subscribers": len(active), "email": email_results,
                "discord": discord_res}
    except Exception as exc:  # noqa: BLE001 — delivery must never raise into record_fire
        log.debug("signals delivery swallowed: %s", exc, exc_info=True)
        try:
            from utah import failures

            failures.record("signals", "deliver_failed", str(exc)[:200])
        except Exception:  # noqa: BLE001 — the failure log itself must never raise here
            pass
        return {"sent": 0, "gated": False, "error": str(exc)}


def deliver_fire_async(fire: dict, *, deliver=None) -> None:
    """Fire-and-forget delivery on a background thread — the ``record_fire`` hook, so
    the ledger write never blocks on SMTP/Discord (the ``alerts.critical_async``
    pattern). Captures the transports NOW so a test teardown can never let a late
    thread reach the real ones. Never raises."""
    if deliver is None:
        mail_send, discord_post, subs_fn = _MAIL_SEND, _DISCORD_POST, _SUBSCRIBERS_FN

        def deliver(f, _m=mail_send, _d=discord_post, _s=subs_fn):
            return deliver_fire(f, mail_send=_m, discord_post=_d, subscribers_fn=_s)

    def _go() -> None:
        try:
            deliver(fire)
        except Exception:  # noqa: BLE001 — the thread dies quietly, the write already landed
            pass

    try:
        threading.Thread(target=_go, name="utah-signals-deliver", daemon=True).start()
    except Exception:  # noqa: BLE001 — thread spawn failure must never break the caller
        pass


# --- the cron lane -------------------------------------------------------------------

_FIRE_COLS = ("id", "engine", "direction", "entry", "symbol", "stop", "target",
              "rationale", "outcome", "pnl", "ts")


def latest_fire() -> dict | None:
    """The newest REAL fire — ungraded (fresh signal) or graded (its result). Bounded
    read (LIMIT 1) on the pooled, connect-timeout-bounded connection."""
    with _conn() as c:
        row = c.execute(
            "SELECT id, engine, direction, entry::float8, symbol, stop::float8, "
            "target::float8, rationale, outcome, pnl::float8, "
            "to_char(ts,'YYYY-MM-DD HH24:MI:SS') FROM fires "
            "WHERE NOT synthetic ORDER BY id DESC LIMIT 1",
        ).fetchone()
    return dict(zip(_FIRE_COLS, row)) if row is not None else None


def deliver_latest(*, fire_fn=None, mail_send=None, discord_post=None,
                   subscribers_fn=None) -> dict:
    """Deliver the most recent ungraded-or-newly-graded fire — the cron entry
    (``python -m utah.product.signals deliver-latest``). The per-state dedup makes the
    cadence idempotent: an already-delivered state gates honestly, a fresh grade on a
    delivered fire goes out exactly once. Never raises."""
    try:
        fire = (fire_fn or latest_fire)()
    except Exception as exc:  # noqa: BLE001 — a dead fires store gates, never crashes the cron
        return {"sent": 0, "gated": True, "reason": f"fires store unreachable: {exc}"}
    if not fire:
        return {"sent": 0, "gated": True, "reason": "no fires recorded"}
    return deliver_fire(fire, mail_send=mail_send, discord_post=discord_post,
                        subscribers_fn=subscribers_fn)


# --- CLI -------------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m utah.product.signals",
        description="Signals subscriber delivery — fan graded engine fires out to "
                    "the signals_subscribers list (email + one Discord post).")
    ap.add_argument("command", choices=["deliver-latest", "subscribers", "subscribe", "pause"])
    ap.add_argument("email", nargs="?", help="for subscribe/pause")
    args = ap.parse_args(argv)
    try:
        if args.command == "deliver-latest":
            print(json.dumps(deliver_latest(), default=str))
            return 0
        if args.command == "subscribers":
            print(json.dumps(subscribers(), default=str))
            return 0
        if not args.email:
            ap.error(f"{args.command} needs an email")
        if args.command == "subscribe":
            print(json.dumps({"ok": True, "new": subscribe(args.email)}))
        else:
            print(json.dumps({"ok": True, "updated": set_status(args.email, "paused")}))
        return 0
    except SignalsError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


__all__ = ["SignalsError", "init_schema", "subscribers", "subscribe", "set_status",
           "compose", "deliver_fire", "deliver_fire_async", "latest_fire",
           "deliver_latest", "set_transports", "set_seen_path"]


if __name__ == "__main__":
    import sys

    sys.exit(_main())
