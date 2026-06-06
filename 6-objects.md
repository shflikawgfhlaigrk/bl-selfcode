# 6 — OBJECTS audit (type & contract model)

> The data objects/types/contracts that flow through the system. Dirt
> (`acesd/core/types.py`, `ipc/schema.py`) + web SOTA.

## 1. What we had
- **Core agent contracts** (`types.py`) = `@dataclass(slots=True)`: `HealthStatus`,
  `AgentResult`, `AgentContext`, `OverrideResponse`, `HumanVerdict`, +
  `HealthState(str,Enum)`, `EscalationFuture`. The file calls itself the "frozen
  interface every track compiles against."
- **IPC boundary** (`ipc/schema.py`) = **pydantic v2** (72 validated models).
- **Everything else** = raw **`dict[str, Any]`** bags (payloads, retrieval,
  world-model slots, `~/.ace/*.json`).

## 2. Why we did it
Lightweight slotted dataclasses for the internal contract (fast, no deps); pydantic
at the IPC edge for validation; plain dicts for "flexible" payloads so agents could
pass whatever. Three tools, each locally sensible.

## 3. What we didn't think about
- **Interface-drift crash:** `AgentResult` has **no `payload=` field** →
  `AgentResult(payload=...)` crashed (`knowledge_ingest`). Mutable, unvalidated
  dataclass + drifting fields = runtime crash.
- **Stringly-typed fields** (`triggered_by: str` in a *comment*) → invalid values
  uncaught.
- **`Any` bags** (payload/retrieval/evidence) → no validation; the seam where type
  safety dies and garbage/confabulation enters.
- **Three representations** (dataclass ↔ pydantic ↔ dict/JSON) → translation +
  drift.
- **"Frozen" was a comment**, not `frozen=True` → contracts are mutable.

## 4. What we're gonna change
- **`msgspec.Struct` as the core object system** — **immutable (frozen) + slotted +
  Rust-fast.** The SAME struct serializes to the **control socket (JSON)**, the
  **WIN data plane (binary)**, and maps to **Postgres rows** — one model, no
  translation/drift.
- **pydantic v2 only at the untrusted boundary** (external API/config).
- **Enums, not strings** (`triggered_by`, health, tier); **no `dict[str,Any]` on
  contracts** (typed structs / tagged unions; `Any` only at a validated ingress).
- **Frozen + versioned contracts** enforced by the type system → `AgentResult(payload=)`
  becomes a validation/type error, not a runtime crash. Keep Ace's frozen-contract
  discipline, but enforced not documented.

## 5. How it helps
| | Ace | Utah |
|---|---|---|
| object systems | 3 (dataclass+pydantic+dict/`Any`) | 1 core (msgspec) + pydantic at edge |
| mutability | mutable contracts | immutable/frozen |
| typing | stringly-typed + `Any` bags | enums + typed structs, no `Any` |
| serialize | pydantic baseline | **msgspec 2–5× faster, ~19× init, ~40% less mem** |
| representations | 3 (drift) | 1 across IPC/WIN/Postgres |
| drift safety | runtime crash | validation/type error |

For a system doing a *ton* daily, msgspec's speed serves every IPC msg / WIN frame /
memory row; immutability kills the whole mutation/drift bug class.

Sources: msgspec vs pydantic benchmark https://hrekov.com/blog/msgspec-vs-pydantic-v2-benchmark · init benchmark https://gist.github.com/jcrist/9bfe44f60533225d5f8383791f2fe734 · dataclasses/pydantic/attrs https://tildalice.io/python-dataclasses-pydantic-attrs/
