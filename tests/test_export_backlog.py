"""tools/export_backlog.py: the change-log workbook becomes CSV files (standard library only)."""
import csv
import importlib.util
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_tool():
    spec = importlib.util.spec_from_file_location("export_backlog", os.path.join(ROOT, "tools", "export_backlog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_xlsx(path, sheets):
    """A minimal workbook: {sheet name: [[cell, ...], ...]}, text cells stored as shared strings."""
    shared, rows_xml = [], {}
    for name, rows in sheets.items():
        xml = ""
        for r, row in enumerate(rows, 1):
            cells = ""
            for c, v in enumerate(row):
                if v is None:
                    continue
                shared.append(str(v))
                cells += '<c r="%s%d" t="s"><v>%d</v></c>' % (chr(65 + c), r, len(shared) - 1)
            xml += '<row r="%d">%s</row>' % (r, cells)
        rows_xml[name] = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % xml
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook %s><sheets>%s</sheets></workbook>' % (
            ns, "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (n, i, i) for i, n in enumerate(sheets, 1))))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(
            '<Relationship Id="rId%d" Type="x" Target="worksheets/sheet%d.xml"/>' % (i, i) for i in range(1, len(sheets) + 1)))
        z.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst %s>%s</sst>' % (ns, "".join("<si><t>%s</t></si>" % s.replace("&", "&amp;") for s in shared)))
        for i, n in enumerate(sheets, 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, rows_xml[n])


def test_the_change_log_and_decisions_are_exported_without_the_prompt_column(tmp_path, monkeypatch):
    tool = load_tool()
    xlsx = str(tmp_path / "log.xlsx")
    make_xlsx(xlsx, {"Summary": [["x"]],
                     "Change Log": [["ID", "Change Request", "Claude Code Prompt", "Status"], ["KW-01", "Add a notes section, with a comma & more", "Read CLAUDE.md (Flask)", "Not started"], [None, None, None, None], ["KW-02", "Delete a photo", "p", "Released"]],
                     "Open Decisions": [["ID", "Decision Needed", "Decision"], ["KW-05", "Odour optional?", None]]})
    out = tmp_path / "docs" / "backlog"
    monkeypatch.setattr(tool.os.path, "dirname", lambda p, _d=os.path.dirname: str(tmp_path / "tools") if p.endswith("export_backlog.py") else _d(p))
    assert tool.main(["export_backlog.py", xlsx]) == 0
    items = list(csv.DictReader(open(out / "items.csv", encoding="utf-8-sig")))
    assert [r["ID"] for r in items] == ["KW-01", "KW-02"] and "Claude Code Prompt" not in items[0]
    assert items[0]["Change Request"] == "Add a notes section, with a comma & more" and items[1]["Status"] == "Released"
    dec = list(csv.DictReader(open(out / "open-decisions.csv", encoding="utf-8-sig")))
    assert dec == [{"ID": "KW-05", "Decision Needed": "Odour optional?", "Decision": ""}]


def test_the_real_exported_files_have_the_expected_shape():
    path = os.path.join(ROOT, "docs", "backlog", "items.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert rows and all(r["ID"].startswith("KW-") for r in rows)
    assert {"ID", "Change Request", "Priority", "Claude Code Mode", "Acceptance Criteria", "Status"} <= set(rows[0])
    assert len({r["ID"] for r in rows}) == len(rows)
