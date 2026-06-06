"""Peer-credential auth — the control socket only serves its owner.

A unix socket at 0700-dir + 0600 is already owner-scoped, but we verify in
depth: read the connecting peer's uid from the kernel and reject anything that
is not this process's uid. macOS exposes this via ``LOCAL_PEERCRED`` (the
``xucred`` struct); Linux via ``SO_PEERCRED``. **Fail closed** — if the uid
cannot be read, the connection is denied, never waved through.
"""
from __future__ import annotations

import os
import socket
import struct

from utah import UtahError

_SOL_LOCAL = 0  # macOS level for LOCAL_PEERCRED
_LOCAL_PEERCRED = getattr(socket, "LOCAL_PEERCRED", 0x001)
_SO_PEERCRED = getattr(socket, "SO_PEERCRED", None)


class PeerAuthError(UtahError):
    """The connecting peer is not the daemon's owner (or could not be verified)."""


def peer_uid(sock: socket.socket) -> int | None:
    """Return the connecting peer's uid, or ``None`` if it cannot be determined."""
    try:
        if _SO_PEERCRED is not None:  # Linux: struct ucred { pid, uid, gid }
            raw = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw)
            return uid
        # macOS: struct xucred { u_int cr_version; uid_t cr_uid; ... }
        raw = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 76)
        if len(raw) >= 8:
            _version, uid = struct.unpack_from("<II", raw, 0)
            return int(uid)
    except OSError:
        return None
    return None


def authorize(sock: socket.socket) -> int:
    """Verify the peer is this process's owner. Returns the uid or raises.

    Fail-closed: an unreadable credential is a denial, not a default-allow.
    """
    uid = peer_uid(sock)
    if uid is None:
        raise PeerAuthError("peer credentials unreadable — denied (fail-closed)")
    if uid != os.getuid():
        raise PeerAuthError(f"peer uid {uid} != owner {os.getuid()} — denied")
    return uid
