"""stripe_sync — the Stripe -> sales mirror. Proven without Stripe or Postgres via the
injected fetcher + a fake ledger: gated when no key, idempotent across polls, records
only real paid sales, and stays honest (never raises) when Stripe errors."""
from __future__ import annotations

from utah.product import stripe_sync


class FakeLedger:
    """Minimal stand-in for utah.product.ledger.Ledger (the unit-proof seam)."""

    def __init__(self) -> None:
        self.sales: dict[str, dict] = {}
        self.syncs: list[dict] = []
        self.schema_inited = 0

    def init_schema(self) -> None:
        self.schema_inited += 1

    def record_sale(self, stripe_id, **kw) -> bool:
        if stripe_id in self.sales:           # UNIQUE(stripe_id) — never-twice
            return False
        self.sales[stripe_id] = {"stripe_id": stripe_id, **kw}
        return True

    def record_sync(self, source, kind="ingest", rows_in=0, cursor=None,
                    status="ok", detail=None) -> int:
        self.syncs.append({"source": source, "rows_in": rows_in, "status": status})
        return len(self.syncs)


def _charge(cid, *, amount=70000, amount_refunded=0, refunded=False, paid=True, **extra):
    return {"id": cid, "amount": amount, "amount_refunded": amount_refunded,
            "refunded": refunded, "paid": paid, "currency": "usd",
            "created": 1_700_000_000, "description": "Sovereign", **extra}


def test_gated_when_no_key(monkeypatch):
    monkeypatch.setattr(stripe_sync, "_secret_key", lambda: None)
    res = stripe_sync.sync()
    assert res == {"ok": True, "gated": True, "synced": 0, "seen": 0,
                   "reason": res["reason"]}
    assert "no Stripe key" in res["reason"]


def test_records_only_real_paid_sales():
    led = FakeLedger()
    charges = [
        _charge("ch_paid", amount=70000),
        _charge("ch_unpaid", paid=False),                          # never captured
        _charge("ch_refunded", refunded=True, amount_refunded=70000),  # money returned
    ]
    res = stripe_sync.sync(fetch_fn=lambda: charges, ledger=led)
    assert res["ok"] and not res["gated"]
    assert res["synced"] == 1 and res["seen"] == 1 and res["fetched"] == 3
    assert set(led.sales) == {"ch_paid"}
    assert led.sales["ch_paid"]["amount_cents"] == 70000
    assert led.sales["ch_paid"]["currency"] == "usd"
    assert led.schema_inited == 1                                  # table ensured before write
    assert led.syncs[-1]["status"] == "ok" and led.syncs[-1]["rows_in"] == 1


def test_net_amount_after_partial_refund():
    led = FakeLedger()
    # Stripe leaves refunded=False on a PARTIAL refund — it is still a real (net) sale.
    charges = [_charge("ch_partial", amount=70000, amount_refunded=20000)]
    stripe_sync.sync(fetch_fn=lambda: charges, ledger=led)
    assert led.sales["ch_partial"]["amount_cents"] == 50000


def test_idempotent_across_polls():
    led = FakeLedger()
    charges = [_charge("ch_1"), _charge("ch_2")]
    first = stripe_sync.sync(fetch_fn=lambda: charges, ledger=led)
    second = stripe_sync.sync(fetch_fn=lambda: charges, ledger=led)
    assert first["synced"] == 2 and second["synced"] == 0
    assert len(led.sales) == 2


def test_fetch_error_is_honest(monkeypatch):
    monkeypatch.setattr(stripe_sync.failures, "record", lambda *a, **k: None)
    led = FakeLedger()

    def boom():
        raise RuntimeError("stripe 401 invalid key")

    res = stripe_sync.sync(fetch_fn=boom, ledger=led)
    assert res["ok"] is False and res["gated"] is False
    assert "stripe 401" in res["error"]
    assert led.syncs[-1]["status"] == "failed"            # the failed pull is logged, not hidden


def test_completed_at_preserves_stripe_time():
    led = FakeLedger()
    stripe_sync.sync(fetch_fn=lambda: [_charge("ch_t", created=1_700_000_000)], ledger=led)
    completed = led.sales["ch_t"]["completed_at"]
    assert completed is not None and completed.year == 2023   # epoch 1.7e9 -> Nov 2023 UTC


def test_secret_key_reads_file_and_rejects_garbage(tmp_path, monkeypatch):
    p = tmp_path / "stripe.json"
    p.write_text('{"secret_key": "rk_live_demo"}', encoding="utf-8")
    monkeypatch.setattr(stripe_sync, "STRIPE_CREDS", p)
    assert stripe_sync._secret_key() == "rk_live_demo"
    p.write_text("not json", encoding="utf-8")
    assert stripe_sync._secret_key() is None


def test_run_scheduled_is_the_cron_entrypoint(monkeypatch):
    monkeypatch.setattr(stripe_sync, "_secret_key", lambda: None)
    assert stripe_sync.run_scheduled()["gated"] is True


def test_first_picks_first_nonempty_stripped():
    assert stripe_sync._first(None, "", "  ", "x", "y") == "x"
    assert stripe_sync._first(None, "") == ""


def test_fetch_charges_parses_stripe_payload(monkeypatch):
    import json as _json

    class _FakeResp:
        def __init__(self, b): self._b = b
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = _json.dumps({"data": [{"id": "ch_1", "paid": True}]}).encode()
    monkeypatch.setattr(stripe_sync.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResp(payload))
    assert stripe_sync.fetch_charges("rk_live_x", limit=5) == [{"id": "ch_1", "paid": True}]
