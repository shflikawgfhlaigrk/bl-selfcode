"""Restart drill: 'works until restarted' is the killer class — the drill makes
restart survival a measured property (kickstart → SLO clock → loud failure)."""
from __future__ import annotations

import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "ops_drill", pathlib.Path(__file__).resolve().parents[1] / "ops" / "drill.py")
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def test_await_passes_fast_probe():
    ok, elapsed, detail = drill._await(lambda: (True, "up"), deadline_s=5, interval=0.01)
    assert ok is True and detail == "up" and elapsed < 1


def test_await_times_out_and_keeps_last_detail():
    ok, elapsed, detail = drill._await(lambda: (False, "still booting"),
                                       deadline_s=0.05, interval=0.01)
    assert ok is False and detail == "still booting"


def test_await_polls_through_boot_window_exceptions():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection refused")   # service still booting
        return True, "recovered"

    ok, _, detail = drill._await(probe, deadline_s=5, interval=0.01)
    assert ok is True and detail == "recovered"


def test_run_holds_services_to_slo_and_reports():
    kicked = []
    drills = [
        ("svc.good", [("comes back", lambda: (True, "up"), 5)]),
        ("svc.bad", [("comes back", lambda: (False, "dead"), 0.05)]),
    ]
    r = drill.run(drills, kick=kicked.append)
    assert kicked == ["svc.good", "svc.bad"]
    assert r["ok"] is False and r["failed"] == ["svc.bad/comes back"]
    by = {(x["service"], x["check"]): x for x in r["results"]}
    assert by[("svc.good", "comes back")]["ok"] is True
    assert by[("svc.bad", "comes back")]["ok"] is False


def test_run_counts_unkickable_service_as_failure():
    def kick(label):
        raise RuntimeError("no such service")

    r = drill.run([("svc.gone", [("up", lambda: (True, ""), 1)])], kick=kick)
    assert r["ok"] is False and r["failed"] == ["svc.gone/kickstart"]
