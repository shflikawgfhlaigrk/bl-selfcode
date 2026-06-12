"""Repair-round pins for ``utah.daemon.cli`` (the three regraded gaps):

1. ``stop`` via the supervisor must NOT exit 0 when the daemon is still up
   after the 24s drain window — scripts read exit codes, not prose.
2. ``discord`` must render the plan from :func:`provision`'s public report
   dict, never by reaching into the integration's private ``_render_plan``.
3. parser construction stays single-statement-per-line (pinned indirectly by
   exercising every discord flag through the real parser).

Everything is monkeypatched — these tests must NEVER signal the live
supervisor, sleep for real, or touch the Discord API.
"""
from __future__ import annotations

import inspect
import signal

import pytest

from utah.daemon import cli


class _Args:
    """argparse.Namespace stand-in."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


PLAN_REP = {
    "gated": False, "dry_run": True, "guild_id": None, "guild_name": "Utah HQ",
    "created_roles": ["Operator", "Member"], "existing_roles": [],
    "created_categories": ["SPINE", "LEADS"],
    "created_channels": ["SPINE / status", "SPINE / failures", "LEADS / fresh"],
    "existing_channels": [],
    "webhooks": {"failures": "(dry-run)"},
    "errors": [],
    "totals": {"roles": 2, "categories": 2, "channels": 3, "webhooks": 1},
}


# -- stop: supervisor drain must be honest --------------------------------------

def _wire_supervisor_stop(monkeypatch, *, sup_pid: int, probes):
    """A live supervisor whose daemon answers ``probes`` (a list of _daemon_up
    results; the last value repeats). No real signals, no real sleeping."""
    kills = []
    it = iter(probes)
    last = probes[-1]
    monkeypatch.setattr(cli, "_read_pid", lambda path: sup_pid)
    monkeypatch.setattr(cli, "_alive", lambda pid: pid == sup_pid)
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_daemon_up", lambda timeout=1.0: next(it, last))
    return kills


def test_stop_drain_timeout_is_exit_1_on_stderr(monkeypatch, capsys):
    """The daemon survives the whole drain window: saying "still draining" with
    exit 0 is a success signal to every script — that is the dishonest-status
    cap. It must exit 1 and complain on stderr."""
    kills = _wire_supervisor_stop(monkeypatch, sup_pid=777, probes=[True])
    rc = cli.cmd_stop(_Args())
    assert rc == 1
    assert kills == [(777, signal.SIGTERM)]
    err = capsys.readouterr().err.lower()
    assert "still" in err and "stop" in err


def test_stop_drain_success_is_exit_0(monkeypatch, capsys):
    """The happy supervisor path: SIGTERM, daemon goes down, verified exit 0."""
    kills = _wire_supervisor_stop(monkeypatch, sup_pid=778, probes=[True, False])
    rc = cli.cmd_stop(_Args())
    assert rc == 0
    assert kills == [(778, signal.SIGTERM)]
    assert "stopped" in capsys.readouterr().out


# -- discord: plan rendering uses ONLY the public report dict -------------------

def test_cli_never_touches_the_integrations_private_renderer():
    """The cited coupling itself: cli must not name ``_render_plan`` anywhere —
    the CLI's contract with the integration is provision()'s report dict."""
    assert "_render_plan" not in inspect.getsource(cli)


def test_discord_plan_prints_the_rendered_report(monkeypatch, capsys):
    monkeypatch.setattr(
        "utah.integrations.discord.provision",
        lambda *a, **kw: dict(PLAN_REP),
    )
    rc = cli.cmd_discord(_Args(plan=True, guild=None, create=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Utah HQ" in out
    assert "Operator" in out and "Member" in out
    # channels grouped under their category, webhook channel tagged differently
    assert out.index("SPINE") < out.index("status") < out.index("LEADS")
    assert "failures" in out and "fresh" in out
    assert "2 categories" in out and "3 channels" in out and "1 feed webhooks" in out


def test_format_plan_groups_categories_and_tags_webhooks():
    text = cli._format_plan(PLAN_REP)
    lines = text.splitlines()
    assert lines[0] == "Server: Utah HQ"
    assert "Roles (2): Operator, Member" in lines
    # category header appears once, channels indented beneath it
    assert lines.count("SPINE") == 1
    spine_block = lines[lines.index("SPINE"):lines.index("LEADS")]
    assert any(l.endswith("status") for l in spine_block)
    webhook_line = next(l for l in lines if l.endswith("failures"))
    plain_line = next(l for l in lines if l.endswith("status"))
    assert webhook_line.split()[0] != plain_line.split()[0]  # tagged differently


def test_format_plan_tolerates_a_degenerate_report():
    """A drifted/partial report must degrade to readable text, not KeyError
    at the operator (the integration's renderer indexes raw keys)."""
    text = cli._format_plan({})
    assert isinstance(text, str) and "Server:" in text
    # malformed channel entry (no " / " separator) must not raise either
    text = cli._format_plan({"created_channels": ["orphan-entry"], "webhooks": {}})
    assert "orphan-entry" in text


def test_discord_gated_is_exit_2_and_still_shows_the_plan(monkeypatch, capsys):
    def fake_provision(*a, dry_run=False, **kw):
        return dict(PLAN_REP) if dry_run else {"gated": True}

    monkeypatch.setattr("utah.integrations.discord.provision", fake_provision)
    rc = cli.cmd_discord(_Args(plan=False, guild=None, create=False))
    assert rc == 2
    out = capsys.readouterr().out
    assert "GATED" in out
    assert "Utah HQ" in out  # the would-be plan is shown so the gate is actionable


def test_discord_apply_success_saves_webhooks_and_exits_0(monkeypatch, capsys):
    applied = dict(PLAN_REP)
    applied.update(dry_run=False, guild_id="g123")
    saved = []
    monkeypatch.setattr("utah.integrations.discord.provision", lambda *a, **kw: applied)
    monkeypatch.setattr("utah.integrations.discord.save_webhooks", saved.append)
    rc = cli.cmd_discord(_Args(plan=False, guild=None, create=False))
    assert rc == 0
    assert saved == [applied]
    assert "guild=g123" in capsys.readouterr().out


# -- parser: discord flags ride the real parser ---------------------------------

@pytest.mark.parametrize("argv,want", [
    (["discord"], {"plan": False, "create": False, "guild": None}),
    (["discord", "--plan"], {"plan": True, "create": False, "guild": None}),
    (["discord", "--create", "--guild", "42"],
     {"plan": False, "create": True, "guild": "42"}),
])
def test_parser_discord_flags(argv, want):
    ns = cli.build_parser().parse_args(argv)
    assert ns.fn is cli.cmd_discord
    for k, v in want.items():
        assert getattr(ns, k) == v
