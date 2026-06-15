"""The conversion unlock: the cold email links the prospect to THEIR own pre-built
preview when (and only when) previews are published. Proven without network/DB."""
from __future__ import annotations

from utah.product import outreach

FOOTER = {"address": "28 Dogwood Rd, Newnan GA 30263", "unsubscribe": "Reply STOP to opt out."}
URL = "https://previews.blacklabelbots.com/newnan-handyman-co"


def _lead(preview=None):
    contact = {"phone": "+17705551212"}
    if preview:
        contact["site"] = {"preview_url": preview}
    return {"name": "Newnan Handyman Co", "kind": "handyman", "region": "Newnan GA",
            "contact": contact}


def test_preview_link_in_pitch_when_live_and_present(monkeypatch):
    monkeypatch.setattr(outreach.config, "previews_live", lambda: True)
    m = outreach.compose(_lead(URL), "smb_no_website", FOOTER)
    assert URL in m["body"]                         # the prospect's own site, linked
    assert "take a look" in m["subject"]            # conversion subject
    assert "make it yours" in m["body"]
    assert FOOTER["address"] in m["body"]           # CAN-SPAM footer still stamped


def test_gated_off_never_links_a_dead_preview(monkeypatch):
    monkeypatch.setattr(outreach.config, "previews_live", lambda: False)
    m = outreach.compose(_lead(URL), "smb_no_website", FOOTER)
    assert "previews.blacklabelbots.com" not in m["body"]   # host not up -> no dead link
    assert "examples of my work" in m["body"]               # honest fallback copy


def test_live_but_no_preview_falls_back(monkeypatch):
    monkeypatch.setattr(outreach.config, "previews_live", lambda: True)
    m = outreach.compose(_lead(), "smb_no_website", FOOTER)   # lead has no site yet
    assert "previews.blacklabelbots.com" not in m["body"]
    assert "examples of my work" in m["body"]


def test_lead_preview_url_requires_https():
    assert outreach._lead_preview_url({"contact": {"site": {"preview_url": URL}}}) == URL
    assert outreach._lead_preview_url({"contact": {"site": {"preview_url": "http://x"}}}) == ""
    assert outreach._lead_preview_url({"contact": {}}) == ""
    assert outreach._lead_preview_url({}) == ""
