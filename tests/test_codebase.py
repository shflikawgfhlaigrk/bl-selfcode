"""Codebase ingest — the MIG-3 atomic-swap contract.

The old ingest DELETEd every ``source='code'`` row on a SEPARATE autocommit connection
BEFORE any insert, so a crash mid-ingest left the brain with ZERO code self-knowledge
until the next daily cron (and a partial run left a silent subset). Pinned here:

* input validation — unreadable / empty / mid-edit files skipped + counted honestly;
* atomic swap — delete + reinsert in ONE transaction; a crash at ANY point (embed,
  mid-insert) leaves the PREVIOUS index intact (proven on fakes AND on real Postgres);
* post-commit reconciliation — count mismatch -> failures.record + ``ok: False``;
* idempotent re-run — same tree -> same rows, no dupes;
* never-raises cron boundary with no swallowed exceptions on the primary path.

Real-PG tests auto-skip without ``UTAH_TEST_DSN`` (a disposable db — they truncate).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from utah import codebase, config, failures

DIM = config.EMBED_DIM


def _vec(_text: str) -> list[float]:
    return [1.0] + [0.0] * (DIM - 1)


def _tree(tmp_path: Path) -> Path:
    """A tiny package: 2 good modules -> deterministic chunk keys."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "mod_a.py").write_text(
        '"""Module A."""\n\nCONST = 7\nANN: int = 9\n\n\ndef foo():\n    return CONST\n'
    )
    (root / "mod_b.py").write_text('class Bar:\n    """Bar."""\n')
    return root


class FakeCodeTable:
    """The ``source='code'`` slice of the memory table, with the real swap's
    all-or-nothing semantics: a raising swap mutates NOTHING (the transaction
    rolled back), a successful swap replaces everything."""

    def __init__(self) -> None:
        self.rows: list[codebase.CodeRow] = []
        self.fail_swap = False
        self.swap_calls = 0

    def swap(self, new_rows):
        self.swap_calls += 1
        if self.fail_swap:
            raise RuntimeError("injected swap crash")
        staged = list(new_rows)
        cleared = len(self.rows)
        self.rows = staged
        return cleared

    def counts(self) -> tuple[int, dict[str, int]]:
        per: dict[str, int] = {}
        for _content, tags, _conf, _emb in self.rows:
            per[tags[0]] = per.get(tags[0], 0) + 1
        return sum(per.values()), per

    def keys(self) -> list[tuple[str, str]]:
        return sorted((tags[0], tags[1]) for _c, tags, _cf, _e in self.rows)


def _previous_index() -> list[codebase.CodeRow]:
    return [("FILE pkg/old.py\nyesterday", ["pkg/old.py", "pkg/old.py::module"], 1.0, _vec(""))]


def _kinds() -> list[str]:
    return [row.kind for row in failures.recent(20)]


# ---------------------------------------------------------------------------
# happy path + chunk-key determinism (the idempotency anchor)
# ---------------------------------------------------------------------------

def test_ingest_happy_path_atomic_contract(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()
    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=table.counts)
    assert res["ok"] is True and res["error"] is None
    assert res["files"] == 2
    # mod_a: module + CONST + ANN + foo = 4; mod_b: module + Bar = 2
    assert res["chunks"] == 6 and len(table.rows) == 6
    assert res["cleared"] == 0
    assert res["skipped"] == {"unreadable": 0, "empty": 0, "syntax": 0}
    assert table.keys() == [
        ("pkg/mod_a.py", "pkg/mod_a.py::ANN"),
        ("pkg/mod_a.py", "pkg/mod_a.py::CONST"),
        ("pkg/mod_a.py", "pkg/mod_a.py::foo"),
        ("pkg/mod_a.py", "pkg/mod_a.py::module"),
        ("pkg/mod_b.py", "pkg/mod_b.py::Bar"),
        ("pkg/mod_b.py", "pkg/mod_b.py::module"),
    ]


def test_idempotent_rerun_same_rows_no_dupes(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()
    r1 = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=table.counts)
    keys1 = table.keys()
    r2 = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=table.counts)
    assert r1["ok"] is True and r2["ok"] is True
    assert r2["cleared"] == r1["chunks"]          # full swap: prior run fully replaced
    assert r2["chunks"] == r1["chunks"]           # same content -> same row count
    assert table.keys() == keys1                  # same constant per-name keys, no dupes
    assert len(set(table.keys())) == len(table.keys())


# ---------------------------------------------------------------------------
# (a) input validation — skip unreadable/empty/mid-edit files honestly
# ---------------------------------------------------------------------------

def test_validation_skips_unreadable_empty_and_syntax(tmp_path):
    root = _tree(tmp_path)
    (root / "empty.py").write_text("")
    (root / "whitespace.py").write_text("   \n\n\t\n")
    (root / "mid_edit.py").write_text("def broken(:\n")
    unreadable = root / "locked.py"
    unreadable.write_text("SECRET = 1\n")
    if os.geteuid() == 0:  # root reads through chmod 000 — the skip can't be proven
        pytest.skip("running as root: cannot make a file unreadable")
    unreadable.chmod(0o000)
    table = FakeCodeTable()
    try:
        res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap,
                              counts_fn=table.counts)
    finally:
        unreadable.chmod(0o644)
    assert res["ok"] is True
    assert res["skipped"] == {"unreadable": 1, "empty": 2, "syntax": 1}
    assert res["files"] == 2                       # only the two good modules ingested
    ingested = {rel for rel, _ in table.keys()}
    assert ingested == {"pkg/mod_a.py", "pkg/mod_b.py"}


def test_empty_tree_refuses_to_wipe_previous_index(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "empty.py").write_text("")
    table = FakeCodeTable()
    table.rows = _previous_index()
    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=table.counts)
    assert res["ok"] is False and "empty_ingest" in res["error"]
    assert table.swap_calls == 0                   # never touched the DB
    assert table.rows == _previous_index()         # previous index INTACT
    assert "empty_ingest" in _kinds()


# ---------------------------------------------------------------------------
# (f) crash mid-ingest: previous rows must survive
# ---------------------------------------------------------------------------

def test_crash_mid_embed_aborts_before_any_write(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()
    table.rows = _previous_index()
    calls = {"n": 0}

    def flaky_embed(text):
        calls["n"] += 1
        if calls["n"] == 3:                        # partway through the staged chunks
            raise RuntimeError("model died mid-run")
        return _vec(text)

    res = codebase.ingest(root, embed_fn=flaky_embed, swap_fn=table.swap,
                          counts_fn=table.counts)
    assert res["ok"] is False and "embed_failed" in res["error"]
    assert table.swap_calls == 0                   # validation BEFORE the delete
    assert table.rows == _previous_index()         # previous index INTACT
    assert res["chunks"] == 0 and res["cleared"] == 0
    assert "embed_failed" in _kinds()


def test_crash_in_swap_leaves_previous_index(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()
    table.rows = _previous_index()
    table.fail_swap = True
    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=table.counts)
    assert res["ok"] is False and "swap_failed" in res["error"]
    assert table.rows == _previous_index()         # transaction rolled back whole
    assert res["chunks"] == 0 and res["cleared"] == 0
    assert "swap_failed" in _kinds()


# ---------------------------------------------------------------------------
# (c) post-run reconciliation — mismatch is documented + ok False
# ---------------------------------------------------------------------------

def test_reconcile_total_mismatch_is_honest(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()

    def short_counts():
        total, per = table.counts()
        return total - 1, per                      # one committed row "missing"

    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=short_counts)
    assert res["ok"] is False and "reconcile_mismatch" in res["error"]
    assert "reconcile_mismatch" in _kinds()


def test_reconcile_zero_chunks_for_a_file_is_honest(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()

    def dropped_file_counts():
        total, per = table.counts()
        gone = per.pop("pkg/mod_b.py")             # a whole file's chunks vanished
        return total - gone, per

    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap,
                          counts_fn=dropped_file_counts)
    assert res["ok"] is False and "reconcile_mismatch" in res["error"]
    assert "pkg/mod_b.py=0" in res["error"]        # names the offending file
    assert "reconcile_mismatch" in _kinds()


def test_reconcile_read_error_is_honest_not_raised(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()

    def broken_counts():
        raise RuntimeError("postgres went away after commit")

    res = codebase.ingest(root, embed_fn=_vec, swap_fn=table.swap, counts_fn=broken_counts)
    assert res["ok"] is False and "reconcile_error" in res["error"]
    assert "reconcile_error" in _kinds()


# ---------------------------------------------------------------------------
# (e) never-raises cron boundary
# ---------------------------------------------------------------------------

def test_ingest_boundary_never_raises(tmp_path):
    table = FakeCodeTable()

    class ExplodingRoot:                               # collect itself blows up
        def rglob(self, pattern):
            raise OSError("filesystem fell over")

    res = codebase.ingest(ExplodingRoot(), embed_fn=_vec, swap_fn=table.swap,
                          counts_fn=table.counts)
    assert res["ok"] is False and "ingest_failed" in res["error"]
    assert table.swap_calls == 0
    assert "ingest_failed" in _kinds()


def test_bad_swap_return_reported_as_swap_failure(tmp_path):
    root = _tree(tmp_path)
    table = FakeCodeTable()
    res = codebase.ingest(root, embed_fn=_vec,
                          swap_fn=lambda rows: "not-an-int",  # int() blows up inside
                          counts_fn=table.counts)
    assert res["ok"] is False and "swap_failed" in res["error"]
    assert "swap_failed" in _kinds()


# ---------------------------------------------------------------------------
# real Postgres: the actual transaction must roll back whole (auto-skip w/o DSN)
# ---------------------------------------------------------------------------

DSN = os.environ.get("UTAH_TEST_DSN", "")


def _pg_available() -> bool:
    if not DSN:
        return False
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
        return True
    except Exception:
        return False


_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="no vector-enabled Postgres (set UTAH_TEST_DSN to a disposable db)",
)


@pytest.fixture
def pg_dsn(monkeypatch):
    """Point codebase's pooled swap/counts at the disposable test db, clean slate."""
    from utah.memory import PostgresStore

    monkeypatch.setattr(config, "DB_DSN", DSN)
    store = PostgresStore(DSN)
    store.init_schema()
    with store._tx() as conn:
        conn.execute("TRUNCATE memory, entity, mem_entity RESTART IDENTITY")
    return store


def _pg_rows(n: int) -> list[codebase.CodeRow]:
    return [(f"FILE pkg/m{i % 2}.py — s{i}\ncode {i}",
             [f"pkg/m{i % 2}.py", f"pkg/m{i % 2}.py::s{i}"], 1.0, _vec("")) for i in range(n)]


@_pg
def test_real_pg_crash_mid_insert_rolls_back_whole_swap(pg_dsn):
    assert codebase._atomic_swap(_pg_rows(3)) == 0       # seed yesterday's index
    before = codebase._code_counts()
    assert before[0] == 3

    def poisoned():
        yield from _pg_rows(2)                            # delete + 2 inserts happen…
        raise RuntimeError("simulated crash mid-ingest")  # …then the process "dies"

    with pytest.raises(RuntimeError):
        codebase._atomic_swap(poisoned())
    assert codebase._code_counts() == before              # previous index INTACT


@_pg
def test_real_pg_ingest_end_to_end_idempotent_and_scoped(pg_dsn, tmp_path):
    with pg_dsn._tx() as conn:                            # a non-code row must survive
        conn.execute("INSERT INTO memory (content, source, confidence) "
                     "VALUES ('a real fact', 'fact', 0.6)")
    root = _tree(tmp_path)
    r1 = codebase.ingest(root, embed_fn=_vec)             # real swap + real reconcile
    assert r1["ok"] is True and r1["cleared"] == 0 and r1["chunks"] == 6
    r2 = codebase.ingest(root, embed_fn=_vec)
    assert r2["ok"] is True
    assert r2["cleared"] == r1["chunks"] and r2["chunks"] == r1["chunks"]  # idempotent
    total, per_file = codebase._code_counts()
    assert total == 6 and per_file == {"pkg/mod_a.py": 4, "pkg/mod_b.py": 2}
    with pg_dsn._tx() as conn:
        kept = conn.execute(
            "SELECT count(*) FROM memory WHERE source='fact'").fetchone()[0]
    assert int(kept) == 1                                 # swap touched ONLY code rows
