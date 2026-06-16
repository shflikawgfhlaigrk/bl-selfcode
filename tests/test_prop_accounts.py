"""Trading — per-client connection registry: any WealthCharts login + any prop firm.

The Trading spec: a client plugs in their own WealthCharts login and whatever prop firm they
trade, and the app shows THEIR signals. This is the intake/registry for those connections —
the same owned-in-house pattern as the Leads client-email registry. "Any prop firm" is literal:
no fixed allow-list, any name is accepted.
"""
from __future__ import annotations

from utah.product import prop_accounts


def _file(monkeypatch, tmp_path):
    f = tmp_path / "prop_accounts.json"
    monkeypatch.setattr(prop_accounts, "CONNECTIONS_FILE", f)
    return f


def test_register_and_read_wc_login_and_prop_firm(monkeypatch, tmp_path):
    _file(monkeypatch, tmp_path)
    r = prop_accounts.register_connection(
        "acme", wealthcharts={"user": "trader@x.com", "password": "p"},
        prop_firm={"name": "Apex", "account": "APX-1"})
    assert r["ok"] is True
    c = prop_accounts.connection("acme")
    assert c["wealthcharts"]["user"] == "trader@x.com"
    assert c["prop_firm"]["name"] == "Apex"


def test_any_prop_firm_name_is_accepted(monkeypatch, tmp_path):
    _file(monkeypatch, tmp_path)
    r = prop_accounts.register_connection("c", prop_firm={"name": "Some Brand-New Firm LLC"})
    assert r["ok"] is True
    assert prop_accounts.connection("c")["prop_firm"]["name"] == "Some Brand-New Firm LLC"


def test_register_requires_client_and_at_least_one_valid_connection(monkeypatch, tmp_path):
    _file(monkeypatch, tmp_path)
    assert prop_accounts.register_connection("", prop_firm={"name": "Apex"})["ok"] is False
    assert prop_accounts.register_connection("c")["ok"] is False                       # nothing
    assert prop_accounts.register_connection("c", wealthcharts={"password": "p"})["ok"] is False  # wc needs user
    assert prop_accounts.register_connection("c", prop_firm={"account": "X"})["ok"] is False      # firm needs name


def test_connection_none_when_unregistered(monkeypatch, tmp_path):
    _file(monkeypatch, tmp_path)
    assert prop_accounts.connection("nobody") is None


def test_list_clients(monkeypatch, tmp_path):
    _file(monkeypatch, tmp_path)
    prop_accounts.register_connection("a", prop_firm={"name": "Apex"})
    prop_accounts.register_connection("b", wealthcharts={"user": "u@x.com", "password": "p"})
    assert sorted(prop_accounts.list_clients()) == ["a", "b"]


def test_known_prop_firms_is_only_a_hint_list():
    assert "Apex" in prop_accounts.KNOWN_PROP_FIRMS and len(prop_accounts.KNOWN_PROP_FIRMS) >= 5
