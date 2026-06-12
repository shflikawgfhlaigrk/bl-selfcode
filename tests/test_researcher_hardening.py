"""Researcher hardening — the page fetch is BYTE-BOUNDED (a multi-GB response must
not eat the daemon's RAM), the search page size is clamped, and a junk gather budget
can never silently slice the whole context away."""
from __future__ import annotations

from utah import failures
from utah.product import researcher
from tests.fakes import FakeFailureStore


class _FakeResp:
    """Stands in for urlopen's response — records how the body was read."""

    def __init__(self):
        self.read_n = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        self.read_n = n
        size = n if n is not None else 8 * 1024 * 1024   # unbounded read = 8 MB body
        return b"x" * size


def test_get_bounds_the_response_read(monkeypatch):
    resp = _FakeResp()
    monkeypatch.setattr(researcher.urllib.request, "urlopen",
                        lambda req, timeout: resp)
    body = researcher._get("https://example.com/huge")
    assert resp.read_n == researcher.MAX_FETCH_BYTES   # capped read, never read()
    assert len(body) <= researcher.MAX_FETCH_BYTES


def test_search_k_is_clamped_to_a_sane_page():
    body = "".join(f'<a class="result__a" href="https://s{i}.com">Title {i}</a>'
                   for i in range(40))
    res = researcher.search("q", k=999, get=lambda url, params: body)
    assert 0 < len(res) <= researcher.MAX_RESULTS
    res_neg = researcher.search("q", k=-5, get=lambda url, params: body)
    assert len(res_neg) == 1                  # negative k clamps to 1, never unbounded


def test_gather_negative_budget_never_slices_the_context_away():
    failures.set_store(FakeFailureStore())
    text = researcher.gather("q", search_fn=lambda q, k=3: [("A", "http://a")],
                             fetch_fn=lambda u: "body " * 100, budget=-5000)
    assert text                               # floor-clamped budget, real context survives
