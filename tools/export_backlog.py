#!/usr/bin/env python3
"""Export the KelpWorks change log (the Excel workbook the team keeps in SharePoint) to CSV files in docs/backlog/, so Claude Code can read an item by its ID.

Standard library only (an .xlsx is a zip of XML). The Excel file stays the master for STATUS, owner and notes; the CSV is a snapshot of the item definitions, so
re-run this after the workbook changes:

    python tools/export_backlog.py "C:\\path\\to\\KelpWorks_ChangeLog_2026-10-16.xlsx"

Sheets exported: "Change Log" -> items.csv and "Open Decisions" -> open-decisions.csv. The workbook's "Claude Code Prompt" column is left out: it was written for a
different tech stack (Flask / SQLAlchemy) and would mislead; the /kw-item command builds its own plan from the repo instead.
"""
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
DROP_COLUMNS = {"Claude Code Prompt"}
SHEETS = {"Change Log": "items.csv", "Open Decisions": "open-decisions.csv"}


def col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheets(path):
    """{sheet name: [row, ...]} with every row a list of strings."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        out = {}
        for s in wb.find("m:sheets", NS):
            target = rels[s.get("{%s}id" % NS["r"])].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            rows = []
            for row in ET.fromstring(z.read(target)).iter("{%s}row" % NS["m"]):
                cells = {}
                for c in row.findall("m:c", NS):
                    t, v = c.get("t"), c.find("m:v", NS)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    cells[col_index(c.get("r"))] = val.strip()
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            out[s.get("name")] = rows
        return out


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 1
    here = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.join(os.path.dirname(here), "docs", "backlog")
    os.makedirs(outdir, exist_ok=True)
    sheets = read_sheets(argv[1])
    for name, fname in SHEETS.items():
        rows = sheets.get(name)
        if not rows:
            print("The workbook has no sheet called '%s'" % name)
            return 2
        header = rows[0]
        keep = [i for i, h in enumerate(header) if h and h not in DROP_COLUMNS]
        with open(os.path.join(outdir, fname), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, lineterminator="\r\n")
            for r in rows:
                if any(x for x in r):
                    w.writerow([(r[i] if i < len(r) else "") for i in keep])
        print("wrote docs/backlog/%s (%d rows)" % (fname, sum(1 for r in rows if any(r)) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
