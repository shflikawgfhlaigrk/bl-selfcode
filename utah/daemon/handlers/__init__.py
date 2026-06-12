"""Control-plane handlers. Each is a small async function wired to LIVE code
(real brain, real pool, real governor) — no injectable stand-ins.

The method table is validated AT IMPORT: a handler that isn't an async function
would only explode at its first call (``await`` on a plain return value →
INTERNAL_ERROR in production), so :func:`validated_registry` rejects the table
up front — a broken registry fails the daemon's import, not a customer call."""
from __future__ import annotations

import inspect

from utah.daemon.handlers.core_handlers import REGISTRY as _REGISTRY


def validated_registry(table: dict) -> dict:
    """Return *table* unchanged iff every entry is callable at the wire.

    Raises ``TypeError`` naming the offender when a method name is not a
    non-blank string or a handler is not an ``async def`` function — the only
    shape :mod:`utah.daemon` can ``await``. Identity-preserving on success so
    callers can wire the validated table directly."""
    for name, fn in table.items():
        if not isinstance(name, str) or not name.strip():
            raise TypeError(f"registry method name must be a non-blank string, got {name!r}")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"handler {name!r} is not an async function: {fn!r}")
    return table


REGISTRY = validated_registry(_REGISTRY)

__all__ = ["REGISTRY", "validated_registry"]
