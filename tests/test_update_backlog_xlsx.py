"""tools/update_backlog_xlsx.py: Status / Notes / Decision cells of the change-log workbook are updated inside the .xlsx, nothing else changes (standard library only)."""
import importlib.util
import json
import os
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
LOG = ('<?xml version="1.0"?><worksheet %s><sheetData>'
       '<row r="1"><c r="A1" s="1" t="s"><v>0</v></c><c r="B1" s="1" t="s"><v>1</v></c><c r="C1" s="1" t="s"><v>2</v></c><c r="D1" s="1" t="s"><v>3</v></c></row>'
       '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" s="13" t="s"><v>5</v></c><c r="C2" s="14"/><c r="D2" s="14"/></row>'
       '<row r="3"><c r="A3" t="s"><v>6</v></c><c r="B3" s="13" t="s"><v>5</v></c><c r="C3" s="14"/><c r="D3" s="14" t="inlineStr"><is><t>old note</t></is></c></row>'
       '</sheetData><dataValidations count="1"><dataValidation type="list" sqref="B2:B3"><formula1>"Not started,Released"</formula1></dataValidation></dataValidations></worksheet>') % NS
DEC = ('<?xml version="1.0"?><worksheet %s><sheetData>'
       '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>7</v></c><c r="C1" t="s"><v>8</v></c></row>'
       '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" s="14"/><c r="C2" s="14"/></row></sheetData></worksheet>') % NS
SHARED = ["ID", "Status", "Owner", "Notes", "KW-05", "Not started", "KW-06", "Decided By", "Decision"]


def make(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook %s><sheets><sheet name="Change Log" sheetId="1" r:id="rId1"/><sheet name="Open Decisions" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029"/></workbook>' % NS)
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst %s>%s</sst>' % (NS, "".join("<si><t>%s</t></si>" % s for s in SHARED)))
        z.writestr("xl/worksheets/sheet1.xml", LOG)
        z.writestr("xl/worksheets/sheet2.xml", DEC)
        z.writestr("customXml/item1.xml", "<keep/>")


def sheets(path):
    return load("export_backlog").read_sheets(path)


def run(tool, xlsx, tmp_path, updates, extra=()):
    spec = tmp_path / "u.json"
    spec.write_text(json.dumps(updates), encoding="utf-8")
    return tool.main(["update_backlog_xlsx.py", str(xlsx), str(spec), *extra])


def test_status_owner_and_notes_are_set_and_everything_else_is_left_alone(tmp_path):
    tool = load("update_backlog_xlsx")
    xlsx = tmp_path / "log.xlsx"
    make(xlsx)
    with zipfile.ZipFile(xlsx) as before:
        keep = {n: before.read(n) for n in before.namelist() if n not in ("xl/worksheets/sheet1.xml", "xl/workbook.xml")}
    assert run(tool, xlsx, tmp_path, [{"id": "KW-05", "status": "Released", "owner": "NW", "notes": "PR #48 merged & live - Won't break"},
                                      {"id": "KW-06", "notes": "second"}]) == 0
    rows = sheets(str(xlsx))["Change Log"]
    assert rows[1] == ["KW-05", "Released", "NW", "PR #48 merged & live - Won't break"]
    assert rows[2] == ["KW-06", "Not started", "", "second"]                      # an omitted field keeps its value; the old note is replaced
    with zipfile.ZipFile(xlsx) as after:
        assert {n: after.read(n) for n in after.namelist() if n not in ("xl/worksheets/sheet1.xml", "xl/workbook.xml")} == keep
        sheet = after.read("xl/worksheets/sheet1.xml").decode()
        workbook = after.read("xl/workbook.xml").decode()
    assert '<dataValidation type="list" sqref="B2:B3">' in sheet and 's="13"' in sheet and 's="14"' in sheet            # drop-down and cell styles survive
    assert 'fullCalcOnLoad="1"' in workbook                                                                         # Summary counts refresh when Excel opens it


def test_a_decision_is_recorded_on_the_open_decisions_sheet(tmp_path):
    tool = load("update_backlog_xlsx")
    xlsx = tmp_path / "log.xlsx"
    make(xlsx)
    assert run(tool, xlsx, tmp_path, [{"decision": "KW-05", "by": "N. Wrana (2026-10-19)", "text": "Make optional"}]) == 0
    assert sheets(str(xlsx))["Open Decisions"][1] == ["KW-05", "N. Wrana (2026-10-19)", "Make optional"]


def test_a_bad_status_or_unknown_id_changes_nothing(tmp_path, capsys):
    tool = load("update_backlog_xlsx")
    xlsx = tmp_path / "log.xlsx"
    make(xlsx)
    original = xlsx.read_bytes()
    assert run(tool, xlsx, tmp_path, [{"id": "KW-05", "status": "Done"}]) == 2
    assert run(tool, xlsx, tmp_path, [{"id": "KW-05", "notes": "ok"}, {"id": "KW-99", "status": "Released"}]) == 2          # one bad entry stops the whole run
    assert xlsx.read_bytes() == original
    assert "KW-99" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(tmp_path):
    tool = load("update_backlog_xlsx")
    xlsx = tmp_path / "log.xlsx"
    make(xlsx)
    original = xlsx.read_bytes()
    assert run(tool, xlsx, tmp_path, [{"id": "KW-05", "status": "Released"}], extra=("--dry-run",)) == 0
    assert xlsx.read_bytes() == original
