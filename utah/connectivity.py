"""Connectivity capability — Ace's connectivity transitions here (NOT an agent): is the box
online? Probes a couple of reliable hosts; an all-unreachable result documents the outage.
Real, ungated. The fetch is injectable.
"""
from __future__ import annotations

import logging
import urllib.request

from utah import failures

log = logging.getLogger("utah.connectivity")

_HOSTS = ("https://www.google.com", "https://duckduckgo.com")


def _head(url: str) -> None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Utah/1.0"})
    urllib.request.urlopen(req, timeout=8).close()


def check(hosts=None, *, fetch=None) -> dict:
    """Probe *hosts*; return ``{online, hosts}``. Records an outage if ALL are unreachable
    (a single host being down isn't an outage). Never raises."""
    hosts = list(hosts or _HOSTS)
    fetch = fetch or _head
    results: dict[str, bool] = {}
    for h in hosts:
        try:
            fetch(h)
            results[h] = True
        except Exception:  # noqa: BLE001
            results[h] = False
    online = any(results.values())
    if not online:
        failures.record("connectivity", "offline",
                        f"all probe hosts unreachable: {list(results)}")
    return {"online": online, "hosts": results}


__all__ = ["check"]
