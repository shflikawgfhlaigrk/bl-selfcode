"""Memory errors — policy vs infrastructure, never mixed."""
from utah import UtahError


class MemoryUnavailable(UtahError):
    """The memory store could not be reached or a statement failed."""


class AdmissionDenied(UtahError):
    """The admission gate rejected a write (policy, not infrastructure)."""
