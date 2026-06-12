"""PostgresStore query-shape + clamp tests — hermetic (fake pool, no live Postgres).

Complements test_memory_store.py (transaction/error contract) by pinning the SHAPE of
every remaining read/write: row construction from positional SELECTs, the empty-input
short-circuits that must never touch Postgres, the deck drill-down clamp constant, and
the SQL guards (mark_promoted's double-tag guard, apply_decay's honest rowcount).
"""
from __future__ import annotations

from tests.test_memory_store import _FakeConn, _FakePool, _Result, _store
from utah import config, entities
from utah.memory.store import _LIST_MAX


# --- clamp constant: the deck drill-down ceiling is the module's, not a magic number ---

def test_hostile_limit_clamps_to_the_module_ceiling():
    conn = _FakeConn(results=[_Result()])
    _store(_FakePool(conn=conn)).list_memories(limit=10**9, offset=0)
    _, params = conn.executed[0]
    assert params[0] == _LIST_MAX             # exactly the env-tunable ceiling

def test_default_limits_pass_through_unclamped():
    conn = _FakeConn(results=[_Result()])
    _store(_FakePool(conn=conn)).list_memories()
    assert conn.executed[0][1] == (50, 0)


# --- search lanes: row shapes + parameterization --------------------------------

def test_dense_search_builds_rows_in_select_order():
    conn = _FakeConn(results=[_Result([(4, "content", "fact", 0.83)])])
    rows = _store(_FakePool(conn=conn)).dense_search([0.0] * config.EMBED_DIM, limit=3)
    assert [(r.id, r.content, r.source) for r in rows] == [(4, "content", "fact")]
    assert rows[0].sim == 0.83
    assert conn.executed[0][1][-1] == 3       # limit parameterized


def test_curated_search_empty_sources_short_circuits_without_sql():
    conn = _FakeConn()
    pool = _FakePool(conn=conn)
    assert _store(pool).curated_search([0.0] * config.EMBED_DIM, 5, sources=[]) == []
    assert conn.executed == []                # never touches Postgres


def test_curated_search_parameterizes_the_source_filter():
    conn = _FakeConn(results=[_Result([(9, "identity", "core", 0.99)])])
    rows = _store(_FakePool(conn=conn)).curated_search(
        [0.0] * config.EMBED_DIM, 2, sources=("core", "knowledge"))
    assert rows[0].source == "core"
    sql, params = conn.executed[0]
    assert "source = ANY(%s)" in sql
    assert ["core", "knowledge"] in list(params)   # passed as a list param, never inlined


def test_sparse_search_builds_rows_and_passes_the_query_twice():
    conn = _FakeConn(results=[_Result([(2, "fts row", "turn")])])
    rows = _store(_FakePool(conn=conn)).sparse_search("newnan ga", limit=6)
    assert [(r.id, r.content, r.source) for r in rows] == [(2, "fts row", "turn")]
    _, params = conn.executed[0]
    assert params == ("newnan ga", "newnan ga", 6)  # match + rank use the SAME query


# --- consolidation/identity reads -----------------------------------------------

def test_unpromoted_turns_shapes_id_content_pairs():
    conn = _FakeConn(results=[_Result([(8, "Q: hi A: hello")])])
    rows = _store(_FakePool(conn=conn)).unpromoted_turns(limit=10)
    assert rows == [(8, "Q: hi A: hello")]
    assert isinstance(rows[0][0], int)


def test_core_rows_shapes_triples():
    conn = _FakeConn(results=[_Result([(1, "Ace and Michael are one", "core")])])
    assert _store(_FakePool(conn=conn)).core_rows() == [
        (1, "Ace and Michael are one", "core")]


def test_entity_names_normalizes_and_groups_per_memory():
    conn = _FakeConn(results=[_Result([(1, "Newnan"), (1, "GEORGIA"), (2, "Newnan")])])
    out = _store(_FakePool(conn=conn)).entity_names([1, 2])
    assert out[1] == {entities.normalize("Newnan"), entities.normalize("GEORGIA")}
    assert out[2] == {entities.normalize("Newnan")}


# --- writes: SQL guards ----------------------------------------------------------

def test_mark_promoted_sql_refuses_a_double_tag():
    conn = _FakeConn()
    _store(_FakePool(conn=conn)).mark_promoted(5)
    sql, params = conn.executed[0]
    assert "NOT ('promoted' = ANY(tags))" in sql   # idempotent: never tags twice
    assert params == (5,)


def test_insert_without_supersede_or_entities_is_one_statement():
    conn = _FakeConn(results=[_Result([(21,)])])
    mem_id = _store(_FakePool(conn=conn)).insert(
        content="c", source="fact", tags=[], confidence=0.7,
        embedding=[0.0] * config.EMBED_DIM, entity_names=[], supersede_ids=[])
    assert mem_id == 21
    assert len(conn.executed) == 1            # no spurious UPDATE/entity statements


def test_apply_decay_returns_the_real_archived_rowcount():
    conn = _FakeConn(results=[_Result(), _Result(rowcount=7)])
    assert _store(_FakePool(conn=conn)).apply_decay() == 7


def test_apply_decay_reports_zero_when_driver_gives_no_rowcount():
    conn = _FakeConn(results=[_Result(), _Result(rowcount=None)])
    assert _store(_FakePool(conn=conn)).apply_decay() == 0
