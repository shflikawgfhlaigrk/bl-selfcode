"""Peer-credential auth, proven on REAL unix sockets: the owner is accepted,
everything else — foreign uid, unreadable creds, dead socket, short kernel
buffer — is DENIED. Fail-closed is the whole point; these tests pin it."""
from __future__ import annotations

import os
import socket

import pytest

from utah.daemon import peercred
from utah.daemon.peercred import PeerAuthError, authorize, peer_uid


@pytest.fixture
def unix_pair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    yield a, b
    a.close()
    b.close()


# -- peer_uid -----------------------------------------------------------------

def test_peer_uid_on_a_real_socketpair_is_the_owner(unix_pair):
    a, b = unix_pair
    assert peer_uid(a) == os.getuid()
    assert peer_uid(b) == os.getuid()  # symmetric: both ends see the same owner


def test_peer_uid_on_a_dead_socket_is_none():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    a.close()
    b.close()
    assert peer_uid(a) is None  # OSError → None, never a guessed uid


def test_peer_uid_short_kernel_buffer_is_none_not_a_crash():
    """A cred buffer too short for xucred's version+uid must read as
    'undetermined', not raise out of the auth boundary."""

    class ShortCred:
        def getsockopt(self, *_a):
            return b"\x00\x00\x00"  # 3 bytes < the 8 needed

    assert peer_uid(ShortCred()) is None  # type: ignore[arg-type]


# -- authorize ----------------------------------------------------------------

def test_authorize_accepts_the_owner_on_a_real_socket(unix_pair):
    a, _ = unix_pair
    assert authorize(a) == os.getuid()


def test_authorize_denies_unreadable_credentials_fail_closed(unix_pair, monkeypatch):
    monkeypatch.setattr(peercred, "peer_uid", lambda sock: None)
    a, _ = unix_pair
    with pytest.raises(PeerAuthError, match="fail-closed"):
        authorize(a)


def test_authorize_denies_a_foreign_uid(unix_pair, monkeypatch):
    intruder = os.getuid() + 1
    monkeypatch.setattr(peercred, "peer_uid", lambda sock: intruder)
    a, _ = unix_pair
    with pytest.raises(PeerAuthError, match=f"peer uid {intruder}"):
        authorize(a)


def test_authorize_denies_root_too(unix_pair, monkeypatch):
    """Owner-only means OWNER-only: even uid 0 is not this daemon's owner."""
    monkeypatch.setattr(peercred, "peer_uid", lambda sock: 0)
    a, _ = unix_pair
    if os.getuid() == 0:
        pytest.skip("running as root — owner IS uid 0 here")
    with pytest.raises(PeerAuthError, match="denied"):
        authorize(a)
