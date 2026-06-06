"""Integration tests against a REAL vector-enabled Postgres.

Auto-skip when no server is reachable. Point ``UTAH_TEST_DSN`` at a database
that allows ``CREATE EXTENSION vector`` (NEVER the production utah db — these
tests truncate tables). The embedder/reranker stay fake (deterministic vectors;
no model downloads) — what is under test here is the STORE: schema, SQL,
transactions, supersede atomicity, FTS, decay SQL parity.

On Michael's Mac:  UTAH_TEST_DSN="host=/tmp port=5433 dbname=utah_test" pytest
"""
from __future__ import annotations

import os

import pytest

from utah import config, memory
from utah.memory import PostgresStore
from tests.fakes import basis, blend

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


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="no vector-enabled Postgres (set UTAH_TEST_DSN to a disposable db)",
)


@pytest.fixture
def pg(fake_embedder, zero_reranker) -> PostgresStore:
    store = PostgresStore(DSN)
    memory.set_backend(store)
    store.init_schema()
    with store._tx() as conn:
        conn.execute("TRUNCATE memory, entity, mem_entity RESTART IDENTITY")
    yield store
    memory.set_backend(None)


def _row(store: PostgresStore, mem_id: int):
    with store._tx() as conn:
        return conn.execute(
            "SELECT content, source, superseded_by, archived, reinforcement, "
            "decay_score FROM memory WHERE id = %s",
            (mem_id,),
        ).fetchone()


def test_schema_init_is_idempotent(pg):
    pg.init_schema()
    pg.init_schema()  # second run must be a no-op, not an error


def test_store_and_recall_roundtrip(pg, fake_embedder):
    fake_embedder.register("Michael lives in Utah", basis(0))
    fake_embedder.register("Where does Michael live?", blend(basis(0), basis(1), 0.9))
    memory.store("Michael lives in Utah", source="fact", confidence=0.8)
    hits = memory.recall("Where does Michael live?")
    assert hits and hits[0].content == "Michael lives in Utah"
    assert hits[0].sim == pytest.approx(0.9, abs=0.01)


def test_newman_regression_live(pg, fake_embedder):
    """The Newman bug against real Postgres: entity-path supersede + recall."""
    fake_embedder.register("Michael lives in Gulf Shores", basis(0))
    fake_embedder.register("Michael lives in Utah", blend(basis(0), basis(1), 0.85))
    fake_embedder.register(
        "Where does Michael live?", blend(blend(basis(0), basis(1), 0.85), basis(2), 0.9)
    )

    old = memory.store("Michael lives in Gulf Shores", source="fact", confidence=0.8)
    new = memory.store("Michael lives in Utah", source="fact", confidence=0.8)

    content, source, superseded_by, archived, _, _ = _row(pg, old.id)
    assert superseded_by == new.id
    assert archived is True

    contents = [h.content for h in memory.recall("Where does Michael live?")]
    assert "Michael lives in Utah" in contents
    assert "Michael lives in Gulf Shores" not in contents

    answer, _ = memory.answer("Where does Michael live?")
    assert answer == "Michael lives in Utah"


def test_dedup_reinforces_live(pg):
    first = memory.store("Michael prefers tea", source="fact")
    second = memory.store("Michael prefers tea", source="fact")
    assert second.id == first.id
    assert _row(pg, first.id)[4] == 2  # reinforcement


def test_sparse_lane_real_fts(pg, fake_embedder):
    """Postgres FTS finds a lexical match even with an orthogonal query vector."""
    fake_embedder.register("the vault passphrase is kiwi-canyon", basis(0))
    fake_embedder.register("vault passphrase", basis(5))
    memory.store("the vault passphrase is kiwi-canyon", source="fact")
    hits = memory.recall("vault passphrase")
    assert [h.content for h in hits] == ["the vault passphrase is kiwi-canyon"]


def test_entity_graph_rows_written(pg):
    result = memory.store("Michael lives in Utah", source="fact")
    names = pg.entity_names([result.id])[result.id]
    assert names == {"michael", "utah"}


def test_decay_sql_matches_pure_function(pg):
    """The SQL decay formula and compute_decay must agree (parity check)."""
    result = memory.store("Q: old\nA: stale exchange", source="turn", confidence=0.5)
    with pg._tx() as conn:
        conn.execute(
            "UPDATE memory SET ts = now() - interval '90 days' WHERE id = %s",
            (result.id,),
        )
    archived = memory.decay()
    assert archived == 1
    _, _, _, is_archived, reinforcement, decay_score = _row(pg, result.id)
    assert is_archived is True
    expected = memory.compute_decay(90 * 86_400, reinforcement, 0.5)
    assert decay_score == pytest.approx(expected, abs=1e-3)


def test_decay_protects_facts_live(pg):
    result = memory.store("Michael lives in Utah", source="fact", confidence=0.5)
    with pg._tx() as conn:
        conn.execute(
            "UPDATE memory SET ts = now() - interval '90 days' WHERE id = %s",
            (result.id,),
        )
    memory.decay()
    assert _row(pg, result.id)[3] is False  # not archived


def test_consolidation_provenance_live(pg, fake_brain, fake_embedder):
    from utah.consolidate import consolidate

    memory.store("Q: I prefer tea\nA: noted", source="turn", confidence=0.5)
    fake_brain.respond = '["Michael prefers tea"]'
    report = consolidate()
    assert report.facts_promoted == 1
    with pg._tx() as conn:
        row = conn.execute(
            "SELECT source FROM memory WHERE content = %s", ("Michael prefers tea",)
        ).fetchone()
    assert row == ("consolidation",)


def test_unavailable_store_is_structured():
    bad = PostgresStore("host=/nonexistent port=1 dbname=nope")
    with pytest.raises(memory.MemoryUnavailable):
        bad.nearest([0.0] * config.EMBED_DIM, 1)


def test_reset_schema_clean_slate(pg):
    memory.store("Michael lives in Utah", source="fact")
    pg.reset_schema()
    assert memory.recall("Michael") == []
