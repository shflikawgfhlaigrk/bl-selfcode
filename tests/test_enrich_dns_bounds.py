"""Enrichment DNS is BOUNDED — no blockable resolver call on a live path.

``domain_resolves`` used ``socket.getaddrinfo``, which has no timeout knob, on two live
paths: the ``com.utah.enrich`` cron (find_email -> domain_accepts_mail A-fallback) and the
outreach send gate (outreach -> verify_email). A slow resolver could hang both forever.
These tests pin the fix: every DNS lookup (A, AAAA, MX) rides ONE ``dig`` shell-out
primitive with a subprocess timeout, getaddrinfo is never touched, hostile domain strings
never reach the subprocess, and ``_registrable_domain`` honors common two-label public
suffixes (foo.co.uk is not the same registrant as co.uk).
"""
from __future__ import annotations

import socket
import subprocess

import pytest

from utah.product import enrich


class _Out:
    def __init__(self, stdout: str = ""):
        self.stdout = stdout


@pytest.fixture()
def no_getaddrinfo(monkeypatch):
    """Any getaddrinfo call is the unbounded bug coming back — fail loudly."""
    def trap(*a, **kw):
        raise AssertionError("socket.getaddrinfo has no timeout and must never be called")
    monkeypatch.setattr(socket, "getaddrinfo", trap)


# --- domain_resolves: bounded dig, never getaddrinfo -----------------------------

def test_domain_resolves_uses_bounded_dig_not_getaddrinfo(monkeypatch, no_getaddrinfo):
    calls: list[tuple[list, dict]] = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return _Out("93.184.216.34\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich.domain_resolves("joesdiner.com") is True
    argv, kw = calls[0]
    assert argv[0] == "dig" and "joesdiner.com" in argv
    assert 0 < kw["timeout"] <= 30          # the in-code bound the cap demanded


def test_domain_resolves_falls_back_to_aaaa(monkeypatch, no_getaddrinfo):
    seen: list[str] = []

    def fake_run(argv, **kw):
        seen.append(argv[-2])               # the record type
        return _Out("" if argv[-2] == "A" else "2606:2800::1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich.domain_resolves("v6only.example") is True
    assert seen == ["A", "AAAA"]            # A first, AAAA only on miss


def test_domain_resolves_false_when_no_records(monkeypatch, no_getaddrinfo):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Out(""))
    assert enrich.domain_resolves("nonexistent-xyz.invalid") is False


def test_domain_resolves_false_when_dig_unavailable(monkeypatch, no_getaddrinfo):
    # Honest gate: if the lookup CANNOT run, the domain is not verified -> False,
    # never a crash and never a silent pass.
    def fake_run(argv, **kw):
        raise FileNotFoundError("dig: command not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich.domain_resolves("joesdiner.com") is False


def test_domain_resolves_false_on_dig_timeout(monkeypatch, no_getaddrinfo):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich.domain_resolves("slow-resolver.example") is False


# --- the shared primitive refuses unsafe argv ------------------------------------

def test_dig_refuses_flag_injection_and_garbage(monkeypatch):
    def trap(*a, **kw):
        raise AssertionError("unsafe domain must never reach the dig subprocess")

    monkeypatch.setattr(subprocess, "run", trap)
    assert enrich._dig("A", "-f/etc/passwd") == []     # would parse as a dig FLAG
    assert enrich._dig("A", "foo bar") == []
    assert enrich._dig("A", "foo;rm -rf /") == []
    assert enrich._dig("A", "") == []
    assert enrich._dig("A", None) == []


def test_dig_mx_rides_the_same_bounded_helper(monkeypatch):
    calls: list[tuple[list, dict]] = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return _Out("10 mail.joesdiner.com.\n\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich._dig_mx("joesdiner.com") == ["10 mail.joesdiner.com."]
    argv, kw = calls[0]
    assert "MX" in argv
    assert 0 < kw["timeout"] <= 30


def test_domain_accepts_mail_default_path_is_fully_bounded(monkeypatch, no_getaddrinfo):
    # The LIVE default path (cron + send gate, no injected lookups) end-to-end:
    # MX empty -> A fallback -> True, all through the bounded subprocess.
    def fake_run(argv, **kw):
        assert kw["timeout"] > 0
        return _Out("93.184.216.34\n" if argv[-2] in ("A", "AAAA") else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert enrich.domain_accepts_mail("joesdiner.com") is True


# --- registrable domain: two-label public suffixes --------------------------------

def test_registrable_domain_respects_two_label_public_suffixes():
    assert enrich._registrable_domain("www.acme.co.uk") == "acme.co.uk"
    assert enrich._registrable_domain("shop.foo.com.au") == "foo.com.au"
    # the suffix alone has no registrant label -> unchanged, never an index error
    assert enrich._registrable_domain("co.uk") == "co.uk"
    # plain gTLD behavior unchanged
    assert enrich._registrable_domain("www.joesdiner.com") == "joesdiner.com"


def test_registrable_domain_strips_a_port():
    assert enrich._registrable_domain("www.joesdiner.com:8443") == "joesdiner.com"


def test_email_on_website_matches_across_co_uk_subdomains():
    # PSL fix end-to-end: an info@ address on the lead's own .co.uk site must match.
    assert enrich._email_on_website("info@acme.co.uk", "https://www.acme.co.uk") is True
    # and a DIFFERENT .co.uk registrant must NOT collapse to the same "co.uk" domain
    assert enrich._email_on_website("info@other.co.uk", "https://www.acme.co.uk") is False
