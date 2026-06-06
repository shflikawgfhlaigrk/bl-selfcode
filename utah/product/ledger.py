"""Revenue ledger — Postgres backbone for leads / probate / outreach / fires.

UNIQUE constraints are the product's integrity: **never the same lead twice,
never email a prospect twice** (CAN-SPAM suppression baked into the schema, not
bolted on). Every successful write returns whether it was new and publishes to
the matching dashboard channel, so the deck's revenue panels light up by push
the instant a real artifact lands. One ``msgspec``-shaped row per concept; the
primary store is Postgres (MVCC, concurrent pipelines), analytics via DuckDB.
"""
from __future__ import annotations

import json
from typing import Callable

import psycopg

from utah import UtahError, config

Publisher = Callable[[str, dict], object]  # e.g. bus.publish or the daemon RPC


class LedgerError(UtahError):
    """The ledger store could not be reached or a statement failed."""


_DDL = """
CREATE TABLE IF NOT EXISTS leads (
  id bigserial PRIMARY KEY,
  name text NOT NULL, kind text NOT NULL, region text NOT NULL,
  source text NOT NULL, contact jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'new', ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (name, region)                       -- never the same lead twice
);
CREATE TABLE IF NOT EXISTS probate (
  id bigserial PRIMARY KEY,
  case_name text NOT NULL, county text NOT NULL, filed date,
  heir_contact jsonb NOT NULL DEFAULT '{}'::jsonb, arv numeric,
  status text NOT NULL DEFAULT 'new', ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_name, county)
);
CREATE TABLE IF NOT EXISTS outreach_ledger (
  id bigserial PRIMARY KEY,
  recipient text NOT NULL, campaign text NOT NULL, channel text NOT NULL,
  ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (recipient, campaign)                -- never email a prospect twice
);
CREATE TABLE IF NOT EXISTS fires (
  id bigserial PRIMARY KEY,
  engine text NOT NULL, direction text NOT NULL, entry numeric,
  ts timestamptz NOT NULL DEFAULT now(), outcome text, pnl numeric,
  synthetic boolean NOT NULL DEFAULT false   -- real engine.fired only on the board
);
"""


class Ledger:
    def __init__(self, dsn: str | None = None, publish: Publisher | None = None) -> None:
        self._dsn = dsn if dsn is not None else config.DB_DSN
        self._publish = publish

    def _conn(self):
        try:
            return psycopg.connect(self._dsn, autocommit=True)
        except psycopg.Error as exc:
            raise LedgerError(f"ledger store unreachable: {exc}") from exc

    def init_schema(self) -> None:
        with self._conn() as c:
            c.execute(_DDL)

    def _emit(self, channel: str, event: dict) -> None:
        if self._publish is not None:
            try:
                self._publish(channel, event)
            except Exception:  # the surface must never break a write
                pass

    def record_lead(self, name, kind, region, source, contact=None) -> bool:
        """Insert a lead; return True if new (False = already had it). Never-twice."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO leads (name, kind, region, source, contact) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name, region) DO NOTHING RETURNING id",
                (name, kind, region, source, json.dumps(contact or {})),
            ).fetchone()
        if row:
            self._emit("leads", {"name": name, "kind": kind, "region": region, "id": row[0]})
        return bool(row)

    def record_probate(self, case_name, county, filed=None, heir_contact=None, arv=None) -> bool:
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO probate (case_name, county, filed, heir_contact, arv) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (case_name, county) DO NOTHING RETURNING id",
                (case_name, county, filed, json.dumps(heir_contact or {}), arv),
            ).fetchone()
        if row:
            self._emit("probate", {"case_name": case_name, "county": county, "id": row[0]})
        return bool(row)

    def log_outreach(self, recipient, campaign, channel="email") -> bool:
        """Record an outreach attempt. Returns True if it may send now, False if
        this prospect was already contacted for this campaign (suppression)."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO outreach_ledger (recipient, campaign, channel) "
                "VALUES (%s,%s,%s) ON CONFLICT (recipient, campaign) DO NOTHING RETURNING id",
                (recipient, campaign, channel),
            ).fetchone()
        if row:
            self._emit("outreach", {"recipient": recipient, "campaign": campaign, "id": row[0]})
        return bool(row)

    def record_fire(self, engine, direction, entry=None, synthetic=False) -> int:
        """Record an engine fire (real only on the board; synthetic flagged)."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO fires (engine, direction, entry, synthetic) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (engine, direction, entry, synthetic),
            ).fetchone()
        if not synthetic:
            self._emit("trading", {"engine": engine, "direction": direction, "id": row[0]})
        return int(row[0])

    def counts(self) -> dict:
        with self._conn() as c:
            return {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("leads", "probate", "outreach_ledger", "fires")
            }
