"""Probate lead-list EXPORT — the sellable data product. Renders the probate table into
a branded, investor-ready .xlsx with HONEST value labeling (county-assessed values are
labeled as such — never presented as sold-comps; the claims-audit rule applies to the
product itself). Zero new dependencies: a minimal hand-rolled XLSX writer (an .xlsx is a
zip of XML; inline strings, one sheet, bold header) keeps the buyer repo dependency-free.

Boundary injectable (``rows=``) so tests never touch Postgres; honest-empty on no data.
"""
from __future__ import annotations

import logging
import re
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

log = logging.getLogger("utah.product.probate_export")

#: Column layout: (header, probate-row key, kind) — kind 'n' numeric, 's' string.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("Case", "case_name", "s"),
    ("County", "county", "s"),
    ("Filed", "filed", "s"),
    ("Assessed value ($)", "arv", "n"),
    ("Value basis", "value_basis", "s"),
    ("Mailing address", "mail_addr", "s"),
    ("Status", "status", "s"),
)

LICENSE_NOTE = ("Licensed for the purchaser's own acquisition outreach only — "
                "resale or redistribution of this list is prohibited. Values are county "
                "tax-assessed figures, not appraisals or comp analyses. © Black Label Bots")


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _cell(ref: str, value, kind: str, style: int = 0) -> str:
    if kind == "n" and value not in (None, ""):
        try:
            return f'<c r="{ref}" s="{style}" t="n"><v>{float(value)}</v></c>'
        except (TypeError, ValueError):
            pass
    txt = "" if value is None else str(value)
    return (f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
            f"{_esc(txt)}</t></is></c>")


def _col_letter(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def xlsx_bytes(rows: list[dict], *, title: str) -> bytes:
    """Minimal valid single-sheet .xlsx. Row 1 = branded title, row 2 = license note,
    row 3 = bold headers, data after. Opens clean in Excel/Numbers/Sheets (inline
    strings, no sharedStrings table needed)."""
    body: list[str] = []
    ncols = len(COLUMNS)
    body.append(f'<row r="1">{_cell("A1", title, "s", 1)}</row>')
    body.append(f'<row r="2">{_cell("A2", LICENSE_NOTE, "s", 0)}</row>')
    hdr = "".join(_cell(f"{_col_letter(c)}3", h, "s", 1)
                  for c, (h, _, _) in enumerate(COLUMNS))
    body.append(f'<row r="3">{hdr}</row>')
    for n, row in enumerate(rows, start=4):
        cells = "".join(_cell(f"{_col_letter(c)}{n}", row.get(key), kind)
                        for c, (_, key, kind) in enumerate(COLUMNS))
        body.append(f'<row r="{n}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="2"><xf xfId="0"/><xf xfId="0" fontId="1" applyFont="1"/></cellXfs>'
              "</styleSheet>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Probate Leads" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
               '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/'
               '2006/relationships/styles" Target="styles.xml"/></Relationships>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                 '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
              'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
              'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
              '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
              'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
              '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
              'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/styles.xml", styles)
    return buf.getvalue()


def _shape(raw: dict) -> dict:
    """DB row → export row. Honest value basis: assessed, labeled — never 'ARV/comps'."""
    hc = raw.get("heir_contact") or {}
    mail = hc.get("owner_mail") or hc.get("situs_mail") or {}
    addr = ", ".join(p for p in (mail.get("street"), mail.get("city"), mail.get("zip")) if p)
    return {
        "case_name": (raw.get("case_name") or "").title(),
        "county": (raw.get("county") or "").title(),
        "filed": str(raw.get("filed") or ""),
        "arv": raw.get("arv"),
        "value_basis": "county tax-assessed" if raw.get("arv") else "unresolved",
        "mail_addr": addr,
        "status": raw.get("status") or "new",
    }


def export(*, rows: list[dict] | None = None, ledger=None, county: str | None = None,
           out_path: str | Path | None = None) -> dict:
    """Build the sellable list. ``rows`` injectable for tests; otherwise reads the live
    probate table (optionally one county — the per-county product unit). Honest-empty:
    no rows → no file, a documented reason, never a fabricated sheet."""
    if rows is None:
        if ledger is None:
            from utah.product.ledger import Ledger
            ledger = Ledger()
        raw = ledger.probate_all() if hasattr(ledger, "probate_all") else []
        rows = [r for r in raw if not county or (r.get("county") or "").lower() == county.lower()]
    shaped = [_shape(r) for r in rows]
    if not shaped:
        return {"written": False, "rows": 0, "reason": "no probate rows for this selection"}
    label = (county or "georgia").lower().replace(" ", "-")
    title = (f"BLACK LABEL BOTS — PROBATE LEADS — {(county or 'Georgia').upper()} — "
             f"{date.today().isoformat()}")
    data = xlsx_bytes(shaped, title=title)
    path = Path(out_path) if out_path else (
        Path.home() / "Desktop" / f"probate-leads-{label}-{date.today().isoformat()}.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    log.info("probate export: %d rows -> %s (%d bytes)", len(shaped), path, len(data))
    return {"written": True, "rows": len(shaped), "path": str(path), "bytes": len(data)}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


__all__ = ["export", "xlsx_bytes", "COLUMNS", "LICENSE_NOTE", "slug"]
