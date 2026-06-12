"""The two sellable artifacts: probate lead-list XLSX + SMB preview site. Both pure
renders with injectable rows — no Postgres, no Desktop writes (tmp_path)."""
from __future__ import annotations

import zipfile
from io import BytesIO

from utah.product import probate_export, sitegen


def _rows():
    return [
        {"case_name": "ELIZABETH JANE STOWE", "county": "houston", "filed": None,
         "heir_contact": {}, "arv": 310900, "status": "new"},
        {"case_name": "LAWRENCE FRANKLIN DAVENPORT", "county": "harris", "filed": None,
         "heir_contact": {"owner_mail": {"street": "1075 MYHAND RD",
                                         "city": "PINE MOUNTAIN", "zip": "31822"}},
         "arv": 14264, "status": "new"},
    ]


def test_xlsx_is_valid_zip_with_required_parts_and_data(tmp_path):
    r = probate_export.export(rows=_rows(), out_path=tmp_path / "list.xlsx")
    assert r["written"] and r["rows"] == 2
    with zipfile.ZipFile(r["path"]) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "xl/workbook.xml",
                "xl/worksheets/sheet1.xml", "xl/styles.xml"} <= names
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "Elizabeth Jane Stowe" in sheet and "310900" in sheet
    assert "1075 MYHAND RD, PINE MOUNTAIN, 31822" in sheet
    # HONESTY: assessed values are labeled as assessed, and the license rides in-file
    assert "county tax-assessed" in sheet
    assert "resale or redistribution" in sheet


def test_xlsx_escapes_xml_and_honest_empty(tmp_path):
    rows = [{"case_name": 'A & B <Estate> "Q"', "county": "x", "heir_contact": {},
             "arv": None, "status": "new"}]
    r = probate_export.export(rows=rows, out_path=tmp_path / "e.xlsx")
    with zipfile.ZipFile(r["path"]) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "&amp;" in sheet and "&lt;Estate&gt;" in sheet     # no broken XML
    assert "unresolved" in sheet                              # no-arv labeled, not faked
    empty = probate_export.export(rows=[], out_path=tmp_path / "n.xlsx")
    assert empty["written"] is False and "no probate rows" in empty["reason"]


def _lead():
    return {"name": "Your man friday", "kind": "general_contractor",
            "region": "Maps Augusta GA [handyman]",
            "contact": {"phone": "+13364131805",
                        "address": "643 Bent Creek Dr, Evans, GA 30809"}}


def test_sitegen_renders_real_lead_fields_no_fabrication(tmp_path):
    html = sitegen.render(_lead())
    assert "Your man friday" in html
    assert 'href="tel:+13364131805"' in html                  # tap-to-call wired
    assert "643 Bent Creek Dr" in html and "maps.google.com" in html
    assert "Augusta GA" in html and "[handyman]" not in html  # scout tag stripped
    # HONESTY: no fabricated social proof, placeholders are explicit
    import re as _re
    for fake in (r"\breviews?\b", r"\bstars?\b", "★", r"\btestimonials?\b", r"\b5\.0\b"):
        assert not _re.search(fake, html.lower()), fake
    assert "Photos of your real" in html
    r = sitegen.generate(_lead(), out_dir=tmp_path)
    assert r["written"] and r["slug"] == "your-man-friday"
    assert r["preview_url"] == "https://previews.blacklabelbots.com/your-man-friday"
    assert r["preview_published"] is False
    assert (tmp_path / "your-man-friday.html").read_text().startswith("<!doctype html>")


def test_sitegen_handles_minimal_lead_and_refuses_nameless(tmp_path):
    html = sitegen.render({"name": "Solo Shop", "kind": "", "contact": {}})
    assert "Solo Shop" in html and "Get in touch" in html      # no phone -> no tel: link
    assert "tel:" not in html
    r = sitegen.generate({"name": "  ", "contact": {}}, out_dir=tmp_path)
    assert r["written"] is False and "no business name" in r["reason"]
