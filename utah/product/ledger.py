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

#: SMB no-website pipeline — OSM + Google Maps trade scout only. Probate heirs live in
#: the ``probate`` table and use :data:`PROBATE_OUTREACH_CAMPAIGN`; they never enter here.
SMB_LEAD_SOURCES: frozenset[str] = frozenset({"osm", "google_maps"})
SMB_OUTREACH_CAMPAIGN = "smb_no_website"
PROBATE_OUTREACH_CAMPAIGN = "probate_motivated"   # separate track — not ``com.utah.outreach``


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
CREATE TABLE IF NOT EXISTS mail_replies (
  id bigserial PRIMARY KEY,
  sender text NOT NULL,                        -- the human (or mailer-daemon) who wrote back
  subject text NOT NULL DEFAULT '',
  kind text NOT NULL,                          -- reply | bounce
  message_id text NOT NULL,                    -- RFC 5322 Message-ID (idempotency across polls)
  snippet text NOT NULL DEFAULT '',
  ts timestamptz NOT NULL DEFAULT now(),
  UNIQUE (message_id)                          -- a message is recorded once, ever
);
CREATE TABLE IF NOT EXISTS bars (
  id bigserial PRIMARY KEY,
  symbol text NOT NULL,                        -- e.g. CM.MNQM6 (the WC feed symbol)
  ts timestamptz NOT NULL,                     -- bar CLOSE time (when its close became real)
  o numeric, h numeric, l numeric,             -- best-effort from intra-bar tick closes
  c numeric NOT NULL,                          -- authoritative bar close (the grading price)
  bar_seconds integer NOT NULL DEFAULT 15,
  ts_recorded timestamptz NOT NULL DEFAULT now(),
  UNIQUE (symbol, ts)                          -- a bar lands once — overlap-safe
);
CREATE UNLOGGED TABLE IF NOT EXISTS wc_live (
  symbol text PRIMARY KEY,                     -- one row per symbol: the LAST tick
  price numeric NOT NULL,
  ts timestamptz NOT NULL,                     -- the tick's (normalized) market time
  recorded timestamptz NOT NULL                -- wall-clock landing time, ms precision
);                                             -- UNLOGGED: a throughput surface, not history
ALTER TABLE fires ADD COLUMN IF NOT EXISTS symbol text;  -- which bar stream grades the fire
ALTER TABLE fires ADD COLUMN IF NOT EXISTS stop numeric;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS target numeric;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS rationale text;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS assessment text;  -- Ace's think-on-fire read
CREATE TABLE IF NOT EXISTS trade_lore (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL,
  engine text NOT NULL,
  kind text,                                   -- OPEN / CLOSE / SIGNAL_CROSSED / ...
  content text NOT NULL,                       -- Ace's historical read (2-3 sentences)
  confidence real,
  ts_recorded timestamptz NOT NULL DEFAULT now(),
  UNIQUE (engine, ts, kind)                    -- one-time migration is re-runnable
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
        try:
            from utah.integrations import discord_feed

            discord_feed.mirror(channel, event)
        except Exception:  # noqa: BLE001 — Discord must never break a write
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

    def update_lead(self, name, region, *, contact=None) -> bool:
        """Merge *contact* fields onto an existing lead (enrichment). Returns True if updated."""
        if not contact:
            return False
        with self._conn() as c:
            row = c.execute(
                "UPDATE leads SET contact = contact || %s::jsonb "
                "WHERE name=%s AND region=%s RETURNING id",
                (json.dumps(contact), name, region),
            ).fetchone()
        if row:
            self._emit("leads", {"name": name, "region": region, "id": row[0], "enriched": True})
        return bool(row)

    def leads_missing_email(self, limit: int = 25) -> list[dict]:
        """SMB leads with a phone but NO email — the email-enrichment candidates. Newest
        first (freshest frontier leads enriched soonest). Never probate."""
        sources = tuple(SMB_LEAD_SOURCES)
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, kind, region, contact, source FROM leads "
                "WHERE source = ANY(%s) "
                "AND contact->>'phone' IS NOT NULL AND contact->>'phone' <> '' "
                "AND (contact->>'email' IS NULL OR contact->>'email' = '') "
                "ORDER BY ts DESC LIMIT %s",
                (list(sources), limit),
            ).fetchall()
        return [{"name": r[0], "kind": r[1], "region": r[2], "contact": r[3],
                 "source": r[4]} for r in rows]

    def has_phone_lead(self, phone: str, source: str = "google_maps") -> bool:
        """True if this phone is already on a lead row for *source* (cross-city dedup)."""
        if not phone:
            return False
        with self._conn() as c:
            return c.execute(
                "SELECT 1 FROM leads WHERE source=%s AND contact->>'phone'=%s LIMIT 1",
                (source, phone),
            ).fetchone() is not None

    def count_maps_phone_leads(self) -> int:
        """Distinct phone numbers on google_maps leads — bulk-ingest progress metric."""
        with self._conn() as c:
            row = c.execute(
                "SELECT count(DISTINCT contact->>'phone') FROM leads "
                "WHERE source='google_maps' AND contact->>'phone' IS NOT NULL "
                "AND contact->>'phone' <> ''",
            ).fetchone()
        return int(row[0] if row else 0)

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
        """Write enrichment (resolved property address/parcel/lat/lng/nearby-business survey in the
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

    def mark_lead_contacted(self, recipient) -> int:
        """Flip matching ``leads`` rows new→contacted AFTER a send actually LANDED.
        Funnel truth: ``leads.status`` mirrors reality (the deck/metrics read it), while
        the never-twice guarantee stays in ``outreach_ledger``. Matches the recipient
        against the contact email OR phone; returns rows flipped (0 = recipient wasn't
        a leads row, e.g. a probate heir or a self-test send — both correct no-ops)."""
        if not recipient:
            return 0
        with self._conn() as c:
            rows = c.execute(
                "UPDATE leads SET status='contacted' "
                "WHERE status='new' AND (contact->>'email'=%s OR contact->>'phone'=%s) "
                "RETURNING id", (recipient, recipient)).fetchall()
        return len(rows)

    def uncontacted_email_leads(self, campaign, limit=8) -> list[dict]:
        """SMB ``leads`` rows with email, not yet contacted — **never** probate heirs."""
        if campaign != SMB_OUTREACH_CAMPAIGN:
            return []
        sources = tuple(SMB_LEAD_SOURCES)
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, kind, region, contact, source FROM leads "
                "WHERE source = ANY(%s) "
                "AND contact->>'email' IS NOT NULL AND contact->>'email' <> '' "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger o "
                "  WHERE o.recipient = leads.contact->>'email' AND o.campaign = %s) "
                "ORDER BY ts DESC LIMIT %s",
                (list(sources), campaign, limit),
            ).fetchall()
        return [{"name": r[0], "kind": r[1], "region": r[2], "contact": r[3],
                 "source": r[4]} for r in rows]

    def uncontacted_phone_leads(self, campaign, limit=8) -> list[dict]:
        """SMB ``leads`` rows with phone, not yet contacted — **never** probate heirs."""
        if campaign != SMB_OUTREACH_CAMPAIGN:
            return []
        sources = tuple(SMB_LEAD_SOURCES)
        with self._conn() as c:
            rows = c.execute(
                "SELECT name, kind, region, contact, source FROM leads "
                "WHERE source = ANY(%s) "
                "AND contact->>'phone' IS NOT NULL AND contact->>'phone' <> '' "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger o "
                "  WHERE o.recipient = leads.contact->>'phone' AND o.campaign = %s) "
                "ORDER BY ts DESC LIMIT %s",
                (list(sources), campaign, limit),
            ).fetchall()
        return [{"name": r[0], "kind": r[1], "region": r[2], "contact": r[3],
                 "source": r[4]} for r in rows]

    def probate_all(self, limit: int = 5000) -> list[dict]:
        """Every probate row, newest first — feeds the sellable lead-list export
        (:mod:`utah.product.probate_export`). Raw shapes; the export labels values
        honestly (assessed, never comps)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT case_name, county, filed, heir_contact, arv, status "
                "FROM probate ORDER BY ts DESC LIMIT %s", (limit,)).fetchall()
        return [{"case_name": r[0], "county": r[1], "filed": r[2],
                 "heir_contact": r[3], "arv": r[4], "status": r[5]} for r in rows]

    def probate_uncontacted_with_mail(self, limit: int = 20) -> list[dict]:
        """Probate rows whose enrichment resolved an owner MAILING address, not yet sent a
        direct-mail letter (suppressed by case_name in the probate campaign). The probate
        direct-mail last-mile draws from this. Reads the ``probate`` table only."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT case_name, county, heir_contact, arv FROM probate "
                "WHERE (coalesce(heir_contact->'owner_mail'->>'street', '') <> '' "
                "    OR coalesce(heir_contact->'situs_mail'->>'street', '') <> '') "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger o "
                "  WHERE o.recipient = probate.case_name AND o.campaign = %s) "
                "ORDER BY ts DESC LIMIT %s",
                (PROBATE_OUTREACH_CAMPAIGN, limit),
            ).fetchall()
        return [{"case_name": r[0], "county": r[1], "heir_contact": r[2] or {}, "arv": r[3]}
                for r in rows]

    def uncontacted_probate_heirs(self, campaign, limit=8) -> list[dict]:
        """Probate heirs with contact info — **separate** from SMB outreach. Reads the
        ``probate`` table only; ``com.utah.outreach`` must never call this."""
        if campaign != PROBATE_OUTREACH_CAMPAIGN:
            return []
        with self._conn() as c:
            rows = c.execute(
                "SELECT case_name, county, heir_contact FROM probate "
                "WHERE (heir_contact->>'phone' IS NOT NULL AND heir_contact->>'phone' <> '' "
                "    OR heir_contact->>'email' IS NOT NULL AND heir_contact->>'email' <> '') "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger o "
                "  WHERE o.recipient = COALESCE(probate.heir_contact->>'email', "
                "                                  probate.heir_contact->>'phone') "
                "  AND o.campaign = %s) "
                "ORDER BY ts DESC LIMIT %s",
                (campaign, limit),
            ).fetchall()
        out: list[dict] = []
        for case_name, county, heir in rows:
            hc = heir or {}
            contact = {k: hc[k] for k in ("phone", "email", "address") if hc.get(k)}
            if not contact:
                continue
            out.append({"name": case_name, "kind": "probate", "region": county,
                        "contact": contact, "source": "probate"})
        return out

    def fire_state(self, engine) -> dict:
        """Open-position + cooldown truth for an engine — the state machine's memory.
        Live 2026-06-10: 749 fires in ONE day because stateless engines re-fired every
        bar a condition persisted (468 'breakout trades' = one trend counted hundreds
        of times). ``open`` = a real fire the grader hasn't closed yet (outcome IS
        NULL), capped at 4h so a dead grader can't blind the engines forever."""
        with self._conn() as c:
            row = c.execute(
                "SELECT extract(epoch FROM now() - max(ts)), "
                "       count(*) FILTER (WHERE outcome IS NULL "
                "                        AND ts > now() - interval '4 hours') "
                "FROM fires WHERE engine=%s AND NOT synthetic", (engine,)).fetchone()
        age = float(row[0]) if row and row[0] is not None else None
        return {"last_fire_age_s": age, "open": bool(row and row[1])}

    def record_fire(self, engine, direction, entry=None, synthetic=False,
                    symbol=None, *, stop=None, target=None, rationale=None) -> int:
        """Record an engine fire (real only on the board; synthetic flagged). ``symbol``
        names the bar stream the fire grader walks to fill outcome/pnl. Optional
        ``stop``/``target``/``rationale`` feed the rich Pushover alert and deck detail."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO fires (engine, direction, entry, synthetic, symbol, "
                "stop, target, rationale) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (engine, direction, entry, synthetic, symbol, stop, target, rationale),
            ).fetchone()
        if not synthetic:
            self._emit("trading", {"engine": engine, "direction": direction, "id": row[0]})
            # The trade stream's single chokepoint: every REAL fire pages Michael's
            # phone (alerts never raises; gates/dedup live in utah/alerts.py).
            from utah import alerts
            alerts.trade_fire(engine, direction, entry, fire_id=int(row[0]),
                              symbol=symbol, stop=stop, target=target,
                              rationale=" ".join(x for x in (symbol, rationale) if x) or None)
        return int(row[0])

    # --- bars + fire grading (the measurability lane: utah/product/fire_grader.py) ---

    def record_tick(self, symbol, price, epoch_s) -> None:
        """Write-through one live tick — the deck's millisecond surface. One row per
        symbol (upsert), ``recorded`` stamped with ``clock_timestamp()`` so the deck can
        show true tick-to-screen age. Tiny and hot-path: called for EVERY feed tick."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO wc_live (symbol, price, ts, recorded) "
                "VALUES (%s, %s, to_timestamp(%s), clock_timestamp()) "
                "ON CONFLICT (symbol) DO UPDATE SET price = EXCLUDED.price, "
                "ts = EXCLUDED.ts, recorded = EXCLUDED.recorded",
                (symbol, price, epoch_s),
            )

    def live_ticks(self) -> list[dict]:
        """The last tick per symbol with millisecond ages — what the Engine Lab's live
        ticker renders. ``age_ms`` = how stale the surface is right now."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, price::float8, ts, recorded, "
                "round(extract(epoch from (clock_timestamp() - recorded)) * 1000) "
                "FROM wc_live ORDER BY symbol",
            ).fetchall()
        return [{"symbol": r[0], "price": r[1], "ts": str(r[2]), "recorded": str(r[3]),
                 "age_ms": int(r[4])} for r in rows]

    def record_bars(self, symbol, bars, bar_seconds=15) -> int:
        """Persist closed bars — ``bars`` is ``[(close_epoch_s, o, h, l, c), ...]``.
        ``ts`` is the bar CLOSE time; never-twice per (symbol, ts) so overlapping
        collection windows can't double-store. Returns how many were NEW."""
        new = 0
        with self._conn() as c:
            for ep, o, h, l, close in bars:
                row = c.execute(
                    "INSERT INTO bars (symbol, ts, o, h, l, c, bar_seconds) "
                    "VALUES (%s, to_timestamp(%s), %s,%s,%s,%s,%s) "
                    "ON CONFLICT (symbol, ts) DO NOTHING RETURNING id",
                    (symbol, ep, o, h, l, close, bar_seconds),
                ).fetchone()
                if row:
                    new += 1
        return new

    def ungraded_fires(self, older_than_minutes=30) -> list[dict]:
        """Real (non-synthetic) fires with no outcome yet, older than the evaluation
        horizon — the grader's worklist, oldest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, engine, direction, entry::float8, ts, symbol FROM fires "
                "WHERE outcome IS NULL AND synthetic = false "
                "AND ts < now() - (%s * interval '1 minute') ORDER BY ts",
                (float(older_than_minutes),),
            ).fetchall()
        return [{"id": r[0], "engine": r[1], "direction": r[2], "entry": r[3],
                 "ts": r[4], "symbol": r[5]} for r in rows]

    def bar_symbols(self, hours: int = 24) -> list[str]:
        """Symbols with bars recorded in the last *hours* — the feed's warm-up roster
        after a restart. Keyed on ts_recorded (arrival), immune to stamp skew."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT symbol FROM bars "
                "WHERE ts_recorded > now() - (%s * interval '1 hour') ORDER BY symbol",
                (int(hours),),
            ).fetchall()
        return [r[0] for r in rows]

    def recent_closes(self, symbol, limit) -> list[tuple[float, float]]:
        """The last *limit* bars as ``[(close_epoch_s, close), ...]`` chronological, in
        ARRIVAL order (ts_recorded, id) — the true sequence even across the 2026-06-10
        legacy rows whose ts carries the +1h exchange-wallclock skew. The feed's
        restart warm-up reader: state is rebuilt from here, never from a state file."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT extract(epoch from ts)::float8, c::float8 FROM bars "
                "WHERE symbol=%s ORDER BY ts_recorded DESC, id DESC LIMIT %s",
                (symbol, int(limit)),
            ).fetchall()
        return [(r[0], r[1]) for r in reversed(rows)]

    def bars_before(self, symbol, ts, limit) -> list[float]:
        """The last *limit* bar closes at/before *ts*, chronological (the grader's
        structural-stop window)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT c::float8 FROM bars WHERE symbol=%s AND ts <= %s "
                "ORDER BY ts DESC LIMIT %s", (symbol, ts, int(limit)),
            ).fetchall()
        return [r[0] for r in reversed(rows)]

    def bars_between(self, symbol, start, end) -> list[float]:
        """Bar closes with ``start < ts <= end``, chronological (the grader's exit walk)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT c::float8 FROM bars WHERE symbol=%s AND ts > %s AND ts <= %s "
                "ORDER BY ts", (symbol, start, end),
            ).fetchall()
        return [r[0] for r in rows]

    def bar_symbols_between(self, start, end) -> list[str]:
        """Distinct symbols holding bars in a window — lets the grader attach a
        symbol-less fire to the ONLY candidate bar stream (never a guess between two)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT symbol FROM bars WHERE ts > %s AND ts <= %s ORDER BY 1",
                (start, end),
            ).fetchall()
        return [r[0] for r in rows]

    def grade_fire(self, fire_id, outcome, pnl=None) -> bool:
        """Fill a fire's outcome/pnl exactly once (``WHERE outcome IS NULL`` — a graded
        fire is never regraded). True if this call did the grading."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE fires SET outcome=%s, pnl=%s WHERE id=%s AND outcome IS NULL",
                (outcome, pnl, int(fire_id)),
            )
            updated = (cur.rowcount or 0) > 0
        if updated:
            self._emit("trading", {"id": int(fire_id), "outcome": outcome, "pnl": pnl})
        return updated

    def fires_missing_assessment(self, limit: int = 3) -> list[dict]:
        """Newest GRADED, gradable fires with no think-on-fire read yet — the
        commentary worklist (bounded: each read is a real brain call)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, engine, direction, entry::float8, outcome, pnl::float8, "
                "symbol, ts FROM fires WHERE outcome IS NOT NULL "
                "AND outcome != 'ungradable' AND assessment IS NULL AND synthetic = false "
                "ORDER BY ts DESC LIMIT %s", (int(limit),),
            ).fetchall()
        return [{"id": r[0], "engine": r[1], "direction": r[2], "entry": r[3],
                 "outcome": r[4], "pnl": r[5], "symbol": r[6], "ts": r[7]} for r in rows]

    def set_fire_assessment(self, fire_id, text) -> bool:
        """Attach Ace's read exactly once (a written assessment is never overwritten)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE fires SET assessment=%s WHERE id=%s AND assessment IS NULL",
                (text, int(fire_id)),
            )
            return (cur.rowcount or 0) > 0

    def engine_scorecard(self, engine) -> dict:
        """One engine's REAL graded record — the numbers Ace grounds its read in."""
        with self._conn() as c:
            row = c.execute(
                "SELECT count(*), count(*) FILTER (WHERE pnl > 0), "
                "coalesce(sum(pnl), 0)::float8 FROM fires "
                "WHERE engine=%s AND outcome IS NOT NULL AND outcome != 'ungradable'",
                (engine,),
            ).fetchone()
        n, wins, net = int(row[0]), int(row[1]), float(row[2])
        return {"graded": n, "wins": wins,
                "win_rate": round(wins / n, 3) if n else None, "net_pnl": round(net, 2)}

    def lore_for(self, engine, limit: int = 5) -> list[dict]:
        """Recent migrated commentary for *engine* (falls back to any engine) — the
        historical voice the live read is grounded in."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, engine, kind, content FROM trade_lore WHERE engine=%s "
                "ORDER BY ts DESC LIMIT %s", (engine, int(limit)),
            ).fetchall()
            if not rows:
                rows = c.execute(
                    "SELECT ts, engine, kind, content FROM trade_lore "
                    "ORDER BY ts DESC LIMIT %s", (int(limit),),
                ).fetchall()
        return [{"ts": r[0], "engine": r[1], "kind": r[2], "content": r[3]} for r in rows]

    def add_lore(self, ts, engine, kind, content, confidence=None) -> bool:
        """Insert one historical commentary row; False if already migrated (dedup key)."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO trade_lore (ts, engine, kind, content, confidence) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (engine, ts, kind) DO NOTHING",
                (ts, engine, kind, content, confidence),
            )
            return (cur.rowcount or 0) > 0

    def engine_detail(self, engine, limit: int = 80) -> dict:
        """Everything the deck's per-engine page shows (apex-style drill, 2026-06-10):
        scorecard, the cumulative paper-PnL curve over graded fires (chronological),
        and recent fires with outcomes + Ace's think-on-fire read. All ledger facts."""
        sc = self.engine_scorecard(engine)
        with self._conn() as c:
            eq = c.execute(
                "SELECT ts, sum(pnl) OVER (ORDER BY ts, id)::float8 FROM fires "
                "WHERE engine=%s AND outcome IS NOT NULL AND outcome != 'ungradable' "
                "AND pnl IS NOT NULL AND synthetic=false ORDER BY ts, id",
                (engine,)).fetchall()
            rows = c.execute(
                "SELECT id, direction, entry::float8, outcome, pnl::float8, symbol, ts, "
                "stop::float8, target::float8, assessment FROM fires WHERE engine=%s "
                "AND synthetic=false ORDER BY ts DESC LIMIT %s",
                (engine, int(limit))).fetchall()
        return {
            "engine": engine,
            "scorecard": sc,
            "equity": [{"ts": str(t)[:16], "cum": round(v, 2)} for t, v in eq],
            "fires": [{"id": r[0], "direction": r[1], "entry": r[2], "outcome": r[3],
                       "pnl": r[4], "symbol": r[5], "ts": str(r[6])[:19],
                       "stop": r[7], "target": r[8], "assessment": r[9]} for r in rows],
        }

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

    def followup_candidates(self, base_campaign, fu_campaign, min_age_days, limit=10) -> list[dict]:
        """Prospects pitched on *base_campaign* (email) ≥ *min_age_days* ago who have
        NOT replied (mail_replies), NOT bounced (mail_ledger), and NOT yet received
        *fu_campaign*. Joined back to ``leads`` for the name/kind the composer needs.
        This is only possible since reply detection exists — without it a follow-up
        could land on someone who already said yes (or on a dead address)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT o.recipient, l.name, l.kind, l.region, l.contact "
                "FROM outreach_ledger o "
                "JOIN leads l ON lower(l.contact->>'email') = lower(o.recipient) "
                "WHERE o.campaign = %s AND o.channel = 'email' "
                "AND o.ts < now() - (%s * interval '1 day') "
                "AND NOT EXISTS (SELECT 1 FROM mail_replies r "
                "  WHERE lower(r.sender) = lower(o.recipient)) "
                "AND NOT EXISTS (SELECT 1 FROM mail_ledger m "
                "  WHERE lower(m.recipient) = lower(o.recipient) AND m.status = 'bounced') "
                "AND NOT EXISTS (SELECT 1 FROM outreach_ledger f "
                "  WHERE f.recipient = o.recipient AND f.campaign = %s) "
                "ORDER BY o.ts ASC LIMIT %s",
                (base_campaign, float(min_age_days), fu_campaign, limit),
            ).fetchall()
        return [
            {"recipient": r[0], "name": r[1], "kind": r[2], "region": r[3],
             "contact": r[4] if isinstance(r[4], dict) else {}}
            for r in rows
        ]

    def pitched_recipients(self) -> set[str]:
        """Every address we have ever pitched (outreach + mail ledgers), lowercased.
        The reply poller matches inbound senders against this set — a hit is the
        conversion moment the whole funnel exists for."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT recipient FROM outreach_ledger "
                "UNION SELECT recipient FROM mail_ledger"
            ).fetchall()
        return {str(r[0]).strip().lower() for r in rows if r[0] and "@" in str(r[0])}

    def record_reply(self, sender, subject, kind, message_id, snippet="") -> bool:
        """Record an inbound reply/bounce; True if NEW (False = already seen this
        Message-ID on a prior poll). Idempotent across the 15-min cron."""
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO mail_replies (sender, subject, kind, message_id, snippet) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (message_id) DO NOTHING RETURNING id",
                (sender, subject, kind, message_id, snippet),
            ).fetchone()
        if row:
            self._emit("reply", {"sender": sender, "kind": kind, "subject": subject,
                                 "id": row[0]})
        return bool(row)

    def mark_bounced(self, recipient) -> int:
        """Flip a recipient's mail_ledger rows to 'bounced' so follow-ups and audits
        stop treating a dead address as contacted-and-alive. Returns rows updated."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE mail_ledger SET status='bounced' "
                "WHERE lower(recipient)=lower(%s) AND status='sent'",
                (recipient,),
            )
            return cur.rowcount or 0

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
                  "id, engine, direction, symbol, entry::float8 entry, "
                  "stop::float8 stop, target::float8 target, rationale, "
                  "outcome, pnl::float8 pnl, "
                  "to_char(ts,'YYYY-MM-DD HH24:MI') ts"),
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
