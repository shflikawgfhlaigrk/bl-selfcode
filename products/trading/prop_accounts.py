"""Trading — per-client connection registry: any WealthCharts login + any prop firm.

The Trading spec: a client plugs in their own WealthCharts login and whatever prop firm they
trade, and the app shows THEIR signals. This module is the intake/registry for those
connections — the same owned-in-house pattern as the Leads client-email registry
(:func:`utah.mail.register_client_account`).

"Any prop firm" is literal: there is NO allow-list gate — any firm name is accepted.
:data:`KNOWN_PROP_FIRMS` is only a UI hint. Showing the signals from a connection is the
deliberate next step (it touches the live feed) and is kept out of this registry.
"""
from __future__ import annotations

import json
from pathlib import Path

from utah.daemon import runtime

#: Per-client trading connections:
#: ``{client_id: {wealthcharts: {user, password}, prop_firm: {name, account}}}``
CONNECTIONS_FILE = runtime.UTAH_HOME / "secrets" / "prop_accounts.json"

#: UI hints only — ANY prop firm name is accepted (the spec is "any prop firm").
KNOWN_PROP_FIRMS = ["Apex", "Topstep", "FTMO", "MyFundedFutures", "TakeProfitTrader",
                    "Earn2Trade", "Bulenox", "Tradeify", "Alpha Futures", "Funded Trading Plus"]


def _load() -> dict:
    try:
        data = json.loads(CONNECTIONS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


def _norm_wc(wc: dict | None) -> dict | None:
    """A WealthCharts login needs a user; password optional here (session may be cookie-based)."""
    if not isinstance(wc, dict) or not (wc.get("user") or "").strip():
        return None
    return {"user": wc["user"].strip(), "password": wc.get("password")}


def _norm_firm(firm: dict | None) -> dict | None:
    """A prop firm needs a name — and ANY name is valid."""
    if not isinstance(firm, dict) or not (firm.get("name") or "").strip():
        return None
    return {"name": firm["name"].strip(), "account": firm.get("account")}


def connection(client_id: str) -> dict | None:
    """A client's stored trading connection, or None if they haven't registered one."""
    raw = _load().get(client_id)
    return raw if isinstance(raw, dict) and raw else None


def list_clients() -> list[str]:
    """All client ids with a registered connection."""
    return list(_load().keys())


def register_connection(client_id: str, *, wealthcharts: dict | None = None,
                        prop_firm: dict | None = None) -> dict:
    """Store a client's WealthCharts login and/or prop firm so the app can show THEIR signals.
    Requires ``client_id`` and at least one valid connection (a WC login with a user, or a prop
    firm with a name). Atomic write (tmp + rename). Returns ``{ok}`` / ``{ok: False, error}``."""
    wc = _norm_wc(wealthcharts)
    firm = _norm_firm(prop_firm)
    if not client_id or not (wc or firm):
        return {"ok": False,
                "error": "client_id plus a WealthCharts login (with user) or a prop firm (with name) is required"}
    data = _load()
    entry = data.get(client_id, {}) if isinstance(data.get(client_id), dict) else {}
    if wc:
        entry["wealthcharts"] = wc
    if firm:
        entry["prop_firm"] = firm
    data[client_id] = entry
    try:
        CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(CONNECTIONS_FILE) + ".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(CONNECTIONS_FILE)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "client_id": client_id,
            "has_wealthcharts": bool(wc), "prop_firm": (firm or {}).get("name")}


__all__ = ["CONNECTIONS_FILE", "KNOWN_PROP_FIRMS", "connection", "list_clients",
           "register_connection"]
