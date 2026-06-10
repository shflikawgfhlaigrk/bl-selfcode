#!/usr/bin/env python3
"""Mass-export Utah leads to an Excel workbook: All Leads + ready-to-work sheets
(With Email, With Phone, Never Contacted) + a per-region summary.

Usage: python3 ops/export_leads_xlsx.py [output.xlsx]
Default output: ~/Desktop/leads-export-YYYY-MM-DD.xlsx
"""
import csv
import datetime
import io
import pathlib
import subprocess
import sys

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

PSQL_GLOB = "/opt/homebrew/opt/postgresql@*/bin/psql"
SQL = """\copy (
  SELECT id, name, kind, region, source, status,
         contact->>'phone'   AS phone,
         contact->>'email'   AS email,
         contact->>'website' AS website,
         contact->>'address' AS address,
         ts
  FROM leads ORDER BY region, name
) TO STDOUT WITH (FORMAT csv, HEADER true)"""
HEADERS = ["id", "name", "kind", "region", "source", "status",
           "phone", "email", "website", "address", "ts"]


def psql_bin() -> str:
    hits = sorted(pathlib.Path("/").glob(PSQL_GLOB.lstrip("/")))
    return str(hits[-1]) if hits else "psql"


def fetch_rows() -> list[dict]:
    out = subprocess.run(
        [psql_bin(), "-h", "127.0.0.1", "-p", "5433", "-d", "utah", "-c", SQL],
        capture_output=True, text=True, check=True)
    return list(csv.DictReader(io.StringIO(out.stdout)))


def add_sheet(wb: Workbook, title: str, rows: list[dict]) -> None:
    ws = wb.create_sheet(title)
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(h, "") for h in HEADERS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"
    widths = {"name": 34, "kind": 16, "region": 30, "phone": 15, "email": 30,
              "website": 34, "address": 40, "ts": 22}
    for i, h in enumerate(HEADERS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 10)


def main() -> None:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
        pathlib.Path.home() / "Desktop" / f"leads-export-{datetime.date.today()}.xlsx")
    rows = fetch_rows()
    wb = Workbook()
    wb.remove(wb.active)
    add_sheet(wb, "All Leads", rows)
    add_sheet(wb, "With Email", [r for r in rows if r["email"]])
    add_sheet(wb, "With Phone", [r for r in rows if r["phone"]])
    add_sheet(wb, "Never Contacted", [r for r in rows if r["status"] == "new"])

    summary = wb.create_sheet("By Region")
    summary.append(["region", "leads", "with_email", "with_phone"])
    for c in summary[1]:
        c.font = Font(bold=True)
    regions: dict[str, list[int]] = {}
    for r in rows:
        agg = regions.setdefault(r["region"], [0, 0, 0])
        agg[0] += 1
        agg[1] += bool(r["email"])
        agg[2] += bool(r["phone"])
    for region, (n, e, p) in sorted(regions.items(), key=lambda kv: -kv[1][0]):
        summary.append([region, n, e, p])
    summary.column_dimensions["A"].width = 34

    wb.save(out)
    print(f"Exported {len(rows)} leads -> {out} "
          f"(email {sum(1 for r in rows if r['email'])}, phone {sum(1 for r in rows if r['phone'])})")


if __name__ == "__main__":
    main()
