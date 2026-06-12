"""Connectivity capability — Ace's connectivity transitions here (NOT an agent): is the box
online? Probes a couple of reliable hosts; an all-unreachable result documents the outage.
Real, ungated. The fetch is injectable.
"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request

from utah import failures

log = logging.getLogger("utah.connectivity")

#: Two independent hosts so one flaky CDN can never fake an outage.
_HOSTS = ("https://www.google.com", "https://duckduckgo.com")

#: Per-probe timeout — env-tunable, never unbounded.
try:
    _TIMEOUT_S = float(os.environ.get("UTAH_CONNECTIVITY_TIMEOUT_S", "8"))
except ValueError:
    _TIMEOUT_S = 8.0
if not 0 < _TIMEOUT_S <= 30:
    _TIMEOUT_S = 8.0


def _head(url: str) -> None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Utah/1.0"})
    urllib.request.urlopen(req, timeout=_TIMEOUT_S).close()


def check(hosts=None, *, fetch=None) -> dict:
    """Probe *hosts*; return ``{online, hosts}``. Records an outage if ALL are unreachable
    (a single host being down isn't an outage). Never raises.

    An HTTP error response (405/503/...) counts as ONLINE: the server answered, so
    DNS+TCP+HTTP all work — only a transport-level failure is "unreachable". Counting a
    server's error page as an outage would fabricate one."""
    hosts = list(hosts or _HOSTS)
    fetch = fetch or _head
    results: dict[str, bool] = {}
    for h in hosts:
        try:
            fetch(h)
            results[h] = True
        except urllib.error.HTTPError as exc:
            results[h] = True  # the server ANSWERED — the network path is up
            log.debug("connectivity: %s answered HTTP %s (still online)", h, exc.code)
        except Exception as exc:  # noqa: BLE001 — injectable boundary: any transport failure = host down
            results[h] = False
            log.debug("connectivity: %s unreachable: %s: %s", h, type(exc).__name__, exc)
    online = any(results.values())
    if not online:
        failures.record("connectivity", "offline",
                        f"all probe hosts unreachable: {list(results)}")
    return {"online": online, "hosts": results}


__all__ = ["check"]
