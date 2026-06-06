# 8 — BINARY audit (formats, encoding, the wire)

> How bytes are encoded on the wire and at rest. Dirt (`win_b.py`, `rag.py`,
> `semantic_memory.py`) + web SOTA on binary formats.

## 1. What we had
- **Vectors:** raw **float32 blobs** via `struct.pack/unpack("{n}f")` +
  `np.frombuffer(blob, float32)` (rag.py, consolidator.py, semantic_memory.py).
- **Audio:** int16 PCM bytes (`audio_int16_bytes`, Piper).
- **WIN-B codec** (`acesd/win/win_b.py`): `MAGIC b"WIN\x01"` + a `struct` header/flags
  + a **CBOR body (`cbor2`)** — gated by `cbor2_available()`.
- **Installed:** `flatbuffers` 25.12.19, `protobuf` 7.35.0, `pyarrow` 24.0.0.
  **Absent:** `cbor2`, `msgpack`, `msgspec`, `capnp`.
- **Everything else = JSON** (control + the would-be data plane's fallback).

## 2. Why we did it
JSON for control (human-debuggable). Raw float32 `struct` blobs for vectors were
cheap and numpy-friendly. **CBOR** was a reasonable pick for the WIN data plane —
compact + lossless floats (unlike JSON). protobuf/flatbuffers/pyarrow rode in only
as transitive deps (google libs, onnxruntime, DuckDB/Lance).

## 3. What we didn't think about
- **`cbor2` was never installed** → `cbor2_available()` = False → **WIN-B never ran;
  it silently fell back to JSON.** The "fast binary path" was vapor.
- **CBOR still requires a decode** (copy + alloc) — not zero-copy; for ticks/audio
  at rate even CBOR isn't ideal.
- **JSON on numerics** loses NaN/Inf and is slow/allocation-heavy.
- **Hand-rolled `struct.unpack("{n}f")` vectors** scattered across modules =
  maintenance burden, and the vector store (sqlite-vec) was brute-force anyway.
- **No single binary story** — struct here, CBOR there, JSON elsewhere, flatbuffers
  installed-but-unused.

## 4. What we're gonna change
- **WIN data plane = FlatBuffers (zero-copy, already installed)** for structured
  binary; **packed fixed-struct + `numpy.frombuffer`** for the flattest hot arrays
  (ticks `ts,o,h,l,c,v`; int16 PCM) — **true zero-copy** (read direct from the
  buffer, no decode). **Fail-loud** if a codec is missing (never silent-JSON).
- **Drop `cbor2`** (absent/inert) and the scattered hand-rolled vector `struct` —
  **pgvector stores/searches vectors natively** (the DB owns that binary).
- **Arrow** for columnar windows → DuckDB (zero-copy into the OLAP tier).
- **JSON stays control-plane only;** `msgpack` optional for medium-rate compact
  telemetry (compact but not zero-copy).
- **One coherent chain:** `msgspec.Struct` (objects) ↔ **FlatBuffers/packed-struct**
  on the wire ↔ **pgvector / Arrow** at rest.

**The binary taxonomy (the differences):**
| Format | Zero-copy? | Use |
|---|---|---|
| JSON | no — text, lossy floats | control plane only |
| protobuf / msgpack / CBOR | no — decode = copy+alloc | compact, medium-rate; not the hot path |
| **FlatBuffers / Cap'n Proto** | **yes** — read fields straight from the buffer | structured hot path (WIN) |
| **packed struct + numpy** | **yes** | fixed numeric arrays (ticks/audio/vectors) |
| **Arrow** | yes — columnar | tables → DuckDB analytics |

## 5. How it helps
- The binary data plane **actually runs** (flatbuffers installed; **zero `cbor2`
  dependency**) and is **fail-loud**, not silently degraded to JSON.
- **Zero-copy reads** (no parse/alloc) on ticks/audio/telemetry → ns-scale on the
  hot path; **exact floats** (no NaN/Inf loss).
- **Vectors handled natively by pgvector** → the scattered hand-rolled `struct`
  code disappears.
- One coherent binary story (wire ↔ at-rest) instead of struct/CBOR/JSON/unused-FB.

Sources: Protobuf vs FlatBuffers vs Cap'n Proto (2026) https://techbytes.app/posts/api-design-2026-protobuf-vs-flatbuffers-vs-capn-proto/ · buffer benchmarks https://github.com/kcchu/buffer-benchmarks · FlatBuffers benchmarks https://flatbuffers.dev/benchmarks/ · Arrow zero-copy https://arxiv.org/pdf/2504.06151
