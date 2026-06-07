"""Curated, durable knowledge packs seeded into memory as grounded facts.

These are *grounding*, not model recall: the local quick model half-hallucinated
the Mark Douglas rules in testing, so the real content is admitted as
``source="fact"`` and served by recall. Idempotent (memory dedup).
"""
