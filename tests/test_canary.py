"""The canary: continuous self-proof. Proven-once rots (2026-06-11: deck blanking,
dead feed-heal paths, an orphan Sovereign daemon — all 'proven live' earlier);
the canary re-earns green every 10 minutes and records/pages when it can't."""
from __future__ import annotations

import datetime

from utah import canary


# ── futures session clock (ticks are only judged in session) ─────────────────
def _ct(wd_name: str, hh: int, mm: int = 0) -> datetime.datetime:
    # 2026-06-08 was a Monday; offset to the named weekday.
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(wd_name)
    return datetime.datetime(2026, 6, 8 + days, hh, mm)


def test_session_closed_saturday_all_day():
    assert canary.futures_session_open(_ct("sat", 12)) is False


def test_session_sunday_opens_at_17():
    assert canary.futures_session_open(_ct("sun", 16, 59)) is False
    assert canary.futures_session_open(_ct("sun", 17, 1)) is True


def test_session_friday_closes_at_16():
    assert canary.futures_session_open(_ct("fri", 15, 59)) is True
    assert canary.futures_session_open(_ct("fri", 16, 1)) is False


def test_session_daily_maintenance_halt():
    assert canary.futures_session_open(_ct("wed", 16, 30)) is False
    assert canary.futures_session_open(_ct("wed", 12)) is True
    assert canary.futures_session_open(_ct("wed", 17, 1)) is True


# ── run_scheduled: aggregate, record, page — never raise ─────────────────────
def test_all_green_records_nothing(monkeypatch):
    recorded = []
    monkeypatch.setattr(canary.failures, "record",
                        lambda *a: recorded.append(a))
    r = canary.run_scheduled({"a": lambda: (True, "fine"),
                              "b": lambda: (True, "also fine")})
    assert r["ok"] is True and r["failed"] == [] and recorded == []


def test_failures_are_recorded_and_paged_once(monkeypatch):
    recorded, paged = [], []
    monkeypatch.setattr(canary.failures, "record",
                        lambda *a: recorded.append(a))
    import utah.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "critical_async",
                        lambda source, detail, *, key=None: paged.append((source, key)))
    r = canary.run_scheduled({"deck": lambda: (False, "silent blank is back"),
                              "mail": lambda: (True, "ok")})
    assert r["ok"] is False and r["failed"] == ["deck"]
    assert recorded == [("canary", "deck", "silent blank is back")]
    assert paged == [("canary", "canary:deck")]   # one page, dedup-keyed


def test_probe_crash_is_a_failure_not_an_exception(monkeypatch):
    monkeypatch.setattr(canary.failures, "record", lambda *a: None)

    def boom():
        raise RuntimeError("probe target unreachable")

    r = canary.run_scheduled({"x": boom})
    assert r["failed"] == ["x"]
    assert "probe crashed" in r["results"]["x"]["detail"]


# ── drift detector (wired as a canary check) ─────────────────────────────────
def test_drift_scan_contract_and_probe_crash_is_a_finding(monkeypatch):
    from utah import drift

    ok, detail = drift.scan()
    assert isinstance(ok, bool) and isinstance(detail, str) and detail

    def boom():
        raise RuntimeError("probe broke")
    monkeypatch.setattr(drift, "plist_drift", boom)
    ok2, detail2 = drift.scan()
    assert ok2 is False and "crashed" in detail2 and "probe broke" in detail2


def test_drift_plist_mismatch_is_reported(tmp_path, monkeypatch):
    from utah import drift

    repo = tmp_path / "repo"; (repo / "ops" / "launchd").mkdir(parents=True)
    agents = tmp_path / "agents"; agents.mkdir()
    (repo / "ops" / "launchd" / "com.utah.x.plist").write_text("<plist>A</plist>")
    (agents / "com.utah.x.plist").write_text("<plist>B</plist>")     # drifted
    (repo / "ops" / "launchd" / "com.utah.y.plist").write_text("<p/>")  # not installed
    # plist_drift takes repo/agents as kwargs (defaults bound at def-time), so inject
    # directly — monkeypatching the module globals no longer reaches the default args.
    out = drift.plist_drift(repo=repo, agents=agents)
    assert any("differs from repo" in f for f in out)
    assert any("NOT installed" in f for f in out)


# ── environment fence: sustained foreign CPU hogs ────────────────────────────
def test_hog_needs_two_consecutive_sightings(tmp_path, monkeypatch):
    """One busy build is fine; a core camped across 2+ canary runs (the 20h
    Claude Desktop pin that starved voice) must be called out."""
    monkeypatch.setattr(canary, "_HOG_STATE", tmp_path / "hogs.json")
    ps = lambda: [(111, 99.0, "/Apps/SomeApp"), (222, 5.0, "/bin/idle"),
                  (333, 98.0, "/Users/x/.utah/UtahVoice")]   # utah excluded

    ok1, d1 = canary.check_hogs(ps_fn=ps)
    assert ok1 is True and "none sustained" in d1            # first sighting: armed

    ok2, d2 = canary.check_hogs(ps_fn=ps)
    assert ok2 is False and "pid 111" in d2                  # second: sustained
    assert "333" not in d2                                   # utah's own never flagged


def test_hog_clears_when_process_calms_down(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "_HOG_STATE", tmp_path / "hogs.json")
    canary.check_hogs(ps_fn=lambda: [(111, 99.0, "/Apps/SomeApp")])
    ok, detail = canary.check_hogs(ps_fn=lambda: [(111, 3.0, "/Apps/SomeApp")])
    assert ok is True
