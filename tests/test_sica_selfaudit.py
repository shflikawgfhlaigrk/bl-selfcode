"""Self-audit lens tests — pure scanners on a synthetic tree (tmp_path), no repo reads."""
from __future__ import annotations

from utah import sica_selfaudit


def _tree(tmp_path):
    pkg = tmp_path / "utah"
    pkg.mkdir()
    (pkg / "alpha.py").write_text(
        "def f():\n    # TODO: tighten the rms floor here\n    return 1\n")
    (pkg / "beta.py").write_text("x = 1\n" * 700)          # over the 650 ceiling
    (pkg / "gamma.py").write_text("def g():\n    return 2\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_alpha.py").write_text("from utah import alpha\n")
    return pkg, tests


def test_todo_lens_finds_marker_with_location(tmp_path):
    pkg, _ = _tree(tmp_path)
    out = sica_selfaudit.todo_findings(pkg)
    assert len(out) == 1 and "alpha.py:2" in out[0] and "tighten the rms floor" in out[0]


def test_untested_lens_skips_covered_modules(tmp_path):
    pkg, tests = _tree(tmp_path)
    out = sica_selfaudit.untested_findings(pkg, tests_dir=tests)
    joined = " ".join(out)
    assert "gamma" in joined and "beta" in joined and "alpha" not in joined


def test_oversize_lens_flags_only_big_files(tmp_path):
    pkg, _ = _tree(tmp_path)
    out = sica_selfaudit.oversize_findings(pkg)
    assert len(out) == 1 and "beta.py" in out[0] and "700 lines" in out[0]


def test_run_files_dedup_aware_and_never_raises(tmp_path):
    pkg, tests = _tree(tmp_path)
    filed: list[str] = []

    def fake_file(task):
        first = task not in filed
        filed.append(task)
        return {"filed": first}

    r = sica_selfaudit.run(root=pkg, file_fn=fake_file, harvest=False)
    assert r["found"] >= 3 and r["filed_new"] == r["found"]
    r2 = sica_selfaudit.run(root=pkg, file_fn=fake_file, harvest=False)
    assert r2["filed_new"] == 0                      # dedup: second sweep files nothing


def test_caps_bound_the_flood(tmp_path):
    pkg = tmp_path / "utah"
    pkg.mkdir()
    for i in range(10):
        (pkg / f"m{i}.py").write_text(f"# TODO: thing {i}\n")
    assert len(sica_selfaudit.todo_findings(pkg)) == 3   # cap honored
