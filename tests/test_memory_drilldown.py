"""Memory drill-down — the deck must show the ACTUAL rows + entities behind the
{total,live,entities} gauge (Michael's transparency rule: click MEMORY -> see all of
it). list_memories pages live rows newest-first; list_entities lists entities by link
count. Both read-only."""
from __future__ import annotations

from utah import config, memory


def test_list_memories_newest_first_and_shaped(mem):
    memory.store("alpha fact one", source="fact", confidence=0.8)
    memory.store("beta fact two", source="fact", confidence=0.8)
    rows = memory.list_memories(limit=10, offset=0)
    contents = [r["content"] for r in rows]
    assert "alpha fact one" in contents and "beta fact two" in contents
    assert contents.index("beta fact two") < contents.index("alpha fact one")  # newest first
    assert {"id", "content", "source", "confidence", "ts"} <= set(rows[0])


def test_list_memories_paginates(mem):
    for i in range(5):
        memory.store(f"distinct fact numbered {i}", source="fact", confidence=0.8)
    p1 = memory.list_memories(limit=2, offset=0)
    p2 = memory.list_memories(limit=2, offset=2)
    assert len(p1) == 2 and len(p2) == 2
    assert {r["id"] for r in p1}.isdisjoint({r["id"] for r in p2})


def test_list_memories_excludes_archived(mem):
    memory.store("live and visible fact", source="fact", confidence=0.8)
    archived = memory.store("old archived fact", source="fact", confidence=0.8)
    mem.store.rows[archived.id].archived = True
    contents = [r["content"] for r in memory.list_memories(limit=50)]
    assert "live and visible fact" in contents
    assert "old archived fact" not in contents


def test_list_entities_returns_names_with_counts(mem):
    mem.store.insert(
        content="Michael lives in Newnan", source="fact", tags=(), confidence=0.8,
        embedding=[0.0] * config.EMBED_DIM,
        entity_names=["Michael", "Newnan"], supersede_ids=(),
    )
    ents = memory.list_entities(limit=50)
    names = {e["name"] for e in ents}
    assert {"michael", "newnan"} <= {n.lower() for n in names}
    assert all("mentions" in e for e in ents)
