"""Utah storage tiers.

Postgres + pgvector is the PRIMARY store of record (OLTP + vectors + FTS + JSONB
+ LISTEN/NOTIFY, MVCC concurrent writers). DuckDB is the OLAP analytics tier —
it reads the primary zero-copy via the postgres scanner; it is never a second
source of truth. SQLite is retired except, at most, tiny local config.

Tier modules are exposed lazily (PEP 562): ``utah.store.olap`` resolves on first
touch, so the many callers that import the package without ever running
analytics never pay the OLAP module's import cost.
"""
from __future__ import annotations

import importlib
from types import ModuleType

#: The package's tier modules, resolvable as lazy attributes.
__all__ = ["olap"]


def __getattr__(name: str) -> ModuleType:
    """Lazy tier access: ``utah.store.olap`` imports the module on first use.

    Only names in ``__all__`` resolve — everything else (including the dunder
    probes copy/pickle/inspect throw at modules) raises a plain AttributeError
    that names the available tiers, never a surprise submodule import."""
    if name in __all__:
        # import_module binds the submodule onto this package, so this hook only
        # pays the import cost once; later access is a plain attribute hit.
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r} (tiers: {', '.join(__all__)})")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
