# 21 — Utah Full Access + Ace Import (config & procedure)

> How to (a) import Ace's assets into Utah and (b) give Utah full access and
> authorization to everything — grounded in Utah's ACTUAL model, not memory.
> Binding rules honored: **full access, never revoke** (`ace-full-access-never-revoke`)
> and **zero `~/.ace`** clean-room isolation (`project-utah-rebuild`). Ace's data &
> creds are **imported/replicated** into Utah's own namespace, never live-bridged.

## 0. The key fact: there is almost nothing to "unlock"

Utah has **NO capability broker, NO deny-by-default, NO sandbox** (it did **not**
inherit Sovereign's broker). The daemon runs as you (`gui/$uid`, no root) and
capabilities call `subprocess`/`urllib`/sockets **directly**. So Utah already has
your full filesystem / network / system reach.

"Full access" therefore = **3 concrete things**, not removing security:

| # | Lever | Mechanism |
|---|---|---|
| 1 | Un-gate integrations | drop credential files into `~/.utah/secrets/` |
| 2 | Self-coding autonomy | flag files in `~/.utah/run/` |
| 3 | Ace-parity reach (Gmail/130 MCP/etc.) | replicate Ace's gateway natively (build) |

The **only** real auth gate is a peer-cred check on the control socket
(`utah/daemon/peercred.py`): owner-uid only. You already pass it — **no change
needed for owner full access**. The only fs restriction is `assert_isolated()`
(`utah/daemon/runtime.py:33`) which blocks `~/.ace` — keep it (it enforces the
clean-room rule); we import via a read-only copy instead.

## 1. Integration credentials (`~/.utah/secrets/<name>.json` — presence = ON)

Each integration self-disables when its file is **absent** (returns `gated:true`,
never fakes). Activation = the file lands. Dir is `0700`, owner-only.

| File | Un-gates | Value needed | Status |
|---|---|---|---|
| `macos.json` | notes, contacts, clipboard, notify, shortcuts | `{}` (existence only) | ✅ **applied** |
| `gmail.json` | mail + outreach send + brief email | `{"from","app_password","smtp_host":"smtp.gmail.com"}` | ⏳ needs your Gmail **app password** |
| `google.json` | Calendar create_event | OAuth `{client_id,client_secret,refresh_token}` | ⏳ template written; needs values + adapter |
| `instagram.json` / `tiktok.json` | marketer posting | channel tokens | ⏳ template; `_real_publish` is a stub (needs code) |
| `external.json` | keyed finance quotes | `{"api_key"}` | optional (weather already free) |
| `chrome.json` | headless browser | — | ✅ auto-detected, already live |

> `*.example` templates for google/instagram/tiktok/external are in
> `~/.utah/secrets/`. Fill one, drop the `.example`, then restart (§4).
> macOS TCC prompts are enforced by the OS on first `osascript`/`pbpaste` — grant
> `~/.utah/venv/bin/python` Automation/Accessibility in System Settings.

## 2. Self-coding autonomy (`~/.utah/run/` flag files)

| Flag file | Effect | Current |
|---|---|---|
| `selfcode.disabled` | present = kill switch (instantly off) | **absent** = self-coding ON |
| `selfcode.automerge` | present = green changes auto-commit → merge `main` → push origin (no review) | **absent** = propose-only branch |

Both states still require a **green pytest suite** or the change is
`git reset --hard` rolled back (`utah/selfcode.py:130`). Enabling auto-merge
overrides the `stop@13` doctrine — **see decision below**.

```bash
# FULL autonomy (auto-merge):   touch ~/.utah/run/selfcode.automerge
# Pause everything:             touch ~/.utah/run/selfcode.disabled
```

## 3. Import Ace → Utah

**Already imported (one real data copy):** 9,240 knowledge facts
(`ace.db semantic_memory` → Utah Postgres `memory`, source=`fact`) + derived
entity graph (9,601 entities). Leads/probate/outreach are **freshly produced** by
Utah capabilities (962 OSM leads), not copied from Ace.

```bash
# Re-run / top-up knowledge (idempotent — dedup@0.995 only adds genuinely-new facts)
PYTHONPATH=/Users/michaelbarber/Desktop/ProjectUtah \
  ~/.utah/venv/bin/python -m migrations.ace_knowledge

# Optional: import Ace's 551 email-bearing law-firm leads as DATA (needs a new
# importer modeled on ace_knowledge.py → utah.product.ledger.record_lead)
```

**Final cutover (14-migrate step 10) — NOT yet done; both stacks live:**
```bash
launchctl bootout gui/$(id -u)/com.ace.daemon   # + the other ~46 com.ace.* jobs
# com.utah.supervisor already KeepAlive owns daemon+web+voice
```

## 4. How config takes effect

No live reload — config/env read at import. After editing `utah/config.py`, plist
env, or dropping a secrets/flag file:
```bash
launchctl kickstart -k gui/$(id -u)/com.utah.supervisor   # restarts daemon+web+voice
```
Env seams go in the supervisor plist `EnvironmentVariables` (inherited by all
children via `Popen(env={**os.environ})`): `UTAH_DSN`, `UTAH_BRAIN`,
`UTAH_LOCAL_*`, `UTAH_POOL_LIMIT`, `UTAH_MAX_LOAD_PER_CORE`, etc.

## 5. Ace-parity "reach everything" (mirrors Ace; namespace `com.utah.*`)

> **v1 BUILT + PROVEN (2026-06-07)** — native gateway at `~/.utah/capstone-mcp/`
> (own git repo, outside the tree). Tools: `inventory`, `utah_ipc` (any daemon
> control method over `utahd.sock`), `db_query` (read-only Postgres), `fs_list/
> fs_read/fs_write` (full reach, **refuses `~/.ace`**), `web_fetch`, `list_auths`
> (names only). Registered in `~/.claude/settings.json` → `mcpServers.utah_gateway`
> (loads next session). selftest 0 failures. **v2 remaining:** `mcp_call` proxy to
> downstream MCP servers (the "~130 servers" reach) + `gmail_send`/`imessage_send`
> comms via a `com.utah.oauth` Keychain adapter (one-time copy of Ace's tokens).

Ace's full reach = 5 mechanisms; replicate **natively** (never point at `~/.ace`):

1. **One gateway** `~/.utah/capstone-mcp/` (outside the tree so self-coding can't
   wipe it) fronting Postgres/SQLite + daemon IPC + loopback HTTP + filesystem +
   comms + a `mcp_call` proxy to downstream MCP servers. Register in
   `~/.claude/settings.json` → `mcpServers.utah_gateway`.
2. **No prompt blocks** — `permissions.defaultMode: "bypassPermissions"`.
3. **Spawned `claude -p`** carries `--dangerously-skip-permissions`.
4. **Credentials** in Keychain `com.utah.oauth` / `com.utah.secret`, exposed as
   **names only**; act through the owning adapter (one-time copy Ace's
   `com.ace.oauth` tokens into `com.utah.oauth` — copy, not live read).
5. **Run as user** (already true — `com.utah.*` LaunchAgents, no root).

Keep the only legit clamp: raw SQL read-only (`SELECT/WITH/PRAGMA/EXPLAIN`);
mutations go through typed write tools / the ledger. Port Ace's
UNTRUSTED_GUARD spotlighting so "no gating" doesn't expose spawned claude to
prompt-injection from web/email content.

---
*Applied 2026-06-07: `macos.json={}`, keyed templates, knowledge import verified
(9,240 facts). Pending your call: autonomy level (§2) + parity approach (§5).*
