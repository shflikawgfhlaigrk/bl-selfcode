"""External-data capability — Ace's weather/finance/accountant transition here (NOT agents):
look up weather or a market quote. GATED on a data source / API key (flag
``~/.utah/secrets/external.json``). Fetcher wired + injectable; with no key each documents
the gate and returns available=False — never fabricates a number.
"""
from __future__ import annotations

import logging

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.external")

EXTERNAL_CREDS = runtime.UTAH_HOME / "secrets" / "external.json"


def source_available() -> bool:
    return EXTERNAL_CREDS.exists()


def _gate(kind: str) -> dict:
    failures.record("external", "gated",
                    f"{kind} gated: no data source key at {EXTERNAL_CREDS} (Michael's input)")
    return {"available": False, "gated": True}


def weather(location: str, *, fetch=None) -> dict:
    if fetch is None and not source_available():
        return _gate(f"weather {location[:30]}")
    try:
        return {"available": True, "gated": False, "location": location,
                "data": (fetch or _no_fetch)(location)}
    except Exception as exc:  # noqa: BLE001
        failures.record("external", "weather_failed", str(exc))
        return {"available": False, "gated": False, "error": str(exc)}


def quote(symbol: str, *, fetch=None) -> dict:
    if fetch is None and not source_available():
        return _gate(f"quote {symbol[:10]}")
    try:
        return {"available": True, "gated": False, "symbol": symbol,
                "data": (fetch or _no_fetch)(symbol)}
    except Exception as exc:  # noqa: BLE001
        failures.record("external", "quote_failed", str(exc))
        return {"available": False, "gated": False, "error": str(exc)}


def _no_fetch(_):  # pragma: no cover
    raise RuntimeError(f"external data source not configured ({EXTERNAL_CREDS})")


__all__ = ["weather", "quote", "source_available", "EXTERNAL_CREDS"]
