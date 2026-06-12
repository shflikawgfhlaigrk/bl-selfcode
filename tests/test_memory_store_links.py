"""PostgresStore insert batching + cleanup visibility — hermetic (fake pool).

Complements test_memory_store.py (transaction/error contract) and
test_memory_store_queries.py (query shapes). Pins three things a regrade flagged:
the entity-link write is a CONSTANT number of statements (batched upsert + batched
link, never 1 + 2N round-trips per memory), a putconn failure is logged instead of
vanishing silently (still never raises into the caller), and apply_decay's SQL stays
glued to the exact config constants compute_decay uses (drift tripwire — the live
parity proof is test_integration_pg.test_decay_sql_matches_pure_function).
"""
from __future__ import annotations

import logging

from tests.test_memory_store import _FakeConn, _FakePool, _Result, _store
from utah import config


# --- entity links: batched, deduped, constant statement count ---------------------

def test_insert_links_entities_in_constant_statements():
    # 3 entities, no supersede -> EXACTLY 3 statements: memory insert, one batched
    # entity upsert, one batched link insert. The old loop issued 1 + 2N.
    conn = _FakeConn(results=[_Result([(11,)]), _Result([(1,), (2,), (3,)]), _Result()])
    mem_id = _store(_FakePool(conn=conn)).insert(
        content="c", source="fact", tags=[], confidence=0.7,
        embedding=[0.0] * config.EMBED_DIM,
        entity_names=["Newnan", "Georgia", "Michael"], supersede_ids=[])
    assert mem_id == 11
    assert len(conn.executed) == 3
    ent_sql, ent_params = conn.executed[1]
    assert "INSERT INTO entity" in ent_sql and "DO UPDATE" in ent_sql
    assert ent_params == (["newnan", "georgia", "michael"],)   # normalized, one array param
    link_sql, link_params = conn.executed[2]
    assert "mem_entity" in link_sql and "ON CONFLICT DO NOTHING" in link_sql
    assert link_params == (11, [1, 2, 3])                       # every id linked to mem 11


def test_insert_dedupes_normalized_entity_names():
    # "Newnan" and " NEWNAN " normalize to the same row; a duplicate inside ONE
    # INSERT ... ON CONFLICT DO UPDATE is a Postgres error ("cannot affect row a
    # second time"), so the batch must dedupe (and drop empties) first.
    conn = _FakeConn(results=[_Result([(11,)]), _Result([(1,)]), _Result()])
    _store(_FakePool(conn=conn)).insert(
        content="c", source="fact", tags=[], confidence=0.7,
        embedding=[0.0] * config.EMBED_DIM,
        entity_names=["Newnan", " NEWNAN ", ""], supersede_ids=[])
    assert conn.executed[1][1] == (["newnan"],)


def test_insert_with_only_empty_entity_names_skips_the_entity_statements():
    conn = _FakeConn(results=[_Result([(11,)])])
    _store(_FakePool(conn=conn)).insert(
        content="c", source="fact", tags=[], confidence=0.7,
        embedding=[0.0] * config.EMBED_DIM,
        entity_names=["", "   "], supersede_ids=[])
    assert len(conn.executed) == 1          # nothing to upsert, nothing to link


# --- putconn failure: never raises, but never silent either -----------------------

def test_putconn_failure_is_logged_not_silent(caplog):
    pool = _FakePool(fail_putconn=True)
    with caplog.at_level(logging.DEBUG, logger="utah.memory.store"):
        _store(pool).touch([1])             # must not raise
    assert any("putconn" in r.getMessage() for r in caplog.records), \
        "a failed return-to-pool must leave a log trace, not vanish"


# --- decay mirror tripwire ---------------------------------------------------------

def test_apply_decay_sql_pins_the_compute_decay_constants():
    # compute_decay (utah/memory/logic.py) and this SQL must use the SAME weights in
    # the SAME roles; if someone reorders the params or rewrites the formula shape,
    # this fails before the live parity test ever runs.
    conn = _FakeConn(results=[_Result(), _Result(rowcount=0)])
    _store(_FakePool(conn=conn)).apply_decay()
    decay_sql, decay_params = conn.executed[0]
    assert "exp(" in decay_sql and "ln(1 + reinforcement) / 3.0" in decay_sql
    assert "LEAST(1.0" in decay_sql
    assert decay_params == (config.DECAY_W_RECENCY, config.DECAY_HALFLIFE_SECONDS,
                            config.DECAY_W_FREQUENCY, config.DECAY_W_CONFIDENCE)
    archive_sql, archive_params = conn.executed[1]
    assert "archived = true" in archive_sql
    assert archive_params == (list(config.DECAY_PROTECTED_SOURCES),
                              config.DECAY_ARCHIVE_BELOW, config.DECAY_MIN_AGE_DAYS)
