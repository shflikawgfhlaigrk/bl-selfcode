"""Utah storage tiers.

Postgres + pgvector is the PRIMARY store of record (OLTP + vectors + FTS + JSONB
+ LISTEN/NOTIFY, MVCC concurrent writers). DuckDB is the OLAP analytics tier —
it reads the primary zero-copy via the postgres scanner; it is never a second
source of truth. SQLite is retired except, at most, tiny local config.
"""
