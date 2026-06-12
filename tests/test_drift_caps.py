"""Drift probe bounds: the conflict-copy sweep stops at a pageable handful
(the cleanup command finds the rest), the unreadable-REPO-side plist is a
finding too, and the per-port connect budget stays canary-cheap."""
from __future__ import annotations

from utah import drift


def test_icloud_conflicts_caps_the_hit_list_at_five(tmp_path):
    (tmp_path / "utah").mkdir()
    for i in range(8):
        (tmp_path / "utah" / f"mod{i} 2.py").write_text("")
    out = drift.icloud_conflicts(repo=tmp_path)
    assert len(out) == 1
    listed = out[0].split(": ", 1)[1].split(", ")
    assert len(listed) == 5  # enough to page on, never an unbounded wall


def test_plist_unreadable_repo_copy_is_a_finding_not_a_crash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ops" / "launchd").mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    locked = repo / "ops" / "launchd" / "com.utah.a.plist"
    locked.write_text("<p/>")
    (agents / "com.utah.a.plist").write_text("<p/>")
    locked.chmod(0o000)
    try:
        out = drift.plist_drift(repo=repo, agents=agents)
        assert any("unreadable" in f for f in out)
    finally:
        locked.chmod(0o644)


def test_port_probe_budget_is_canary_cheap():
    """The drift scan runs inside the canary tick — a black-holed port must cost
    at most the declared budget, and the budget must stay small."""
    assert 0 < drift.PORT_PROBE_TIMEOUT_S <= 2.0


def test_default_probe_set_covers_every_outage_class():
    names = {p.__name__ for p in drift.DEFAULT_PROBES}
    assert names == {"plist_drift", "stale_runtime", "port_squatters",
                     "icloud_conflicts", "empty_secrets"}
