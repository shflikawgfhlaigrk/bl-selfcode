"""sitegen checkout button — the deliverable is the sales page. Proven pure: a real
checkout link renders a Buy button (in the hero + contact), an absent one renders none
(honest fallback, no fake button), and the URL is HTML-escaped into the attribute."""
from __future__ import annotations

from utah.product import sitegen

_LEAD = {"name": "La Monarca", "kind": "restaurant", "region": "Newnan GA",
         "contact": {"phone": "+15551234567"}}


def test_buy_button_renders_when_checkout_url_given():
    html = sitegen.render(_LEAD, checkout_url="https://buy.stripe.com/test_abc123")
    assert html.count("Buy this site — $700") == 2          # hero + contact CTA
    assert 'href="https://buy.stripe.com/test_abc123"' in html
    assert 'class="btn buy"' in html


def test_no_button_without_checkout_url():
    html = sitegen.render(_LEAD)                            # default: no checkout configured
    assert "Buy this site" not in html
    assert "btn buy" not in html
    # still a real, usable preview — the contact/call CTA remains
    assert "tel:+15551234567" in html


def test_checkout_url_is_escaped_into_attribute():
    # a hostile URL must not break out of the double-quoted href
    html = sitegen.render(_LEAD, checkout_url='https://x/"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in html
    assert "&quot;&gt;&lt;script&gt;" in html


def test_generate_reports_checkout_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(sitegen.config, "checkout_url", lambda: "")
    off = sitegen.generate(_LEAD, out_dir=tmp_path)
    assert off["written"] is True and off["checkout_ready"] is False

    monkeypatch.setattr(sitegen.config, "checkout_url",
                        lambda: "https://buy.stripe.com/test_live")
    on = sitegen.generate(_LEAD, out_dir=tmp_path)
    assert on["checkout_ready"] is True
    assert "Buy this site — $700" in (tmp_path / f"{sitegen.slug(_LEAD['name'])}.html").read_text()
