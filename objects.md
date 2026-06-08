# PART V — FOUNDATION · (B) OBJECTS (type & contract model), from the dirt

## V.B.0 Ground truth (dirt)
- Core agent contracts (`acesd/core/types.py`) = **`@dataclass(slots=True)`**:
  `HealthStatus`, `AgentResult`, `AgentContext`, `OverrideResponse`,
  `HumanVerdict`, + `HealthState(str, Enum)`, `EscalationFuture(asyncio.Future)`,
  abstract `AgentMemory`. The file calls itself "the **frozen interface** every
  track compiles against."
- IPC boundary (`acesd/ipc/schema.py`) = **pydantic v2 BaseModel** (72 req/resp
  models, validated).
- Everything else = **raw `dict[str, Any]` bags** (payloads, retrieval,
  world-model slots, the `~/.ace/*.json` state files).

## V.B.1 Failures
1. **Interface-drift crash** — `AgentResult` has **no `payload=` field**; agents
   calling `AgentResult(payload=...)` crashed (`knowledge_ingest`, CLAUDE.md §6).
   Mutable, unvalidated dataclass + a drifting field set = runtime crash.
2. **Stringly-typed fields** — `triggered_by: str` ("cron|event|voice|manual" in a
   *comment*), tiers as ints/strings → invalid values uncaught.
3. **`Any` bags** — `payload: dict[str,Any]`, `retrieval: list[dict[str,Any]]`,
   `evidence_collected: list[Any]` → no validation; the seam where type safety dies
   and garbage/confabulation enters.
4. **Three representations** — dataclass (internal) ↔ pydantic (IPC) ↔ raw
   dict/JSON (storage/files) → translation layers + drift between them.
5. **Mutable "frozen" contracts** — "frozen" is a comment, not `frozen=True`.

## V.B.2 Web SOTA (sourced)
- **msgspec** (Rust): immutable `Struct` + `__slots__`, **2–5× faster** encode/
  decode and **~19× faster init** than pydantic, ~40% less memory; strict typing
  → best for high-throughput serialization/validation.
- **pydantic v2**: best at validating **untrusted/external** data (coercion,
  ergonomics) but 2–3× overhead in tight loops → use at boundaries, not hot paths.
- **attrs**: balanced internal models (slots + composable validators + cattrs).
- **Immutable/frozen** structs prevent the whole mutation/drift bug class.

## V.B.3 Utah object model (design)
Utah does a *ton* daily → serialization volume matters, and types must be safe and
**one model**:
- **`msgspec.Struct` is the core object/type system — immutable (frozen) + slotted
  + Rust-fast.** The SAME struct flows end-to-end: serialized to the **control
  socket (JSON)**, the **WIN data plane (binary)**, and mapped to **Postgres rows**
  — one model, no dataclass↔pydantic↔dict translation, no drift.
- **pydantic v2 only at the untrusted boundary** (external API inputs, config)
  where coercion/ergonomics earn the cost.
- **Enums, not strings** (`triggered_by`, health, verification tier).
- **No `dict[str,Any]` on contracts** — typed structs / tagged unions; `Any` only
  at a validated ingress that immediately parses into a struct.
- **Frozen + versioned contracts** — each carries a version; changes are additive +
  validated → `AgentResult(payload=)` becomes a **validation/type error, not a
  runtime crash**.
- Keep Ace's **frozen-contract discipline** (Agent base / IPC schema / MCP
  protocol) — but **enforced by the type system**, not a docstring.

## V.B.4 Quantified
| Dimension | Ace | Utah |
|---|---|---|
| object systems | 3 (dataclass + pydantic + raw dict/`Any`) | 1 core (**msgspec Struct**) + pydantic at boundary |
| mutability | mutable contracts (`slots`, not frozen) | **immutable/frozen** structs |
| typed fields | stringly-typed + `Any` bags | **enums + typed structs**, no `Any` on contracts |
| serialize speed | pydantic baseline | **msgspec 2–5× faster, ~19× init, ~40% less mem** |
| representations | dataclass↔pydantic↔dict (drift) | **one struct** across IPC/WIN/Postgres |
| drift safety | `AgentResult(payload=)` → runtime crash | validation/type error |

## V.B.5 Keep / Kill / Add
| | |
|---|---|
| **KEEP** | the frozen-contract concept (Agent base / IPC schema / MCP protocol) · slotted objects · enums where used (`HealthState`) |
| **KILL** | mutable dataclass contracts · `dict[str,Any]` payload/retrieval bags · stringly-typed `triggered_by` · the 3-representation drift · the unvalidated-kwarg crash path |
| **ADD** | **msgspec.Struct core** (immutable, one model across IPC/WIN/Postgres) · pydantic-only-at-boundary · enums everywhere · **versioned contracts enforced by the type system** |

Sources: msgspec vs pydantic v2 benchmark https://hrekov.com/blog/msgspec-vs-pydantic-v2-benchmark · jcrist init benchmark https://gist.github.com/jcrist/9bfe44f60533225d5f8383791f2fe734 · dataclasses/pydantic/attrs guide https://tildalice.io/python-dataclasses-pydantic-attrs/
