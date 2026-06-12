"""Utah product / revenue — the money, routed through one transactional ledger.

Ace's pattern was "build it, gate it, forget to open it": the lead machine sat
at $0 behind a placeholder address, reels rendered into a folder nothing posted
from, outreach had no never-twice guarantee. Utah routes every revenue artifact
through a Postgres ledger with UNIQUE = never-twice, and every write publishes to
its dashboard channel. The live money flow (Resend sending domain, CAN-SPAM
address, posting credentials, WIN feed) is gated on Michael's business inputs —
the ledger + wiring is built and proven; the gates open when those land.

Capability modules are exposed lazily (PEP 562, same pattern as ``utah.store``):
``utah.product.enrich`` resolves on first touch, so importing the package stays
free for the daemon paths that never touch a given capability.
"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

#: Every capability module in this package, resolvable as a lazy attribute. Computed
#: from disk (one cheap directory scan), so a new module is advertised without editing
#: this list — and a deleted one stops being advertised, never goes stale.
__all__ = sorted(m.name for m in pkgutil.iter_modules(__path__))


def __getattr__(name: str) -> ModuleType:
    """Lazy capability access: ``utah.product.leads`` imports the module on first use.

    Only converts "no such submodule" into AttributeError — a submodule whose OWN
    import crashes (a missing dependency, a syntax error) propagates loudly, because
    masking a broken deploy as a missing attribute would be a dishonest signal."""
    if name in __all__:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
