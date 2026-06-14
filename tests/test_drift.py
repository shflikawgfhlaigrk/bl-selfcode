"""Drift probes — each one models a real outage class on this machine. Every
probe must be hermetically testable (injectable roots) and total: a vanished
file or unreadable plist is a finding or a skip, never an unhandled crash."""
from __future__ import annotations

import socket
import time

from utah import drift


# ── plist drift ───────────────────────────────────────────────────────────────

def test_plist_matching_copies_are_clean(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ops" / "launchd").mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    (repo / "ops" / "launchd" / "com.utah.a.plist").write_text("<plist>same</plist>")
    (agents / "com.utah.a.plist").write_text("<plist>same</plist>")
    assert drift.plist_drift(repo=repo, agents=agents) == []


def test_plist_symlink_equivalent_paths_are_not_drift(tmp_path):
    """~/Desktop/ProjectUtah and ~/ProjectUtah spellings must not false-alarm."""
    canonical = tmp_path / "ProjectUtah"
    canonical.mkdir()
    repo = tmp_path / "Desktop" / "ProjectUtah"
    repo.parent.mkdir(parents=True, exist_ok=True)
    repo.symlink_to(canonical)
    (repo / "ops" / "launchd").mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    body = """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.utah.x</string>
  <key>WorkingDirectory</key><string>{wd}</string>
</dict></plist>"""
    (repo / "ops" / "launchd" / "com.utah.x.plist").write_text(
        body.format(wd=str(repo))
    )
    (agents / "com.utah.x.plist").write_text(body.format(wd=str(canonical)))
    assert drift.plist_drift(repo=repo, agents=agents) == []


def test_plist_unreadable_installed_copy_is_a_finding_not_a_crash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ops" / "launchd").mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    (repo / "ops" / "launchd" / "com.utah.a.plist").write_text("<p/>")
    locked = agents / "com.utah.a.plist"
    locked.write_text("<p/>")
    locked.chmod(0o000)
    try:
        out = drift.plist_drift(repo=repo, agents=agents)
        assert any("unreadable" in f for f in out)
    finally:
        locked.chmod(0o644)


# ── stale runtime ─────────────────────────────────────────────────────────────

def test_stale_runtime_no_pidfiles_is_a_finding(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    repo = tmp_path / "repo"
    (repo / "utah").mkdir(parents=True)
    out = drift.stale_runtime(run_dir=run, repo=repo)
    assert out and "supervisor state unknown" in out[0]


def test_stale_runtime_source_newer_than_boot_is_a_finding(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    repo = tmp_path / "repo"
    (repo / "utah").mkdir(parents=True)
    pid = run / "utahd.pid"
    pid.write_text("1")
    old = time.time() - 3600
    import os
    os.utime(pid, (old, old))                       # daemon booted an hour ago
    (repo / "utah" / "new.py").write_text("# fresh")  # source edited just now
    out = drift.stale_runtime(run_dir=run, repo=repo)
    assert out and "restart pending" in out[0]


def test_stale_runtime_source_older_than_boot_is_clean(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    repo = tmp_path / "repo"
    (repo / "utah").mkdir(parents=True)
    src = repo / "utah" / "old.py"
    src.write_text("# old")
    import os
    old = time.time() - 3600
    os.utime(src, (old, old))
    (run / "utahd.pid").write_text("1")  # booted now
    assert drift.stale_runtime(run_dir=run, repo=repo) == []


def test_stale_runtime_grace_suppresses_a_marginal_skew(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    repo = tmp_path / "repo"
    (repo / "utah").mkdir(parents=True)
    (run / "utahd.pid").write_text("1")
    (repo / "utah" / "edge.py").write_text("# written right after boot")
    assert drift.stale_runtime(pidfile_age_grace_s=120.0, run_dir=run, repo=repo) == []


# ── port squatters ────────────────────────────────────────────────────────────

def test_port_open_is_clean_and_closed_is_a_finding():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        live_port = srv.getsockname()[1]
        assert drift.port_squatters(listeners={live_port: "test svc"}) == []
    finally:
        srv.close()
    # now the same port is closed → its owner is down → finding
    out = drift.port_squatters(listeners={live_port: "test svc"})
    assert out and f":{live_port} (test svc) not listening" in out[0]


# ── iCloud conflict copies ────────────────────────────────────────────────────

def test_icloud_conflicts_catches_any_numbered_copy(tmp_path):
    (tmp_path / "utah").mkdir()
    (tmp_path / "utah" / "core 2.py").write_text("")
    (tmp_path / "utah" / "brain 13.py").write_text("")   # `* 2.py` glob missed these
    (tmp_path / "utah" / "fine.py").write_text("")
    out = drift.icloud_conflicts(repo=tmp_path)
    assert len(out) == 1
    assert "core 2.py" in out[0] and "brain 13.py" in out[0]
    assert "fine.py" not in out[0]


def test_icloud_conflicts_skips_venv_and_git(tmp_path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "pkg 2.py").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook 2.py").write_text("")
    assert drift.icloud_conflicts(repo=tmp_path) == []


def test_icloud_conflicts_clean_tree(tmp_path):
    (tmp_path / "utah").mkdir()
    (tmp_path / "utah" / "core.py").write_text("")
    assert drift.icloud_conflicts(repo=tmp_path) == []


# ── empty secrets ─────────────────────────────────────────────────────────────

def test_empty_secret_file_is_a_finding(tmp_path):
    (tmp_path / "gmail.json").write_text("{}")  # < 10 bytes: effectively empty
    out = drift.empty_secrets(secrets_dir=tmp_path)
    assert out and "gmail.json" in out[0]


def test_populated_and_missing_secrets_are_clean(tmp_path):
    (tmp_path / "gmail.json").write_text('{"user": "x", "app_password": "y"}')
    # pushover.json absent: never configured is not drift — only empty-but-present is
    assert drift.empty_secrets(secrets_dir=tmp_path) == []


# ── scan aggregation ──────────────────────────────────────────────────────────

def test_scan_all_clean_reports_ok():
    ok, detail = drift.scan(probes=(lambda: [], lambda: []))
    assert ok is True and "no drift" in detail


def test_scan_joins_findings_and_is_red():
    ok, detail = drift.scan(probes=(lambda: ["finding one"], lambda: ["finding two"]))
    assert ok is False
    assert "finding one" in detail and "finding two" in detail


def test_scan_probe_crash_is_a_finding_not_an_exception():
    def boom():
        raise RuntimeError("probe target unreachable")

    ok, detail = drift.scan(probes=(boom,))
    assert ok is False and "boom crashed" in detail and "probe target unreachable" in detail


def test_scan_detail_is_capped():
    ok, detail = drift.scan(probes=(lambda: ["x" * 2000],))
    assert ok is False and len(detail) <= 400


def test_scan_default_probes_run_against_the_real_machine():
    """The wired contract canary.check_drift depends on: (bool, non-empty str)."""
    ok, detail = drift.scan()
    assert isinstance(ok, bool) and isinstance(detail, str) and detail
