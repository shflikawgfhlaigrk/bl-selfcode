"""Utah interface — the surfaces Michael lives in.

The web surface (the Black Gold command deck) is served here and bridged to the
daemon: ``/api/status`` reflects live daemon state and ``/events`` streams the
bus over SSE — so the deck reflects backend state by push, never stale poll.
The agentic voice+chat harness layers on top (the brain's ``tell`` is its core).

Submodules resolve lazily (PEP 562): ``import utah.interface`` stays free of the
web stack (Starlette/uvicorn/SSE), so daemon-side imports of the package never
pay the bridge's import cost; ``utah.interface.web`` loads on first attribute
touch and is cached by the import system thereafter.
"""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["web"]


def __getattr__(name: str) -> ModuleType:
    """Lazy submodule access — ``utah.interface.web`` imports on first touch."""
    if name in __all__:
        return import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
