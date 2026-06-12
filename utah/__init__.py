"""Utah — step 1 of the baseline: THE BRAIN.

Grounded, compounding memory on Postgres + pgvector with the Claude CLI as the
one paid reasoning lane. This package is the whole of step 1: recall -> ground ->
reason -> remember, hardened. Nothing here fabricates: every answer is either
grounded in admitted memory, produced by the brain from recalled context, or an
explicit "I don't know".
"""

__version__ = "1.0.0"

__all__ = ["UtahError", "__version__"]


class UtahError(Exception):
    """Base class for every structured Utah failure.

    Subclasses signal *which* boundary failed (embedding, brain subprocess,
    memory store, admission policy) so callers can degrade gracefully instead
    of crashing the loop or swallowing errors blindly.
    """
