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
CREATE TABLE IF NOT EXISTS mail_ledger (
  id bigserial PRIMARY KEY,
  recipient text NOT NULL, subject text NOT NULL,
  channel text NOT NULL DEFAULT 'email',
  status text NOT NULL DEFAULT 'sent',        -- sent | gated | failed
  ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (recipient, subject)                 -- never send the same message twice
);
CREATE TABLE IF NOT EXISTS marketer_posts (
  id bigserial PRIMARY KEY,
  channel text NOT NULL,                       -- instagram | tiktok | ...
  caption text NOT NULL, media_ref text NOT NULL,
  subject text NOT NULL DEFAULT '',            -- the business/topic spotlighted
  status text NOT NULL DEFAULT 'posted',       -- posted | gated | failed
  post_id text,                                -- external id when actually posted
  ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (channel, media_ref)                  -- never post the same media to a channel twice
);
CREATE TABLE IF NOT EXISTS sync_log (
  id bigserial PRIMARY KEY,
  source text NOT NULL,                        -- ace_knowledge | web_research | osm_leads | ...
  kind text NOT NULL DEFAULT 'ingest',         -- ingest | refresh
  rows_in integer NOT NULL DEFAULT 0,
  cursor text,                                 -- resumable position (last id / ts / page)
  status text NOT NULL DEFAULT 'ok',           -- ok | partial | failed
  detail text,
  ts timestamptz NOT NULL DEFAULT now()
);                                             -- append-only run log (no UNIQUE; every run is a row)
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

    def update_probate(self, case_name, county, *, heir_contact=None, arv=None,
                       filed=None) -> bool:
        """Write enrichment (resolved property address/parcel/lat/lng/comps in the
        heir_contact jsonb, plus optional arv/filed) onto an EXISTING probate row.
        Returns True if a row was updated. Pushes the probate deck channel so the panel
        refreshes. Never inserts — enrichment only augments what the scout found."""
        sets, vals = [], []
        if heir_contact is not None:
            sets.append("heir_contact=%s"); vals.append(json.dumps(heir_contact))
        if arv is not None:
            sets.append("arv=%s"); vals.append(arv)
        if filed is not None:
            sets.append("filed=%s"); vals.append(filed)
        if not sets:
            return False
        vals += [case_name, county]
        with self._conn() as c:
            row = c.execute(
                f"UPDATE probate SET {', '.join(sets)} WHERE case_name=%s AND county=%s "
                "RETURNING id", tuple(vals),
            ).fetchone()
        if row:
            self._emit("probate", {"case_name": case_name, "county": county,
                                   "id": row[0], "enriched": True})
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

    def is_contacted(self, recipient, campaign) -> bool:
        """Read-only suppression check: was this prospect already contacted for this
        campaign? Lets a send-now path SEND before committing the never-twice row, so a
        gated/failed send never burns the prospect's one shot."""
        with self._conn() as c:
            return c.execute(
                "SELECT 1 FROM outreach_ledger WHERE recipient=%s AND campaign=%s",
                (recipient, campaign)).fetchone() is not None

    def uncontacted_email_leads(self, campaign, limit=8) -> list[dict]:
        """Leads that have a REAL email and have NOT been contacted for *campaign* — the
        suppression-aware queue cold outreach pulls from (newest first). This is what was
        missing: the send path was proven but nothing fed it the live leads."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, kind, region, contact FROM leads "
                "WHERE contact->>'email' IS NOT NULL AND contact->>'email' <> '' "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger o "
                "  WHERE o.recipient = leads.contact->>'email' AND o.campaign = %s) "
                "ORDER BY ts DESC LIMIT %s",
                (campaign, limit),
            ).fetchall()
        return [{"name": r[0], "kind": r[1], "region": r[2], "contact": r[3]} for r in rows]

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

    def record_mail(self, recipient, subject, status="sent", channel="email") -> bool:
        """Record an email attempt; True if new (False = this message already sent).
        Never-twice — the merge's mail/outreach send writes here, real-or-nothing."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO mail_ledger (recipient, subject, channel, status) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (recipient, subject) DO NOTHING RETURNING id",
                (recipient, subject, channel, status),
            ).fetchone()
        if row:
            self._emit("mail", {"recipient": recipient, "subject": subject, "id": row[0]})
        return bool(row)

    def record_post(self, channel, caption, media_ref, subject="", status="posted",
                    post_id=None) -> bool:
        """Record a marketer post; True if new (False = this media already posted to this
        channel). Never-twice — the merge's marketer writes here, real-or-nothing."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO marketer_posts (channel, caption, media_ref, subject, status, post_id) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (channel, media_ref) DO NOTHING RETURNING id",
                (channel, caption, media_ref, subject, status, post_id),
            ).fetchone()
        if row:
            self._emit("marketer", {"channel": channel, "subject": subject, "id": row[0]})
        return bool(row)

    def record_sync(self, source, kind="ingest", rows_in=0, cursor=None,
                    status="ok", detail=None) -> int:
        """Append one ingestion-run row (resumable cursor + counts). Returns the row id.
        The merge's importers (ace_knowledge, web_research, osm_leads) write here so every
        ingest is auditable on the deck — real-or-nothing."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO sync_log (source, kind, rows_in, cursor, status, detail) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, kind, rows_in, cursor, status, detail),
            ).fetchone()
        self._emit("sync", {"source": source, "rows_in": rows_in, "status": status, "id": row[0]})
        return int(row[0])

    def counts(self) -> dict:
        with self._conn() as c:
            return {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("leads", "probate", "outreach_ledger", "fires",
                          "mail_ledger", "marketer_posts", "sync_log")
            }

    #: deck panel domain -> (table, explicit columns). Explicit columns keep the
    #: output JSON-safe (ts as text, no raw datetime/Decimal) and stable.
    _RECENT = {
        "leads": ("leads",
                  "id, name, kind, region, source, status, contact, "
                  "to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "probate": ("probate",
                    "id, case_name, county, status, arv::float8 arv, heir_contact, "
                    "to_char(filed,'YYYY-MM-DD') filed, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "outreach": ("outreach_ledger",
                     "id, recipient, campaign, channel, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "fires": ("fires",
                  "id, engine, direction, outcome, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "mail": ("mail_ledger",
                 "id, recipient, subject, channel, status, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "marketer": ("marketer_posts",
                     "id, channel, subject, status, post_id, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
        "sync": ("sync_log",
                 "id, source, kind, rows_in, status, to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
    }

    def recent(self, domain: str, limit: int = 50) -> list[dict]:
        """Recent rows for a deck revenue panel (leads|probate|outreach|fires).
        Unknown domain -> [] (honest empty, never an error)."""
        spec = self._RECENT.get(domain)
        if spec is None:
            return []
        table, cols = spec
        limit = max(1, min(int(limit), 200))
        with self._conn() as c:
            cur = c.execute(f"SELECT {cols} FROM {table} ORDER BY id DESC LIMIT %s", (limit,))
            names = [d.name for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]


_ledger: Ledger | None = None


def get_ledger(publish: Publisher | None = None) -> Ledger:
    """Process-wide ledger. ``publish`` (e.g. the daemon bus) is bound on first
    construction so every write pushes to the deck."""
    global _ledger
    if _ledger is None:
        _ledger = Ledger(publish=publish)
    return _ledger


def set_ledger(ledger: Ledger | None) -> None:
    global _ledger
    _ledger = ledger
