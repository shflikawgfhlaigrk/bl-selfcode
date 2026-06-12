"""``utah`` CLI contract: exit codes are honest (0 = verified state, 1 = down/
failed, 2 = bad usage), output goes to the right stream, and no command ever
tracebacks at an operator. The control socket is monkeypatched — these tests
must NEVER start/stop the live daemon or signal the live supervisor."""
from __future__ import annotations

import json
import time

import pytest

from utah.daemon import cli
from utah.daemon.client import DaemonNotRunning


class _Args:
    """argparse.Namespace stand-in."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# -- parser wiring -------------------------------------------------------------

def test_parser_binds_every_subcommand_to_its_handler():
    p = cli.build_parser()
    cases = {
        "start": cli.cmd_start, "stop": cli.cmd_stop, "restart": cli.cmd_restart,
        "status": cli.cmd_status, "ping": cli.cmd_ping, "verify": cli.cmd_verify,
    }
    for name, fn in cases.items():
        assert p.parse_args([name]).fn is fn
    tell = p.parse_args(["tell", "hello", "world"])
    assert tell.fn is cli.cmd_tell and tell.text == ["hello", "world"]
    agent = p.parse_args(["agent", "1.5"])
    assert agent.fn is cli.cmd_agent and agent.seconds == 1.5


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


# -- tell ----------------------------------------------------------------------

def test_tell_empty_text_is_usage_error_2(capsys):
    rc = cli.cmd_tell(_Args(text=["   "]))
    assert rc == 2
    assert "nothing to tell" in capsys.readouterr().err


def test_tell_prints_source_and_text_and_never_persists(monkeypatch, capsys):
    seen = {}

    def fake_call(method, params=None, **kw):
        seen["method"], seen["params"] = method, params
        return {"source": "brain", "text": "the answer"}

    monkeypatch.setattr(cli.ctl, "call_sync", fake_call)
    rc = cli.cmd_tell(_Args(text=["what", "is", "utah?"]))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "[brain] the answer"
    assert seen["method"] == "tell"
    assert seen["params"]["text"] == "what is utah?"
    # terminal probes must never write a turn into recall
    assert seen["params"]["persist"] is False


def test_tell_daemon_down_is_exit_1_on_stderr(monkeypatch, capsys):
    def boom(*a, **kw):
        raise DaemonNotRunning("daemon not reachable")

    monkeypatch.setattr(cli.ctl, "call_sync", boom)
    rc = cli.cmd_tell(_Args(text=["hi"]))
    assert rc == 1
    assert "not reachable" in capsys.readouterr().err


def test_tell_unexpected_reply_shape_is_exit_1_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli.ctl, "call_sync", lambda *a, **kw: "weird-string")
    rc = cli.cmd_tell(_Args(text=["hi"]))
    assert rc == 1
    assert "unexpected" in capsys.readouterr().err.lower()


# -- status / ping -------------------------------------------------------------

def test_status_prints_json_when_up(monkeypatch, capsys):
    monkeypatch.setattr(cli.ctl, "call_sync", lambda *a, **kw: {"pid": 7, "ok": True})
    rc = cli.cmd_status(_Args())
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"pid": 7, "ok": True}


def test_status_down_is_exit_1(monkeypatch, capsys):
    def down(*a, **kw):
        raise DaemonNotRunning("gone")

    monkeypatch.setattr(cli.ctl, "call_sync", down)
    assert cli.cmd_status(_Args()) == 1
    assert "not running" in capsys.readouterr().out


def test_ping_down_is_exit_1(monkeypatch, capsys):
    def down(*a, **kw):
        raise TimeoutError("wedged")

    monkeypatch.setattr(cli.ctl, "call_sync", down)
    assert cli.cmd_ping(_Args()) == 1


# -- agent ----------------------------------------------------------------------

def test_agent_daemon_down_is_exit_1_not_a_traceback(monkeypatch, capsys):
    """Every command faces the same daemon-down reality — agent was the only
    one that let DaemonNotRunning traceback at the operator."""
    def down(*a, **kw):
        raise DaemonNotRunning("gone")

    monkeypatch.setattr(cli.ctl, "call_sync", down)
    rc = cli.cmd_agent(_Args(seconds=0.5))
    assert rc == 1
    assert "gone" in capsys.readouterr().err


def test_agent_passes_seconds_and_prints_the_result(monkeypatch, capsys):
    seen = {}

    def fake_call(method, params=None, **kw):
        seen["method"], seen["params"] = method, params
        return {"ran": True}

    monkeypatch.setattr(cli.ctl, "call_sync", fake_call)
    assert cli.cmd_agent(_Args(seconds=1.5)) == 0
    assert seen == {"method": "agent", "params": {"seconds": 1.5}}
    assert "ran" in capsys.readouterr().out


# -- start (already-running guard; the spawn itself is monkeypatched) ----------

def test_start_when_already_running_is_a_noop_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_daemon_up", lambda timeout=1.0: True)
    monkeypatch.setattr(cli, "live_pid", lambda: 4242)
    spawned = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    rc = cli.cmd_start(_Args())
    assert rc == 0
    assert spawned == []  # never double-starts
    assert "already running" in capsys.readouterr().out


def test_start_spawns_supervisor_detached_and_waits_for_readiness(
    monkeypatch, tmp_path, capsys
):
    probes = iter([False, True])  # guard probe → down; post-spawn probe → up
    monkeypatch.setattr(cli, "_daemon_up", lambda timeout=1.0: next(probes))
    monkeypatch.setattr(cli, "live_pid", lambda: 555)
    monkeypatch.setattr(cli.runtime, "ensure_runtime", lambda: None)
    monkeypatch.setattr(cli.runtime, "LOG_DIR", tmp_path)
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    rc = cli.cmd_start(_Args())
    assert rc == 0
    assert seen["argv"][-2:] == ["-m", "utah.daemon.supervisor"]
    assert seen["kw"]["start_new_session"] is True  # detached: survives the tty
    assert "started" in capsys.readouterr().out


# -- stop (fully faked: must not signal anything real) --------------------------

def test_stop_when_nothing_is_running_says_so_and_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_read_pid", lambda path: None)
    monkeypatch.setattr(cli, "_daemon_up", lambda timeout=1.0: False)
    rc = cli.cmd_stop(_Args())
    assert rc == 0
    assert "not running" in capsys.readouterr().out


def test_stop_failed_shutdown_is_reported_not_claimed(monkeypatch, capsys):
    """If the daemon survives the shutdown RPC the CLI must say FAILED with
    exit 1 — claiming success on a failed stop is the dishonest-signal cap."""
    monkeypatch.setattr(cli, "_read_pid", lambda path: None)  # no supervisor
    monkeypatch.setattr(cli, "_daemon_up", lambda timeout=1.0: True)  # still up after
    monkeypatch.setattr(cli.ctl, "call_sync", lambda *a, **kw: None)
    rc = cli.cmd_stop(_Args())
    assert rc == 1
    assert "failed" in capsys.readouterr().out.lower()


# -- verify ---------------------------------------------------------------------

def test_verify_missing_file_is_exit_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.runtime, "RUN_DIR", tmp_path)
    assert cli.cmd_verify(_Args()) == 1
    assert "no verify status" in capsys.readouterr().out


def test_verify_green_is_exit_0(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.runtime, "RUN_DIR", tmp_path)
    (tmp_path / "verify.json").write_text(json.dumps(
        {"state": "green", "exit_code": 0, "pyfiles": 100, "ts": time.time()}
    ))
    assert cli.cmd_verify(_Args()) == 0
    assert "green" in capsys.readouterr().out


def test_verify_confirmed_red_is_exit_1_with_failures_listed(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli.runtime, "RUN_DIR", tmp_path)
    (tmp_path / "verify.json").write_text(json.dumps({
        "state": "red", "confirmed": True, "exit_code": 1,
        "ts": time.time(), "failures": ["tests/test_x.py::test_y"],
    }))
    assert cli.cmd_verify(_Args()) == 1
    assert "test_x.py::test_y" in capsys.readouterr().out


def test_verify_unconfirmed_red_is_exit_0(monkeypatch, tmp_path):
    """A single red sample is noise (iCloud junk, transient) until the agent
    confirms it — the gate only fails on a CONFIRMED red."""
    monkeypatch.setattr(cli.runtime, "RUN_DIR", tmp_path)
    (tmp_path / "verify.json").write_text(json.dumps(
        {"state": "red", "confirmed": False, "exit_code": 1, "ts": time.time()}
    ))
    assert cli.cmd_verify(_Args()) == 0


def test_verify_corrupt_json_is_exit_1_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.runtime, "RUN_DIR", tmp_path)
    (tmp_path / "verify.json").write_text("{not json")
    assert cli.cmd_verify(_Args()) == 1


# -- small helpers ----------------------------------------------------------------

def test_read_pid_bad_or_missing_content_is_none(tmp_path):
    missing = tmp_path / "nope.pid"
    assert cli._read_pid(missing) is None
    bad = tmp_path / "bad.pid"
    bad.write_text("not-a-pid")
    assert cli._read_pid(bad) is None
    good = tmp_path / "good.pid"
    good.write_text(" 1234\n")
    assert cli._read_pid(good) == 1234


def test_alive_handles_none_and_dead_pids():
    assert cli._alive(None) is False
    assert cli._alive(0) is False


def test_main_dispatches_parsed_args(monkeypatch, capsys):
    monkeypatch.setattr(cli.ctl, "call_sync", lambda *a, **kw: {"pong": True})
    assert cli.main(["ping"]) == 0
