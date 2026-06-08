# BASELINE FEATURE CATALOG — everything Ace was supposed to do → Utah done-right

> The union of EVERY Ace plan/task-sheet, de-duplicated: `ACE_EXECUTION_TASK_SHEET`
> (303 tasks), `ACE_MASTER_PLAN`(+V2) (344 steps), `AGI-BLUEPRINT`,
> `AGI-BUILD-ROADMAP` (13 BOXes), `PROJECT-UTAH`, `MICHAEL_BACKLOG`,
> `ACE_REAL_GAP_AUDIT`, `ACE_JARVIS_COMPLETION_FORECAST`, `FOUNDATION_PLAN`, and the
> 73-agent roster. This is the **baseline**: all of it must be live + correct
> before "compound." Each line carries its done-right gate.

## 1. Interface — unified voice + chat agentic harness
- Wake ("Hey Ace/Utah") → STT → same agent loop as typed; utterance shows in chat. **Gate:** spoken == typed pipeline, one session.
- Visible thinking stream (reasoning + every tool call + subagent) like Cursor/Claude. **Gate:** full trace on screen, spoken conclusion via TTS.
- Frontier agentic escalation (shell/file/git/web/subagents/self-edit). **Gate:** does anything this terminal does.
- Mid-sentence wake, intent judge, echo suppression, smart-turn EOU, barge-in, partial-transcript stabilization. **Gate:** natural full-duplex voice.
- Memory-first answer (no fabrication; live-data guard for PnL/weather/engines). **Gate:** "I don't know" beats a guess; live facts never stale.
- Persona (concise, dry, no markdown spoken, owner=Michael). **Gate:** no spoken symbols/asterisks/tags.

## 2. Memory & learning (compounding) — Postgres + pgvector
- Per-turn fact promotion (corrections/preferences/decisions/project-facts/entities). **Gate:** fresh facts recallable within one consolidator tick.
- Hybrid recall: pgvector HNSW + FTS (RRF fusion). **Gate:** within ±2 ranks of fixture set; hybrid actually fuses.
- Admission gate + source discipline + poison filter (no backfill, no synthetic). **Gate:** every row sourced; zero fabricated/news rows in default recall.
- Contradiction detect + supersede (cosine>0.85 → `superseded_by`, never delete). **Gate:** Kokoro-style re-poison resolved nightly w/o manual purge.
- Memory blocks (Letta-style, char-budgeted, labeled). **Gate:** cognition reads blocks not raw dumps.
- Sleep-time consolidator (idle + nightly): rewrite blocks, reindex entities, emit improvement proposals. **Gate:** non-blocking; three-job sequence runs.
- Entity graph wired into compaction; entity-boosted rerank; merge_duplicates nightly. **Gate:** query_neighbors(name,k)≥k; reduces hallucination.
- Decay (active/archived/expired), never delete. **Gate:** stale fades from default recall, recoverable.
- DuckDB analytics over memory (trends, "what I learned over months"). **Gate:** OLAP scans off the hot path.

## 3. World-model & cognition
- World-model bus: slots (time, location, weather, calendar.next, mail.unread, market.regime, engines.heartbeat, thermal, presence, utterance) single-writer, TTL decay, crash-mirror. **Gate:** voice reads slots O(dict) not O(agent run).
- Sensor-push agents (weather/calendar/mail/location/thermal/market/engine) push into bus. **Gate:** additive; slots populate from sensors.
- Cognition tick (5 min): thermal, stale-slot refresh, goal sweep, proactor. **Gate:** completes without blocking voice.
- Presence + quiet hours (AWAY suppresses TTS). **Gate:** no speech when away/quiet.

## 4. Proactivity & autonomy (the compounding layer — on top of baseline)
- Proactor rules engine (≥5 classes) → speak / notify / log / ignore; rejection decay (learns "shut up"). **Gate:** ≥1 useful unprompted/day, ≥70% rated useful.
- Sensory autopilot: ≥30% of daily thoughts unprompted. **Gate:** thoughts.jsonl ≥30%.
- Goal generation (4 passes: failed-crystals, intent-gaps, contradictions, WM-anomalies) → ranked queue. **Gate:** ≥3 grounded goals/week.
- Skill library (≥50 composable, retrieval-indexed, self-verified). **Gate:** ≥40% reuse; ≥1 composed from two.
- Tiered self-coding A/B/C/D (policy-as-data; A autonomous on green after 20 supervised; B batch-review; C 5-ladder; D off-limits byte-checked). **Gate:** kill-switch smoke test refuses; Tier-A merges green w/ 0 rollbacks.
- agent_007 scheduler consumes goal queue → reviewable diffs through gates. **Gate:** D refused at spec level.
- 14-day AGI bar: all 6 conditions green (autopilot, goals, skills, recall, tiers, trading-decoupled). **Gate:** 14 consecutive green days.

## 5. The 11 composites (baseline roster, done-right) — 73 agents
- **Concierge** (planner 05:50 brief, calendar EventKit, mail IMAP triage, tasks Reminders+vault, brief pre-render WAV, courier draft-not-send, notifier, email_compose). **Gate:** spoken plan <30s from real data.
- **Doctor** (L4-only symptom intake + differential + supplement daypart alerts + 21:00 habits + nightly correlation + coach + gym + journal). **Gate:** reasoned answers, no fallback templates; alerts real.
- **Growth** (marketer ideas, outreach CAN-SPAM cold-email, contacts). **Gate:** sends logged w/ message-id, suppression+unsub enforced.
- **Lawyer** (local legal-PDF RAG, discovery template-fill, distinct objections + subpart counts). **Gate:** verbatim-traceable from defense docs.
- **Operator** (macOS control, shortcuts, clipboard, dictation, scribe, apple_notes, timer/alarm, navigator). **Gate:** voice device control works.
- **RealEstate** (probate+foreclosure scrape, why-inference, comps, margin score, 10 profitable/day). **Gate:** 10/day source-traceable, no hallucination.
- **Scout** (lead_scout 500 no-website SMB/day frontier, tech_scout, news, researcher, knowledge_ingest, idea_harvester ≥10/day, scraper_farm, browser). **Gate:** real scraped data, deduped.
- **SelfCode** (agent_007 + tiered gates). **Gate:** §4 gates.
- **Sentinel** (melissa 15s watchdog, observer predictions, connectivity). **Gate:** escalation ladder live; storms contained.
- **Steward** (gardener vault hygiene, nightly_maintenance, librarian, recurring, reflector, self_model, user_profile). **Gate:** memory/self-model fresh.
- **Trading** (trader supervisor, OODA, accountant read-only, fire_commentary, trade_alert <5s phone, trading_brief 17:00). **Gate:** engines decoupled (BROKER=null, AUTO_EXECUTE=false); per-trade reasoning in memory.
- **Weather** (standalone hourly sensor). **Gate:** real forecast, voice intents.

## 6. Revenue engines (the money baseline — done-right gates from the BOXes)
- **Leads (BOX 7):** ≥400 verified law-firm emails + ≥50 no-site SMBs + 500 no-website SMB/day frontier; spaced send 9–5 Tue/Wed/Thu; SMB-no-email → call queue. **Gate:** source-traceable, message-id logged, owner gets test send; **needs: postal address + Resend domain.**
- **Real estate (BOX 4):** 10 profitable probate/day, enriched (comps+ARV+spread), source-linked. **Gate:** owner judges real; **needs: skip-trace for heir contact.**
- **Marketing (BOX 11):** 5 TikTok + 5 IG reels/day; idea→approval→produce→post; analytics back in 24h. **Gate:** posting pipeline exists (the missing piece); genuinely good video (local LTX-2 MLX); URL captured.
- **Trading (BOX 3a–3f):** per-fill phone alert <5s + opinion + reason; ms-precision fills in DB; 17:00 brief with per-trade analytics + engine-change proposal; Truth Social + market news ms-aware. **Gate:** real engine.fired only (no synthetic); honest scorecard.

## 7. Surfaces (HQ / dashboard — one web frontend, event-pushed)
- World-model panel, goals panel, proactor observations+rejections, memory room, agent-status cards (real click-through), failures panel (`/api/failures`), capabilities catalog, forecast panel (state-derived, evidence per field). **Gate:** "if backend produces it, the surface reflects it" (event-driven, no stale poll).

## 8. Integrations & notifications
- Gmail/OAuth (Keychain), Resend (+ domain), Pushover phone alerts (5 streams: trade/brief/leads/sentinel/consolidator, <5s), Tailscale Serve (phone), Calendar (EventKit), Apple Health, Playwright browser, capstone-MCP gateway (one gateway), Stripe (Sovereign-only). **Gate:** each real or clearly OFF with one-line unlock — never gated-and-forgotten.

## 9. Infra, durability & global invariants
- One supervisor (not 46 KeepAlive), bounded backoff + circuit-break, resource governor, unix control socket + WIN data plane, no sync-I/O on loop, one rotated log, stash-verify-swap deploy (never reset --hard), test-DB isolation, fd-limit, OAuth health probe, engine-death alert, kill-switch smoke test, off-limits byte-check.
- Global invariants: G.1 every fact sourced · G.2 cost ledger · G.3 weekly retro · G.4 forecast state-derived · G.5 phone <5s · G.6 thumbs feedback · G.7 failure catalog · G.8 voice-pin · G.10 drift detect · G.11 unrecoverable sentinel · G.12 engine death.

## 10. The 13 BOX acceptance gates (verbatim-in-spirit)
1 Forecast (state-derived, evidence) · 2 Memory (visible, news-quarantined) · 3 Trading (3a–3f) · 4 RealEstate (10/day) · 5 Doctor (clinical+supplements+habits) · 6 Gym+morning debrief+alarm · 7 Leads (400+50) · 8 Lawyer (discovery) · 9 Ideas (≥10/day, Google Doc each) · 10 Memory consolidation (per-compaction phone proof) · 11 Marketer (5+5 reels/day) · 12 Browser (NL action end-to-end) · 13 Decommission off-spec.

**Baseline = §1–§10 all live + correct. Then §4's autonomy compounds.** Nothing
ships as "done" without its gate proven by a live probe (no log/summary).

---
