# Ace → Utah Revenue Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Ace's three revenue domains — leads, trading, marketing — into Utah as capabilities that write the Postgres product ledger and light deck panels, closing the specific gaps between the current scaffolds and the approved spec.

**Architecture:** Capabilities-behind-the-brain (NOT agents). Each producer writes one ledger schema (`record_lead`/`log_outreach`/`record_fire`, `UNIQUE`=never-twice) → publishes a bus channel → deck panel lights (else DORMANT, never simulated). The capability scaffolds already exist (`utah/product/leads.py`, `outreach.py`, `trading.py`, `marketer.py`); this plan closes the gaps. **Ungated tasks ship first** (real live proof immediately); **gated tasks** (real send, Pushover, WC feed) are built + unit-tested + left honestly DORMANT behind a runtime cred check until Michael provides the input.

**Tech Stack:** Python 3.14, Postgres 17 + pgvector (`host=/tmp port=5433 dbname=utah`), psycopg, msgspec, anyio, starlette deck, free-everything (stdlib `urllib` for Resend/Pushover HTTP; OSM Overpass for leads), Claude CLI only paid lane.

---

## Pre-flight (read before Task 1)

**Run tests:**
```bash
cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q
```
A single test file/test:
```bash
cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -q
```

**DB rules (from HANDOFF §4/§7):**
- Capability unit tests use hand-rolled `_RecLedger` doubles + `FakeFailureStore` — **no DB needed**.
- `tests/test_ledger.py` runs against the **live `utah` DB**, tags rows with `MARK = "__pytest__"`, and self-cleans in teardown — **never** point ledger tests at anything else. It `pytest.skip`s if Postgres is unreachable.
- The disposable `utah_test` DB is only for the brain integration suite; do not touch it here.

**Secrets convention (from `utah/daemon/runtime.py`):** gated capabilities read JSON under `~/.utah/secrets/<name>.json` (dir mode 0700). New slots this plan introduces: `resend.json` (outreach send), `pushover.json` (trade alerts), `wc.json` (WealthCharts feed). Never read from `~/.ace` (`assert_isolated` forbids it).

**Test boundary fixture:** `tests/conftest.py` autouse `_restore_boundaries` already pins `failures.set_store(FakeFailureStore())` per test — but capability tests still call `failures.set_store(FakeFailureStore())` explicitly to grab a handle for assertions (follow the existing pattern in `tests/test_trading.py`).

**Execution ordering (important):** Respect dependencies — Task 5 (fires schema) before Task 9; Tasks 6–8 before Task 9; Task 10 before Task 11. Recommended order: **ungated first** (real live proof, no Michael-gate): `1 → 2` (leads frontier; live proof at Task 2 Step 7) `→ 5 → 6 → 7 → 8 → 9` (trading paper risk core; proof at Task 9). Then **gated, built-and-DORMANT**: `3 → 4` (outreach send) `→ 10 → 11` (trade alert + WC feed). Then **deferred**: `12` (marketing resume trigger). Each task ends in a commit.

**Commit identity:** end every commit message body with:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

# PHASE A — LEADS

## Task 1: Leads frontier tiling (pure function) — UNGATED

The current `scout()` is single-bbox COWETA and a single Overpass query over a big box times out. Add a pure tiling function that splits a metro bbox into sub-tiles small enough to query.

**Files:**
- Modify: `utah/product/leads.py`
- Test: `tests/test_leads.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_leads.py`:
```python
from utah.product import leads

def test_frontier_tiles_splits_bbox_into_grid():
    # 1.0 x 1.0 degree box, 0.5 step -> 2x2 = 4 tiles
    tiles = leads.frontier_tiles((33.0, -85.0, 34.0, -84.0), step=0.5)
    assert len(tiles) == 4
    # every tile is within the parent box and has positive area
    for (s, w, n, e) in tiles:
        assert 33.0 <= s < n <= 34.0
        assert -85.0 <= w < e <= -84.0
    # tiles cover the corners
    assert (33.0, -85.0, 33.5, -84.5) in tiles
    assert (33.5, -84.5, 34.0, -84.0) in tiles

def test_frontier_tiles_handles_nondivisible_remainder():
    # 0.7 wide, 0.5 step -> 2 columns (0.5 + 0.2 remainder), clamped to parent edge
    tiles = leads.frontier_tiles((33.0, -85.0, 33.5, -84.3), step=0.5)
    east_edges = sorted({t[3] for t in tiles})
    assert east_edges[-1] == -84.3  # last column clamps to parent east edge
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_leads.py::test_frontier_tiles_splits_bbox_into_grid -v`
Expected: FAIL — `AttributeError: module 'utah.product.leads' has no attribute 'frontier_tiles'`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/leads.py` (near `COWETA_BBOX`):
```python
# A metro frontier: tile a large bbox into Overpass-sized sub-boxes.
# Atlanta metro ring around Coweta; ~0.7deg box tiled at 0.25deg ~= 9 tiles.
METRO_BBOX = (33.0, -85.1, 34.1, -84.0)
TILE_STEP = 0.25  # degrees; each tile small enough to not time out Overpass


def frontier_tiles(bbox, step=TILE_STEP):
    """Split (south, west, north, east) into a grid of <=step sub-boxes.

    Pure function. Last row/column clamps to the parent edge so the whole
    box is covered with no overlap and no spill.
    """
    south, west, north, east = bbox
    tiles = []
    s = south
    while s < north:
        n = min(s + step, north)
        w = west
        while w < east:
            e = min(w + step, east)
            tiles.append((round(s, 6), round(w, 6), round(n, 6), round(e, 6)))
            w = e
        s = n
    return tiles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_leads.py -k frontier_tiles -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**
```bash
cd ~/Desktop/ProjectUtah
git add utah/product/leads.py tests/test_leads.py
git commit -m "feat(leads): frontier_tiles — split a metro bbox into Overpass-sized tiles"
```

---

## Task 2: scout_frontier capability + RPC — UNGATED (real live proof here)

Iterate the tiles, scout each, dedup at the ledger, respect a per-run tile budget. This is the L2 "scale toward 500/day" producer. Real OSM data; the live probe yields real new leads.

**Files:**
- Modify: `utah/product/leads.py`
- Modify: `utah/daemon/handlers/core_handlers.py` (REGISTRY + handler)
- Test: `tests/test_leads.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_leads.py` (mirror the existing `_RecLedger` + injected `fetch` pattern already in this file):
```python
def test_scout_frontier_iterates_tiles_and_dedupes(monkeypatch):
    # two adjacent tiles; second returns a lead already seen in the first
    calls = {"n": 0}
    SAMPLE_A = {"elements": [{"tags": {"name": "Joe Plumbing", "shop": "trade"}}]}
    SAMPLE_B = {"elements": [{"tags": {"name": "Joe Plumbing", "shop": "trade"}},
                             {"tags": {"name": "Acme Welding", "craft": "welder"}}]}

    def fake_fetch(query):
        calls["n"] += 1
        return SAMPLE_A if calls["n"] == 1 else SAMPLE_B

    lg = _RecLedger()
    r = leads.scout_frontier(
        lg, bbox=(33.0, -85.0, 33.0 + leads.TILE_STEP * 2, -85.0 + leads.TILE_STEP),
        region="Test Metro", fetch=fake_fetch, max_tiles=2,
    )
    assert r["tiles_scanned"] == 2
    assert r["found"] >= 3          # 1 + 2 across tiles
    assert r["new"] == 2            # Joe deduped on the second tile
    assert r["region"] == "Test Metro"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_leads.py::test_scout_frontier_iterates_tiles_and_dedupes -v`
Expected: FAIL — `AttributeError: ... has no attribute 'scout_frontier'`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/leads.py` (after `scout`):
```python
def scout_frontier(ledger, bbox=METRO_BBOX, region="Atlanta Metro Ring",
                   fetch=None, max_tiles=12):
    """Scale leads: tile a metro bbox, scout each tile, dedup at the ledger.

    Each tile is a separate Overpass query (avoids the single-big-box timeout).
    Dedup is the ledger's UNIQUE(name, region) — same lead across tiles counts once.
    Returns {tiles_scanned, found, new, region}.
    """
    tiles = frontier_tiles(bbox)[:max_tiles]
    found = 0
    new = 0
    for tile in tiles:
        res = scout(ledger, bbox=tile, region=region, fetch=fetch)
        found += res["found"]
        new += res["new"]
    return {"tiles_scanned": len(tiles), "found": found, "new": new, "region": region}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_leads.py -q`
Expected: PASS (all leads tests)

- [ ] **Step 5: Register the RPC**

In `utah/daemon/handlers/core_handlers.py`, add the blocking fn + async handler next to the existing `scout_leads` ones, and add to `REGISTRY`:
```python
def _scout_frontier_blocking() -> dict:
    from utah.product import leads
    from utah.product.ledger import get_ledger
    return leads.scout_frontier(get_ledger())

async def scout_frontier(ctx: Context, params: object) -> dict:
    """Capability: tile the metro frontier and scout each tile -> record_lead.
    Ungated (free OSM); dedup at the ledger UNIQUE(name, region)."""
    with ctx.governor.admission():
        return await ctx.pool.run(_scout_frontier_blocking)
```
Add `"scout_frontier": scout_frontier,` to the `REGISTRY` dict literal.

- [ ] **Step 6: Run the full suite + commit**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions)
```bash
git add utah/product/leads.py tests/test_leads.py utah/daemon/handlers/core_handlers.py
git commit -m "feat(leads): scout_frontier capability + RPC — tiled metro scout, ledger dedup"
```

- [ ] **Step 7: LIVE PROOF (real data, no gate)**

With the daemon running (`~/Desktop/ProjectUtah/bin/utah status` healthy), call the RPC and confirm real new leads land in Postgres:
```bash
~/.utah/venv/bin/python -c "from utah.daemon.client import call; print(call('scout_frontier', {}))"
PYTHONPATH=~/Desktop/ProjectUtah ~/.utah/venv/bin/python -c "from utah.product.ledger import get_ledger; print(get_ledger().counts())"
```
Expected: `scout_frontier` returns `tiles_scanned >= 1, new >= 1`; `counts()['leads']` increased. Confirm on the deck: `http://127.0.0.1:8766/` LEADS panel shows rows; drill-down (`/panel/leads`) lists them. **This is the first real revenue-surface proof.**

> **L2/L3 coverage note (spec §2.3):** Dedup already lives at the ledger `UNIQUE(name, region)`, so the **Parquet archive** is an archive optimization, not on the revenue path — intentionally **deferred (YAGNI)** until lead volume warrants a cold store (revisit when `counts()['leads'] > ~10k`). Spec **L3** (leads panel drill-down) is **already satisfied** by the generic `panel_detail` + `data-drill="leads"` wiring (per gap analysis); the only missing element is an "outreach state" column — a future `outreach_ledger` join, deferred until a panel consumer needs it.

---

## Task 3: Resend email sender (GATED) — built + unit-tested, DORMANT until creds

A real transactional sender via Resend's HTTP API (stdlib `urllib`, free-tier). Reads `~/.utah/secrets/resend.json` (`{"api_key": "...", "from": "outreach@yourdomain.com"}`). No secret → returns a gated result; never raises, never fakes a send.

**Files:**
- Create: `utah/product/sender.py`
- Test: `tests/test_sender.py`

- [ ] **Step 1: Write the failing test**
```python
import json
from utah.product import sender

def test_send_gated_without_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(sender, "_SECRET", tmp_path / "resend.json")
    r = sender.send_email("a@b.com", "Subj", "Body")
    assert r == {"sent": False, "gated": True, "reason": "no resend secret"}

def test_send_uses_transport_when_configured(tmp_path, monkeypatch):
    sec = tmp_path / "resend.json"
    sec.write_text(json.dumps({"api_key": "re_test", "from": "o@dom.com"}))
    monkeypatch.setattr(sender, "_SECRET", sec)
    seen = {}
    def fake_transport(url, headers, payload):
        seen.update(url=url, headers=headers, payload=payload)
        return {"id": "email_123"}
    r = sender.send_email("a@b.com", "Subj", "Body", transport=fake_transport)
    assert r == {"sent": True, "gated": False, "id": "email_123"}
    assert seen["payload"]["to"] == ["a@b.com"]
    assert seen["payload"]["from"] == "o@dom.com"
    assert seen["headers"]["Authorization"] == "Bearer re_test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utah.product.sender'`

- [ ] **Step 3: Write minimal implementation**

Create `utah/product/sender.py`:
```python
"""Resend transactional email sender (free-tier, stdlib urllib).

Gated: reads ~/.utah/secrets/resend.json. No secret -> {sent:False, gated:True}.
Never fakes a send; never raises into the caller.
"""
from __future__ import annotations

import json
import urllib.request

from utah.daemon import runtime

_SECRET = runtime.UTAH_HOME / "secrets" / "resend.json"
_API = "https://api.resend.com/emails"


def _load():
    try:
        return json.loads(_SECRET.read_text())
    except Exception:
        return None


def _http_post(url, headers, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def send_email(to, subject, body, *, transport=None) -> dict:
    sec = _load()
    if not sec or not sec.get("api_key") or not sec.get("from"):
        return {"sent": False, "gated": True, "reason": "no resend secret"}
    headers = {"Authorization": f"Bearer {sec['api_key']}",
               "Content-Type": "application/json"}
    payload = {"from": sec["from"], "to": [to], "subject": subject, "text": body}
    send = transport or _http_post
    try:
        res = send(_API, headers, payload)
    except Exception as exc:  # network/HTTP failure is real-or-black, not faked green
        return {"sent": False, "gated": False, "error": str(exc)}
    return {"sent": True, "gated": False, "id": res.get("id")}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_sender.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/sender.py tests/test_sender.py
git commit -m "feat(outreach): Resend email sender — gated on ~/.utah/secrets/resend.json, real-or-black"
```

---

## Task 4: Wire real send into outreach.queue + real footer (GATED at runtime)

`outreach.queue` currently hardcodes `sent: 0` and never sends. Wire it to send via an injectable `send_fn` when `can_send=True`, `log_outreach` only on a confirmed send, and pull the CAN-SPAM address from config (placeholder until Michael sets it).

**Files:**
- Modify: `utah/product/outreach.py`
- Modify: `utah/config.py` (add `CANSPAM_ADDRESS` env constant)
- Modify: `utah/daemon/handlers/core_handlers.py` (`queue_outreach` wiring)
- Test: `tests/test_outreach.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_outreach.py` (uses the file's existing `_RecLedger` + `FakeFailureStore` pattern):
```python
def test_queue_sends_and_logs_only_on_confirmed_send():
    from utah import failures
    from tests.fakes import FakeFailureStore
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    leads_in = [{"name": "Joe Plumbing", "kind": "trade", "phone": None,
                 "contact": "joe@plumb.com"}]
    sent_to = []
    def send_fn(to, subject, body):
        sent_to.append(to)
        return {"sent": True, "gated": False, "id": "email_1"}
    r = outreach.queue(lg, "smb-jun", leads_in, can_send=True, send_fn=send_fn)
    assert r["sent"] == 1
    assert sent_to == ["joe@plumb.com"]
    assert ("joe@plumb.com", "smb-jun") in lg.outreach  # logged after send

def test_queue_does_not_log_when_send_gated():
    from utah import failures
    from tests.fakes import FakeFailureStore
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    leads_in = [{"name": "Joe Plumbing", "kind": "trade", "contact": "joe@plumb.com"}]
    def gated_send(to, subject, body):
        return {"sent": False, "gated": True, "reason": "no resend secret"}
    r = outreach.queue(lg, "smb-jun", leads_in, can_send=True, send_fn=gated_send)
    assert r["sent"] == 0
    assert ("joe@plumb.com", "smb-jun") not in lg.outreach  # never logged a non-send
```

Note: the existing `_RecLedger` in `tests/test_outreach.py` records `log_outreach` into a `self.outreach` list — confirm that attribute name; if the double uses a different name (e.g. `self.seen`), match it in the asserts.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_outreach.py -k confirmed_send -v`
Expected: FAIL — `queue()` got an unexpected keyword `send_fn` (or `sent` stays 0).

- [ ] **Step 3: Add the config constant**

In `utah/config.py`, add near the other env constants:
```python
# CAN-SPAM physical mailing address (Michael's business input; required to cold-send).
# Placeholder until set; outreach stays gated while this is the placeholder.
CANSPAM_ADDRESS: str = os.environ.get(
    "UTAH_CANSPAM_ADDRESS",
    "[CAN-SPAM physical address — Michael's business input, required to send]",
)
```

- [ ] **Step 4: Wire the sender into queue**

In `utah/product/outreach.py`, change `DEFAULT_FOOTER` to source the address from config, and add the send path. Update the signature to `queue(ledger, campaign, leads, footer=None, can_send=False, send_fn=None)`:
```python
from utah import config

# ... in DEFAULT_FOOTER, replace the hardcoded placeholder address with:
#   "address": config.CANSPAM_ADDRESS,

def queue(ledger, campaign, leads, footer=None, can_send=False, send_fn=None):
    queued = suppressed = needs_contact = blocked = sent = 0
    for lead in leads:
        channel = pick_channel(lead.get("contact") or lead.get("phone"))
        if channel is None:
            needs_contact += 1
            continue
        msg = compose(lead, campaign, footer=footer)
        score = content_score(msg["body"])
        if score["block"]:
            blocked += 1
            continue
        recipient = lead.get("contact") or lead.get("phone")
        # Suppression check is the ledger UNIQUE(recipient, campaign).
        # We only consume it once we have actually sent (so a gated attempt
        # does not burn the never-twice slot).
        if can_send and send_fn is not None:
            res = send_fn(recipient, msg["subject"], msg["body"])
            if res.get("sent"):
                if ledger.log_outreach(recipient, campaign, channel):
                    sent += 1
                continue
            # gated / errored send: count as queued-but-not-sent, do NOT log
            queued += 1
            continue
        # no live send path: queue + consume suppression (compose-only mode)
        if ledger.log_outreach(recipient, campaign, channel):
            queued += 1
        else:
            suppressed += 1

    gated = ""
    if queued and not (can_send and send_fn is not None):
        gated = ("outreach send is gated: needs sending creds (Resend) + a real "
                 "CAN-SPAM physical address (Michael's business inputs)")
        failures.record("outreach", "send_gated",
                        f"{campaign}: {queued} queued, 0 sent — {gated}")
    return {"campaign": campaign, "queued": queued, "suppressed": suppressed,
            "needs_contact": needs_contact, "blocked": blocked,
            "sent": sent, "gated": gated}
```
Keep the existing compose/lint/suppress tests green — the compose-only branch preserves the old behavior when `send_fn` is None.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_outreach.py -q`
Expected: PASS (new + existing). If a pre-existing test asserted the placeholder address string literally, update it to assert `config.CANSPAM_ADDRESS` instead.

- [ ] **Step 6: Wire the RPC**

In `utah/daemon/handlers/core_handlers.py`, update `_queue_outreach_blocking` to inject the real sender + footer + cred-derived `can_send`:
```python
def _queue_outreach_blocking(campaign: str) -> dict:
    from utah.product import outreach, leads, sender
    from utah.product.ledger import get_ledger
    from utah import config
    lg = get_ledger()
    found = leads.find_no_website_smbs(leads.COWETA_BBOX)  # current leads source
    footer = dict(outreach.DEFAULT_FOOTER)
    can_send = (sender._load() is not None
                and "[CAN-SPAM" not in config.CANSPAM_ADDRESS)
    return outreach.queue(lg, campaign, found, footer=footer,
                          can_send=can_send, send_fn=sender.send_email)
```
(If `queue_outreach` already builds its lead list differently, keep that — only add `footer`, `can_send`, and `send_fn`.)

- [ ] **Step 7: Run full suite + commit**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q`
Expected: PASS
```bash
git add utah/product/outreach.py utah/config.py utah/daemon/handlers/core_handlers.py tests/test_outreach.py
git commit -m "feat(outreach): real Resend send path in queue — log_outreach only on confirmed send, gated on creds+address"
```

- [ ] **Step 8: GATED PROOF (blocked on Michael)**

Live send proof requires Michael to (a) put `{"api_key","from"}` in `~/.utah/secrets/resend.json` with a verified SPF/DKIM domain, and (b) `export UTAH_CANSPAM_ADDRESS="<real postal address>"`. Until then the capability is correct and **honestly gated** (`sent: 0`, `send_gated` on the AUDIT panel) — never faked green. Probe when unblocked: `sent >= 1` in 24h; deck OUTREACH panel shows the logged send.

---

# PHASE B — TRADING (paper engines + alert pipeline, NO real orders)

## Task 5: Extend the fires ledger for rich fires — UNGATED

The `fires` table has `engine, direction, entry, ts, outcome, pnl, synthetic` but no `stop`, `target`, or `rationale` — the rich-alert fields. Add them idempotently and extend `record_fire` (keyword-only, backward compatible).

**Files:**
- Modify: `utah/product/ledger.py`
- Test: `tests/test_ledger.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ledger.py` (uses the real `ledger` fixture + `MARK="__pytest__"` self-clean pattern already in the file):
```python
def test_record_fire_persists_rich_fields(ledger):
    fid = ledger.record_fire("breakout", "long", entry=12.5, synthetic=True,
                             stop=12.0, target=13.5, rationale="trend regime, breakout > 20-bar high")
    assert isinstance(fid, int)
    rows = ledger.recent("fires", limit=5)
    row = next(r for r in rows if r["id"] == fid)
    assert row["stop"] == 12.0
    assert row["target"] == 13.5
    assert "trend regime" in row["rationale"]
```
Tag the engine name with `MARK` if the fixture filters by it; follow the file's existing cleanup convention so teardown deletes this row.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_ledger.py::test_record_fire_persists_rich_fields -v`
Expected: FAIL (column does not exist / `record_fire` got unexpected kwarg). Skips if Postgres down — start the stack first.

- [ ] **Step 3: Add columns + extend record_fire**

In `utah/product/ledger.py`:
1. In `_DDL`, after the `fires` `CREATE TABLE`, add idempotent column adds:
```sql
ALTER TABLE fires ADD COLUMN IF NOT EXISTS stop numeric;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS target numeric;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS rationale text;
```
2. Update `record_fire`:
```python
def record_fire(self, engine, direction, entry=None, synthetic=False,
                *, stop=None, target=None, rationale=None) -> int:
    with self._conn() as conn:
        row = conn.execute(
            "INSERT INTO fires (engine, direction, entry, synthetic, stop, target, rationale) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (engine, direction, entry, synthetic, stop, target, rationale),
        ).fetchone()
    if not synthetic:
        self._emit("trading", {"engine": engine, "direction": direction, "id": row[0]})
    return int(row[0])
```
3. Add `stop, target, rationale` to the `fires` column list in the `_RECENT` map so `recent("fires", ...)` returns them.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_ledger.py -q`
Expected: PASS (the live `utah` table gets the new columns; rows self-clean)

- [ ] **Step 5: Commit**
```bash
git add utah/product/ledger.py tests/test_ledger.py
git commit -m "feat(ledger): fires gains stop/target/rationale (idempotent), record_fire kw-only extension"
```

---

## Task 6: Regime detection — UNGATED

The old engines lost on RANGE chop because they fired breakouts in a ranging market. Add a pure regime classifier (`trend` vs `range`) from closes.

**Files:**
- Modify: `utah/product/trading.py`
- Test: `tests/test_trading.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_trading.py`:
```python
def test_detect_regime_trend_vs_range():
    trend = [float(i) for i in range(40)]            # strict uptrend
    assert trading.detect_regime(trend) == "trend"
    rng = [10.0 + (i % 2) * 0.05 for i in range(40)] # tiny oscillation = range
    assert trading.detect_regime(rng) == "range"

def test_detect_regime_too_few_bars_is_range():
    assert trading.detect_regime([1.0, 2.0]) == "range"  # default safe = range (no-fire)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k detect_regime -v`
Expected: FAIL — no attribute `detect_regime`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/trading.py`:
```python
def detect_regime(closes, *, lookback=20) -> str:
    """Classify the recent window as 'trend' or 'range'.

    Net directional move over the window vs the summed bar-to-bar travel:
    a high ratio = directional (trend); a low ratio = chop (range).
    Safe default 'range' (which blocks fires) when data is insufficient.
    """
    if not closes or len(closes) < lookback + 1:
        return "range"
    window = closes[-lookback - 1:]
    net = abs(window[-1] - window[0])
    travel = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if travel == 0:
        return "range"
    return "trend" if (net / travel) >= 0.35 else "range"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k detect_regime -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/trading.py tests/test_trading.py
git commit -m "feat(trading): detect_regime — trend/range classifier, safe-range default"
```

---

## Task 7: Regime guard (block the loss classes) — UNGATED

The old book lost on LONG and grade-B coin-flips. Gate every signal through the regime: only fire in a trend regime, and only in the trend's direction.

**Files:**
- Modify: `utah/product/trading.py`
- Test: `tests/test_trading.py`

- [ ] **Step 1: Write the failing test**
```python
def test_passes_regime_guard_blocks_range_and_counter_trend():
    long_sig = {"engine": "breakout", "direction": "long", "entry": 30.0}
    # range regime -> always blocked (the RANGE-chop loss class)
    assert trading.passes_regime_guard(long_sig, "range", trend_dir="long") is False
    # trend regime, signal aligned with trend -> allowed
    assert trading.passes_regime_guard(long_sig, "trend", trend_dir="long") is True
    # trend regime, signal AGAINST trend -> blocked (the counter-trend LONG loss class)
    assert trading.passes_regime_guard(long_sig, "trend", trend_dir="short") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k regime_guard -v`
Expected: FAIL — no attribute `passes_regime_guard`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/trading.py`:
```python
def passes_regime_guard(signal, regime, *, trend_dir) -> bool:
    """Only fire in a trend regime, and only WITH the trend.

    Blocks: any fire in 'range' (chop loss class); counter-trend fires in
    'trend' (the LONG-into-downtrend loss class). trend_dir is the regime's
    own direction ('long'|'short').
    """
    if regime != "trend":
        return False
    return signal["direction"] == trend_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k regime_guard -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/trading.py tests/test_trading.py
git commit -m "feat(trading): passes_regime_guard — block range + counter-trend fires"
```

---

## Task 8: Per-bar stop + PnL — UNGATED (the −168pt fix)

The old book never checked the stop on each bar; overnight gaps blew through it. Add a per-bar stop walk that exits at the stop the first bar it's breached and returns realized PnL.

**Files:**
- Modify: `utah/product/trading.py`
- Test: `tests/test_trading.py`

- [ ] **Step 1: Write the failing test**
```python
def test_apply_stop_exits_on_first_breach_long():
    # long from 100, stop 98; bars dip to 97 on bar 2 -> exit at stop, pnl = -2
    out = trading.apply_stop(entry=100.0, direction="long", stop=98.0,
                             bars=[100.5, 97.0, 105.0])
    assert out["exit"] == 98.0
    assert out["pnl"] == -2.0
    assert out["outcome"] == "stopped"

def test_apply_stop_rides_to_last_bar_when_never_breached():
    out = trading.apply_stop(entry=100.0, direction="long", stop=98.0,
                             bars=[101.0, 102.0, 103.0])
    assert out["exit"] == 103.0
    assert out["pnl"] == 3.0
    assert out["outcome"] == "open_close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k apply_stop -v`
Expected: FAIL — no attribute `apply_stop`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/trading.py`:
```python
def apply_stop(entry, direction, stop, bars) -> dict:
    """Walk bars; exit the first bar the stop is breached, else exit at last close.

    Returns {exit, pnl, outcome}. pnl is in points, sign-correct for direction.
    This is the per-bar stop the old engines never checked.
    """
    for price in bars:
        breached = price <= stop if direction == "long" else price >= stop
        if breached:
            pnl = (stop - entry) if direction == "long" else (entry - stop)
            return {"exit": stop, "pnl": round(pnl, 4), "outcome": "stopped"}
    last = bars[-1]
    pnl = (last - entry) if direction == "long" else (entry - last)
    return {"exit": last, "pnl": round(pnl, 4), "outcome": "open_close"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k apply_stop -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/trading.py tests/test_trading.py
git commit -m "feat(trading): apply_stop — per-bar stop walk + realized pnl"
```

---

## Task 9: Wire guarded paper engine → record_fire — UNGATED logic (paper only)

Compose the pieces: evaluate a signal, classify regime, apply the guard, set a stop, and on a guarded fire write a rich `record_fire`. Paper only — `synthetic=True` until a real WC feed lands (Task 12), so nothing claims a real fire prematurely.

**Files:**
- Modify: `utah/product/trading.py`
- Test: `tests/test_trading.py`

- [ ] **Step 1: Write the failing test**
```python
def test_run_paper_guarded_fire_writes_rich_record():
    from utah import failures
    from tests.fakes import FakeFailureStore
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    # strict uptrend, last bar breaks the 20-bar high -> long signal in a trend
    closes = [float(i) for i in range(30)]  # ...28, 29 ; 29 > max(prior)
    r = trading.run_paper(lg, closes=closes, stop_pts=2.0, lookback=20)
    assert r["fired"] is True
    eng, direction, entry, synthetic = lg.fires[-1][:4]
    assert direction == "long" and synthetic is True   # paper
    assert r["rationale"]                                # non-empty why

def test_run_paper_blocks_range_chop():
    from utah import failures
    from tests.fakes import FakeFailureStore
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [10.0 + (i % 2) * 0.05 for i in range(30)]  # chop
    r = trading.run_paper(lg, closes=closes, stop_pts=2.0, lookback=20)
    assert r["fired"] is False
    assert lg.fires == []

def test_run_paper_resolves_stop_when_future_bars_given():
    # replay/backtest mode: subsequent bars exist, so resolve the per-bar stop
    from utah import failures
    from tests.fakes import FakeFailureStore
    failures.set_store(FakeFailureStore())
    lg = _RecLedger()
    closes = [float(i) for i in range(30)]            # long signal at 29, stop 27
    r = trading.run_paper(lg, closes=closes, stop_pts=2.0, lookback=20,
                          resolve_bars=[29.5, 26.0, 31.0])  # dips through 27 on bar 2
    assert r["fired"] is True
    assert r["outcome"] == "stopped"
    assert r["pnl"] == -2.0
```
Update the file's `_RecLedger.record_fire` double to accept the new keyword args:
```python
def record_fire(self, engine, direction, entry=None, synthetic=False,
                *, stop=None, target=None, rationale=None):
    self.fires.append((engine, direction, entry, synthetic, stop, target, rationale))
    return len(self.fires)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k run_paper -v`
Expected: FAIL — no attribute `run_paper`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/trading.py`:
```python
def run_paper(ledger, *, closes, stop_pts=2.0, lookback=20, engine="breakout",
              resolve_bars=None) -> dict:
    """Guarded paper engine: evaluate -> regime guard -> stop -> rich record_fire.

    ALWAYS synthetic=True (paper). A real (non-synthetic) fire only happens once
    the live WC feed is wired and proven (see feed_available).

    resolve_bars: optional post-signal bars (replay/backtest). When provided,
    walk the per-bar stop via apply_stop and attach the realized outcome/pnl —
    the live forward loop has no future bars yet, so it omits this.
    """
    sig = evaluate(closes, lookback=lookback, engine=engine)
    if sig is None:
        return {"fired": False, "reason": "no signal"}
    regime = detect_regime(closes, lookback=lookback)
    trend_dir = "long" if closes[-1] >= closes[-lookback - 1] else "short"
    if not passes_regime_guard(sig, regime, trend_dir=trend_dir):
        return {"fired": False, "reason": f"blocked: regime={regime}, trend={trend_dir}"}
    entry = sig["entry"]
    stop = entry - stop_pts if sig["direction"] == "long" else entry + stop_pts
    rationale = (f"{regime} regime, {sig['direction']} breakout vs {lookback}-bar "
                 f"extreme; stop {stop_pts}pt")
    fid = ledger.record_fire(engine, sig["direction"], entry, synthetic=True,
                             stop=stop, rationale=rationale)
    result = {"fired": True, "id": fid, "direction": sig["direction"], "entry": entry,
              "stop": stop, "regime": regime, "rationale": rationale}
    if resolve_bars:
        result.update(apply_stop(entry, sig["direction"], stop, resolve_bars))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -q`
Expected: PASS (new + existing trading tests; existing `run` tests untouched)

- [ ] **Step 5: Commit**
```bash
git add utah/product/trading.py tests/test_trading.py
git commit -m "feat(trading): run_paper — guarded paper engine writes rich synthetic fires"
```

---

## Task 10: Pushover trade alert (GATED) — built + unit-tested

A rich Pushover alert: engine + entry + stop/target + rationale. Reads `~/.utah/secrets/pushover.json` (`{"token","user"}`). No secret → gated; never fakes a send.

**Files:**
- Create: `utah/product/trade_alert.py`
- Test: `tests/test_trade_alert.py`

- [ ] **Step 1: Write the failing test**
```python
import json
from utah.product import trade_alert

def test_alert_gated_without_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_alert, "_SECRET", tmp_path / "pushover.json")
    r = trade_alert.send_fire_alert({"engine": "breakout", "direction": "long",
                                     "entry": 12.5, "stop": 10.5, "rationale": "why"})
    assert r == {"sent": False, "gated": True, "reason": "no pushover secret"}

def test_alert_composes_and_sends(tmp_path, monkeypatch):
    sec = tmp_path / "pushover.json"
    sec.write_text(json.dumps({"token": "t", "user": "u"}))
    monkeypatch.setattr(trade_alert, "_SECRET", sec)
    seen = {}
    def fake_transport(url, payload):
        seen.update(url=url, payload=payload)
        return {"status": 1}
    r = trade_alert.send_fire_alert(
        {"engine": "breakout", "direction": "long", "entry": 12.5,
         "stop": 10.5, "target": 14.5, "rationale": "trend breakout"},
        transport=fake_transport)
    assert r["sent"] is True
    assert "breakout" in seen["payload"]["message"]
    assert "12.5" in seen["payload"]["message"]
    assert "10.5" in seen["payload"]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trade_alert.py -v`
Expected: FAIL — no module `utah.product.trade_alert`

- [ ] **Step 3: Write minimal implementation**

Create `utah/product/trade_alert.py`:
```python
"""Pushover trade-fire alert (free, stdlib urllib).

Gated on ~/.utah/secrets/pushover.json {"token","user"}. Composes the rich
alert (engine, entry, stop, target, rationale). Real-or-black; never faked.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from utah.daemon import runtime

_SECRET = runtime.UTAH_HOME / "secrets" / "pushover.json"
_API = "https://api.pushover.net/1/messages.json"


def _load():
    try:
        return json.loads(_SECRET.read_text())
    except Exception:
        return None


def compose(fire) -> str:
    parts = [f"🔥 {fire['engine']} {fire['direction'].upper()} @ {fire['entry']}"]
    if fire.get("stop") is not None:
        parts.append(f"stop {fire['stop']}")
    if fire.get("target") is not None:
        parts.append(f"target {fire['target']}")
    if fire.get("rationale"):
        parts.append(f"— {fire['rationale']}")
    return " | ".join(parts)


def _http_post(url, payload):
    data = urllib.parse.urlencode(payload).encode()
    with urllib.request.urlopen(url, data=data, timeout=15) as resp:
        return json.loads(resp.read().decode())


def send_fire_alert(fire, *, transport=None) -> dict:
    sec = _load()
    if not sec or not sec.get("token") or not sec.get("user"):
        return {"sent": False, "gated": True, "reason": "no pushover secret"}
    payload = {"token": sec["token"], "user": sec["user"], "message": compose(fire)}
    send = transport or _http_post
    try:
        res = send(_API, payload)
    except Exception as exc:
        return {"sent": False, "gated": False, "error": str(exc)}
    return {"sent": bool(res.get("status") == 1), "gated": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trade_alert.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/trade_alert.py tests/test_trade_alert.py
git commit -m "feat(trading): Pushover fire alert — gated, rich entry/stop/target/rationale"
```

---

## Task 11: WC feed gate detector + run_engines wiring — GATED (live probe blocked on Michael)

Flip `feed_available()` from hardwired `False` to a real secret check, and add a `wc_closes()` reader stub that raises until the live CDP bridge is wired with Michael's WealthCharts login. Wire `run_engines` to call `run_paper` + fire the alert. The **live** (non-synthetic) path stays unreachable until the feed is proven — honest DORMANT, never faked.

**Files:**
- Modify: `utah/product/trading.py`
- Modify: `utah/daemon/handlers/core_handlers.py` (`run_engines` wiring)
- Test: `tests/test_trading.py`

- [ ] **Step 1: Write the failing test**
```python
def test_feed_available_reflects_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "_WC_SECRET", tmp_path / "wc.json")
    assert trading.feed_available() is False
    (tmp_path / "wc.json").write_text('{"logged_in": true}')
    assert trading.feed_available() is True

def test_wc_closes_raises_until_bridge_wired(tmp_path, monkeypatch):
    sec = tmp_path / "wc.json"
    sec.write_text('{"logged_in": true}')
    monkeypatch.setattr(trading, "_WC_SECRET", sec)
    import pytest
    with pytest.raises(RuntimeError, match="WealthCharts CDP bridge not wired"):
        trading.wc_closes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -k "feed_available or wc_closes" -v`
Expected: FAIL — `_WC_SECRET` / `wc_closes` undefined

- [ ] **Step 3: Write minimal implementation**

In `utah/product/trading.py`:
```python
from utah.daemon import runtime

_WC_SECRET = runtime.UTAH_HOME / "secrets" / "wc.json"


def feed_available() -> bool:
    """True only when Michael's WealthCharts login secret is present.

    The live tick bridge (Chrome CDP on the WC ES/MES chart -> binary tick
    plane) is wired separately; until then this gates real fires to paper.
    """
    return _WC_SECRET.exists()


def wc_closes():
    """Read recent closes from the live WealthCharts feed.

    GATED: the CDP bridge that connects to the WC chart tab and streams ticks
    onto the binary plane requires Michael's WC login + a loaded ES/MES chart.
    Raises until that bridge is wired and proven with a live probe.
    """
    raise RuntimeError("WealthCharts CDP bridge not wired — needs Michael's WC login")
```
Replace the old hardwired `feed_available() -> False` and the `_live_feed()` raiser with these (keep `run`'s existing gated behavior; it now calls the real `feed_available`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_trading.py -q`
Expected: PASS (all trading tests)

- [ ] **Step 5: Wire run_engines (paper now, alert on fire)**

In `utah/daemon/handlers/core_handlers.py`, update `_run_engines_blocking`:
```python
def _run_engines_blocking() -> dict:
    from utah.product import trading, trade_alert
    from utah.product.ledger import get_ledger
    from utah import failures
    if not trading.feed_available():
        failures.record("trading", "feed_gated",
                        "engine fires gated: no WealthCharts feed (Michael's WC login)")
        return {"fired": False, "gated": True}
    try:
        closes = trading.wc_closes()
    except RuntimeError as exc:
        failures.record("trading", "feed_gated", str(exc))
        return {"fired": False, "gated": True}
    res = trading.run_paper(get_ledger(), closes=closes)
    if res.get("fired"):
        trade_alert.send_fire_alert(res)
    return res
```

- [ ] **Step 6: Run full suite + commit**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q`
Expected: PASS
```bash
git add utah/product/trading.py utah/daemon/handlers/core_handlers.py tests/test_trading.py
git commit -m "feat(trading): feed_available secret-gate + run_engines paper wiring + alert on fire"
```

- [ ] **Step 7: GATED PROOF (blocked on Michael)**

Live trading proof needs Michael's WealthCharts login wired to the CDP tick bridge (a separate follow-up; depends on the binary tick plane, see spec §5). Until then `run_engines` returns `{gated: True}`, the ENGINES deck panel stays honestly DORMANT, and the AUDIT panel shows `feed_gated`. When unblocked: real WC tick → `run_paper` fires → rich `record_fire` (paper) → ENGINES panel lights + Pushover alert. **No real orders, ever, this round.**

---

# PHASE C — MARKETING (deferred — resume trigger only)

## Task 12: Marketing resume-trigger predicate + honest DORMANT — minimal

Marketing is deferred per the spec. Encode the 3-condition resume trigger as a checkable predicate so "deferred" is a contract, not a vibe, and keep the deck honest (no marketing panel, no simulation).

**Files:**
- Modify: `utah/product/marketer.py`
- Test: `tests/test_marketer.py`

- [ ] **Step 1: Write the failing test**
```python
from utah.product import marketer

def test_resume_trigger_blocked_until_all_three_conditions():
    # none met
    r = marketer.resume_ready(revenue_usd=0.0, local_video_proven=False, has_social_creds=False)
    assert r["ready"] is False
    assert set(r["unmet"]) == {"revenue", "local_video", "social_creds"}
    # only revenue
    r = marketer.resume_ready(revenue_usd=5.0, local_video_proven=False, has_social_creds=False)
    assert r["ready"] is False
    assert "revenue" not in r["unmet"]
    # all three
    r = marketer.resume_ready(revenue_usd=5.0, local_video_proven=True, has_social_creds=True)
    assert r["ready"] is True
    assert r["unmet"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_marketer.py -k resume -v`
Expected: FAIL — no attribute `resume_ready`

- [ ] **Step 3: Write minimal implementation**

Add to `utah/product/marketer.py`:
```python
# Marketing is DEFERRED (spec 2026-06-06). It resumes ONLY when all three hold:
#   (a) leads has earned >= $1 real revenue,
#   (b) a proven LOCAL video path exists (LTX-2/MLX or ffmpeg — never Veo/fake URLs),
#   (c) IG/TikTok creds are present.
def resume_ready(*, revenue_usd, local_video_proven, has_social_creds) -> dict:
    unmet = []
    if not (revenue_usd and revenue_usd >= 1.0):
        unmet.append("revenue")
    if not local_video_proven:
        unmet.append("local_video")
    if not has_social_creds:
        unmet.append("social_creds")
    return {"ready": not unmet, "unmet": unmet}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/test_marketer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add utah/product/marketer.py tests/test_marketer.py
git commit -m "feat(marketing): resume_ready trigger — deferred until revenue+local-video+creds"
```

---

## Final verification

- [ ] **Full suite green:**
```bash
cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q
```
Expected: all pass (no regressions; new leads/outreach/trading/ledger/marketer/sender/trade_alert tests green).

- [ ] **Ungated live proof captured:** `scout_frontier` produced real new leads in Postgres (Task 2 Step 7); `run_paper` fires correctly on replayed/real-shaped closes and is blocked in range chop (Task 9). Deck LEADS panel shows real rows.

- [ ] **Gated surfaces honestly DORMANT:** outreach `sent: 0` + `send_gated` (no Resend secret / placeholder address); ENGINES panel DORMANT + `feed_gated` (no WC login). AUDIT panel reflects both. Nothing faked green.

- [ ] **Update spec/handoff:** note in `HANDOFF.md` §5 that leads frontier + trading paper-engine risk core are migrated; the two open Michael-gates are `resend.json`+`UTAH_CANSPAM_ADDRESS` (outreach send) and `wc.json`+CDP bridge (live trading).

---

## What stays gated on Michael (carry to the blocker list)

1. **Outreach real send** — `~/.utah/secrets/resend.json` (verified SPF/DKIM sending domain) + `UTAH_CANSPAM_ADDRESS` (real postal address). Then probe `sent >= 1` in 24h.
2. **Live trading** — `~/.utah/secrets/wc.json` + the WealthCharts CDP tick bridge onto the binary plane (depends on spec §5 baseline). Paper-only until proven; no real orders this round.
3. **Marketing resume** — revenue ≥ $1 AND a proven local video path AND IG/TikTok creds (`resume_ready` gates it).
