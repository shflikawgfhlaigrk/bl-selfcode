"""Peer-cred on the REAL accept path (listener → connect → accept → authorize),
plus the xucred layout guard: a kernel struct whose version field is not the
one we parse must read as 'undetermined' (denied), never as a misparsed uid —
fail-closed extends to struct drift, not just missing credentials."""
from __future__ import annotations

import os
import socket
import struct
import tempfile

import pytest

from utah.daemon import peercred
from utah.daemon.peercred import PeerAuthError, authorize, peer_uid


def test_authorize_accepts_the_owner_over_a_real_listener_accept():
    """The exact shape of the live path: the server authorizes the ACCEPTED
    socket of a freshly connected client."""
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    path = os.path.join(d, "p.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(path)
        srv.listen(1)
        cli.connect(path)
        conn, _ = srv.accept()
        try:
            assert authorize(conn) == os.getuid()
        finally:
            conn.close()
    finally:
        cli.close()
        srv.close()
        os.unlink(path)
        os.rmdir(d)


class _FakeSock:
    def __init__(self, raw: bytes | Exception) -> None:
        self._raw = raw

    def getsockopt(self, *_a):
        if isinstance(self._raw, Exception):
            raise self._raw
        return self._raw


@pytest.mark.skipif(peercred._SO_PEERCRED is not None,
                    reason="xucred parse is the macOS path")
def test_unknown_xucred_version_is_denied_not_misparsed():
    """76 bytes that LOOK like a cred but carry an unknown layout version must
    not be trusted — version drift means our offsets may be wrong."""
    raw = struct.pack("<II", 42, os.getuid()) + b"\x00" * 68  # version 42 ≠ XUCRED_VERSION
    assert peer_uid(_FakeSock(raw)) is None  # type: ignore[arg-type]
    with pytest.raises(PeerAuthError, match="fail-closed"):
        authorize(_FakeSock(raw))  # type: ignore[arg-type]


def test_getsockopt_oserror_reads_as_undetermined():
    assert peer_uid(_FakeSock(OSError("ENOTCONN"))) is None  # type: ignore[arg-type]


def test_authorize_error_is_a_typed_utah_error():
    """Callers catch UtahError at the daemon boundary — PeerAuthError must be in
    that family so a denial degrades to a typed rejection, not a crash."""
    from utah import UtahError
    assert issubclass(PeerAuthError, UtahError)
