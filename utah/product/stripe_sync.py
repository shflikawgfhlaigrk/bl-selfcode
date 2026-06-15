"""Stripe -> Utah sales mirror — the one bridge that turns a real purchase into a row.

Utah does NOT run the storefront or the checkout — that is Michael's blacklabelbots
site + Stripe (see ``ops/BOUNDARIES.md``: "the paywall/sale page stays Michael's").
Utah is strictly read-only here: it PULLS completed charges from Stripe (the source of
truth) and mirrors each into the ``sales`` ledger, idempotently keyed on the Stripe
charge id. The first real sale that lands flips :func:`utah.revenue_heal.outcome_gate`
green — which is the whole point of wiring this at all.

Honest-gated like every paid lane: with no Stripe key it returns ``gated`` (never
fabricates a sale, never raises). Drop ``~/.utah/secrets/stripe.json`` =
``{"secret_key": "rk_live_..."}`` to arm it — a RESTRICTED, read-only key (charges:read)
is plenty and safer than a full secret key.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from utah import config, failures
from utah.product import ledger as product_ledger
from utah.product.ledger import LedgerError

log = logging.getLogger("stripe_sync")

#: Michael's business input — a restricted, read-only Stripe key. Absent -> gated.
STRIPE_CREDS = config.UTAH_HOME / "secrets" / "stripe.json"
API_BASE = "https://api.stripe.com/v1"
DEFAULT_LIMIT = 100
_HTTP_TIMEOUT = 15

Fetcher = Callable[[], list[dict]]


def _secret_key() -> str | None:
    """The Stripe API key from ~/.utah/secrets/stripe.json, or None when absent/malformed
    (-> gated, never an error)."""
    try:
        data = json.loads(STRIPE_CREDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = str(data.get("secret_key") or data.get("api_key") or "").strip()
    return key or None


def fetch_charges(secret_key: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """GET /v1/charges (newest first). Read-only. Raises on transport/HTTP error so the
    caller records an honest 'failed' sync rather than a silently empty pull."""
    limit = max(1, min(int(limit), 100))
    req = urllib.request.Request(
        f"{API_BASE}/charges?limit={limit}",
        headers={"Authorization": f"Bearer {secret_key}"},
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 — fixed https Stripe host
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _is_real_sale(charge: dict) -> bool:
    """A charge counts as revenue iff it was paid and not (fully) refunded — activity
    that did not net money never becomes a ``sales`` row."""
    if not charge.get("paid") or charge.get("refunded"):
        return False
    return (charge.get("amount") or 0) - (charge.get("amount_refunded") or 0) > 0


def _sale_fields(charge: dict) -> dict:
    """Map a Stripe charge onto :meth:`Ledger.record_sale` kwargs (net amount, buyer
    email, completion time). Defensive on every field — Stripe nulls description often."""
    bd = charge.get("billing_details") or {}
    created = charge.get("created")
    net = (charge.get("amount") or 0) - (charge.get("amount_refunded") or 0)
    return {
        "stripe_id": str(charge.get("id") or ""),
        "product": str(charge.get("description") or charge.get("statement_descriptor") or "")[:200],
        "customer": str(charge.get("receipt_email") or bd.get("email")
                        or charge.get("customer") or "")[:200],
        "amount_cents": int(net),
        "currency": str(charge.get("currency") or "usd"),
        "status": "paid",
        "completed_at": (datetime.fromtimestamp(int(created), tz=timezone.utc)
                         if created else None),
    }


def _record_sync(led, rows_in: int, status: str, detail: str) -> None:
    """Append the auditable run row; a downed ledger must not mask the sync result."""
    try:
        led.record_sync("stripe", kind="refresh", rows_in=rows_in, status=status, detail=detail)
    except LedgerError:
        pass


def sync(*, fetch_fn: Fetcher | None = None, ledger=None, limit: int = DEFAULT_LIMIT) -> dict:
    """One Stripe -> ``sales`` mirror pass. Never raises. Returns an honest stats dict.

    Gated (no key and no injected fetcher) -> ``{"ok": True, "gated": True, "synced": 0}``;
    gating is not failure. *fetch_fn* and *ledger* are the unit-proof seams (tests drive
    the whole pass without Stripe or Postgres); production callers leave the defaults.
    """
    if fetch_fn is None:
        key = _secret_key()
        if not key:
            return {"ok": True, "gated": True, "synced": 0, "seen": 0,
                    "reason": f"no Stripe key ({STRIPE_CREDS}) — Michael's business input"}
        fetch_fn = lambda: fetch_charges(key, limit)  # noqa: E731 — tiny bound fetcher

    led = ledger if ledger is not None else product_ledger.get_ledger()
    try:
        led.init_schema()                        # the sales table exists before the first write
    except LedgerError as exc:
        failures.record("stripe_sync", "db_unreachable", str(exc)[:200])
        return {"ok": False, "gated": False, "synced": 0, "seen": 0, "error": str(exc)[:200]}

    try:
        charges = fetch_fn()
    except Exception as exc:  # noqa: BLE001 — Stripe down/bad key: honest 'failed', never crash the cron
        failures.record("stripe_sync", "fetch_failed", str(exc)[:200])
        _record_sync(led, 0, "failed", str(exc)[:200])
        return {"ok": False, "gated": False, "synced": 0, "seen": 0, "error": str(exc)[:200]}

    seen = new = 0
    for ch in charges:
        if not isinstance(ch, dict) or not _is_real_sale(ch):
            continue
        seen += 1
        fields = _sale_fields(ch)
        if not fields["stripe_id"]:
            continue
        try:
            if led.record_sale(**fields):
                new += 1
        except LedgerError as exc:
            failures.record("stripe_sync", "record_failed", f"{fields['stripe_id']}: {exc}"[:200])

    fetched = len(charges)
    _record_sync(led, new, "ok", f"{new} new / {seen} paid of {fetched} fetched")
    if new:
        log.info("stripe_sync: %s NEW sale(s) recorded — the revenue gate may now be GREEN", new)
    return {"ok": True, "gated": False, "synced": new, "seen": seen, "fetched": fetched}


def run_scheduled() -> dict:
    """Cron entrypoint (``com.utah.stripe-sync``, every 15 min)."""
    res = sync()
    log.info("stripe_sync: %s", res)
    return res


__all__ = ["sync", "run_scheduled", "fetch_charges", "STRIPE_CREDS"]
