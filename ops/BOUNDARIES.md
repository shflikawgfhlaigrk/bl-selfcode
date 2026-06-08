# Boundaries — Utah/Ace vs Sellables

> **Utah is Michael's private operator.** Everything sold (Sovereign, marketing,
> trading skins, website, Discord) is separate — own repos, ports, runtime dirs,
> and launchd labels. They may share *ideas* but never share *processes* or ports.

---

## The four zones

| Zone | What it is | Code / runtime | Who sees it |
|------|------------|----------------|-------------|
| **Utah (Ace)** | Private live operator — voice, memory, leads, self-code | `~/Desktop/ProjectUtah`, `~/.utah/`, `com.utah.*` | Michael only (loopback + tailnet) |
| **Sovereign** | Sold product ($1k zip / Mac app) | `~/Desktop/Sovereign`, `~/.sovereign/`, `com.sovereign.*` | Customers |
| **Black Label Bots** | Marketing site + Stripe + demos | `~/Desktop/Products/blacklabelbots-final`, Cloudflare | Public |
| **Discord** | Community + buyer roles | Bot worker, guild webhooks | Members (may merge with site later) |

**Rule:** Utah never reads `~/.ace/` at runtime. Ace legacy data is archived, not deleted.
**Rule:** The website reads *sanitized JSON* (`data/live.json`) from `~/.blacklabelbots/publish.py` — never the Utah command deck API.

---

## Ports (Michael's Mac)

| Port | Owner | URL |
|------|-------|-----|
| **8766** | **Utah command deck** | `http://127.0.0.1:8766/` |
| **8765 (tailnet)** | Utah via Tailscale serve | `http://michaels-macbook-pro:8765/` → proxies to :8766 |
| **8775 / 8776** | Sovereign (local demo install) | `http://127.0.0.1:8775/` — **not** 8765/8766 |
| **8500** | Ace apex (legacy trading paper) | publish.py reads only; not the Utah deck |
| **5433** | Utah Postgres | `~/.utah/pgdata` |

**Do not open `localhost:8765` for Utah.** On this machine that port was claimed by Sovereign's
dashboard. Utah localhost is always **8766**.

---

## MCP

| Server | Path | Stack |
|--------|------|-------|
| `capstone` | `~/.ace/capstone-mcp/run` | Legacy Ace MCP (still registered) |
| `utah_gateway` | `~/.utah/capstone-mcp/run` | Utah MCP — use this for Project Utah work |

---

## launchd labels

| Active (Utah) | Purpose |
|---------------|---------|
| `com.utah.supervisor` | ONE root — daemon + web deck + voice |
| `com.utah.postgres` | Utah DB |
| `com.utah.tailserve` | Tailscale :8765 → :8766 |
| `com.utah.leads` / `probate` / `marketer` / … | Revenue cron (interval, not KeepAlive) |
| `com.blacklabelbots.publish` | Sanitized site stats every 3h |

| Archived (Ace) | |
|----------------|--|
| `com.ace.*` | Moved to `~/Library/LaunchAgents/_archived-ace/` — do not bootstrap |

| Sellable (Sovereign) | |
|----------------------|--|
| `com.sovereign.daemon` | Stop when running Utah; use ports 8775/8776 locally |

---

## Cleanup

```bash
cd ~/Desktop/ProjectUtah
./ops/cleanup-boundaries.sh          # dry-run
./ops/cleanup-boundaries.sh --apply  # archive Ace plists, stop Sovereign conflict, bootstrap Utah
```

After cleanup, open the deck: **http://127.0.0.1:8766/**
