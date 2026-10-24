"""KW-79: every change request is classified, the Change Log layout is tidied and every task has an owner. `changelog_write.py --classify-setup` inserts Touches after Type (Type becomes a fixed
list with a drop-down), removes the Claude Code Prompt column and moves every cell, colour rule, drop-down and Summary formula with them; `--default-owner` fills the Owner column;
`--playbook-sheet` rewrites the Claude Code Playbook sheet from a file. A new item must carry a Type and Touches. Standard library only; every test runs in a temporary folder."""
import csv
import json
import os
import re
import zipfile
from xml.etree import ElementTree

import pytest

from test_changelog_storage import ROOT, NAME, cw, env, make, new_item, note, row, run  # noqa: F401  (the fixtures and helpers of the storage tests)

TYPE = "Security & hardening"
SHEET = "xl/worksheets/sheet2.xml"
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
ITEMS = [
    {"ID": "KW-01", "Type": "Feature", "Change Request": "Add a notes box", "Claude Code Prompt": "Read CLAUDE.md, Flask app", "Owner": "N.Wrana", "Priority": "P1", "Claude Code Mode": "Direct",
     "Status": "Not started", "Approved to Start": "Y", "Row Updated": "2026-10-20", "Updated By": "Claude Code"},
    {"ID": "KW-02", "Type": "Bug", "Change Request": "Rename the tab", "Owner": "", "Priority": "P2", "Claude Code Mode": "Plan mode", "Status": "Not started", "Approved to Start": "",
     "Row Updated": "2026-10-20", "Updated By": "N. Wrana"},
    {"ID": "KW-03", "Type": "Change", "Change Request": "Fix the label", "Owner": "N. Wrana", "Priority": "P2", "Claude Code Mode": "Direct", "Status": "Released", "Approved to Start": "Y",
     "PR #": "70", "Released Date": "2026-10-20", "Row Updated": "2026-10-20", "Updated By": "Claude Code"},
    {"ID": "KW-04", "Type": "Action", "Change Request": "Drop the report", "Owner": "Someone Else", "Priority": "P3", "Claude Code Mode": "Direct", "Status": "Won't do", "Approved to Start": "",
     "Row Updated": "2026-10-20", "Updated By": "N. Wrana"},
    {"ID": "KW-05", "Type": "UI", "Change Request": "Speed up the page", "Priority": "P1", "Claude Code Mode": "Plan mode", "Status": "In progress", "Approved to Start": "Y",
     "Row Updated": "2026-10-20", "Updated By": "Claude Code"},
]
SUMMARY = ('<?xml version="1.0"?><worksheet %s><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Summary</t></is></c></row>'
           '<row r="5"><c r="B5"><f>COUNTIFS(\'Change Log\'!$I:$I,$A5,\'Change Log\'!$N:$N,B$4)</f><v>0</v></c><c r="C5"><f>COUNTIFS(\'Change Log\'!$I:$I,"P1",\'Change Log\'!$U:$U,"")</f><v>0</v></c></row>'
           '</sheetData></worksheet>') % NS


def playbook_xml(steps=3, assumptions=2):
    rows = '<row r="1">%s</row>' % "".join('<c r="%s1" s="3" t="inlineStr"><is><t>%s</t></is></c>' % (c, t) for c, t in zip("ABCD", ("Step", "What", "Command / Action", "Why")))
    r = 2
    for i in range(steps):
        rows += '<row r="%d">%s</row>' % (r, "".join('<c r="%s%d" s="4" t="inlineStr"><is><t>old %s%d</t></is></c>' % (c, r, c, r) for c in "ABCD"))
        r += 1
    r += 1
    rows += '<row r="%d"><c r="A%d" s="5" t="inlineStr"><is><t>Assumptions</t></is></c></row>' % (r, r)
    r += 1
    for i in range(assumptions):
        rows += '<row r="%d" ht="15" customHeight="1"><c r="A%d" t="inlineStr"><is><t>-</t></is></c><c r="B%d" s="6" t="inlineStr"><is><t>old assumption</t></is></c></row>' % (r, r, r)
        r += 1
    r += 1
    rows += '<row r="%d"><c r="A%d" s="5" t="inlineStr"><is><t>Note</t></is></c><c r="B%d" s="6" t="inlineStr"><is><t>old note</t></is></c></row>' % (r, r, r)
    return '<?xml version="1.0"?><worksheet %s><cols><col min="1" max="1" width="6"/><col min="2" max="2" width="30"/><col min="3" max="3" width="40"/><col min="4" max="4" width="60"/></cols><sheetData>%s</sheetData></worksheet>' % (NS, rows)


def rewrite(path, **changes):
    """Replace parts of the workbook zip: changes = {part: text}; a part that does not exist yet is added."""
    with zipfile.ZipFile(path) as z:
        parts = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    names = [n for n, _b in parts]
    for part, text in changes.items():
        data = text.encode("utf-8") if isinstance(text, str) else text
        if part in names:
            parts[names.index(part)] = (part, data)
        else:
            parts.append((part, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in parts:
            z.writestr(n, b)


def old_layout(env, summary=SUMMARY, steps=3, assumptions=2):
    """The workbook as the real one is today: Claude Code Prompt column, Summary formulas on Priority / Status / Verified Date, and a Playbook sheet."""
    make(env.master, ITEMS)
    with zipfile.ZipFile(env.master) as z:
        wb = z.read("xl/workbook.xml").decode()
        rels = z.read("xl/_rels/workbook.xml.rels").decode()
    wb = wb.replace("</sheets>", '<sheet name="Claude Code Playbook" sheetId="9" r:id="rId9"/></sheets>')
    rels = rels.replace("</Relationships>", '<Relationship Id="rId9" Type="x" Target="worksheets/sheet9.xml"/></Relationships>')
    rewrite(env.master, **{"xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": rels, "xl/worksheets/sheet1.xml": summary, "xl/worksheets/sheet9.xml": playbook_xml(steps, assumptions)})
    return env


@pytest.fixture
def old(env):
    return old_layout(env)


def parts(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def setup(cw):
    assert run(cw, "--classify-setup") == 0
    return parts(os.environ["KELPWORKS_CHANGELOG"])


# ---- the layout change ----------------------------------------------------------------------------------------------------------------------------------------------------
def test_every_cell_moves_with_its_column_and_the_prompt_column_goes(cw, old):
    was = cw.Snapshot(old.master)
    after = setup(cw)
    now = cw.Snapshot(old.master)
    assert list(sorted(now.headers, key=lambda h: cw.colnum(now.headers[h]))) == ["ID", "Module", "Section", "Type", "Touches", "Change Request", "Context / Rationale", "Transcript Time", "Raised By",
        "Priority", "Lens", "Claude Code Mode", "Acceptance Criteria", "Status", "Owner", "Notes", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date",
        "QM-001 Ref", "Row Updated", "Updated By"]
    assert now.headers["Status"] == was.headers["Status"] == "N" and now.headers["Updated By"] == "X" and now.headers["Priority"] == "J" and now.headers["Touches"] == "E"
    for kw, r in was.id_rows():
        old_values, new_values = was.values(r), now.values(now.row_of(kw))
        assert {h: v for h, v in old_values.items() if h != "Claude Code Prompt"} == {h: v for h, v in new_values.items() if h != "Touches"}, kw
        assert new_values["Touches"] == "" and "Claude Code Prompt" not in new_values
    assert "Claude Code Prompt" not in now.xml


def test_only_the_change_log_the_summary_and_the_workbook_part_are_rewritten(cw, old):
    before = parts(old.master)
    after = setup(cw)
    assert [n for n in before if before[n] != after[n]] == ["xl/workbook.xml", "xl/worksheets/sheet1.xml", SHEET]                 # nothing else changes, byte for byte
    ElementTree.fromstring(after[SHEET])
    ElementTree.fromstring(after["xl/worksheets/sheet1.xml"])


def test_formulas_colour_rules_drop_downs_and_widths_follow_the_moved_columns(cw, old):
    after = setup(cw)
    summary, sheet = after["xl/worksheets/sheet1.xml"].decode(), after[SHEET].decode()
    assert summary.count("'Change Log'!$J:$J") == 2 and "'Change Log'!$I:$I" not in summary                                    # Priority moved from I to J
    assert summary.count("'Change Log'!$N:$N") == 1 and summary.count("'Change Log'!$U:$U") == 1                                 # Status and Verified Date did not move
    assert 'COUNTIFS(\'Change Log\'!$J:$J,"P1",' in summary and ",$A5," in summary and "B$4" in summary                          # quoted text and the Summary's own cells are untouched
    assert re.findall(r'<conditionalFormatting sqref="([^"]*)"', sheet) == ["J2:J6", "A2:M6"]
    assert 'sqref="J2:J6"><formula1>"P1"' in sheet and 'sqref="N2:N6"' in sheet and "I2:I6" not in sheet
    m = re.search(r'sqref="D2:D6"><formula1>"([^"]*)"', sheet)
    values = m.group(1).replace("&amp;", "&").split(",")
    assert values == cw.TYPES and len(m.group(1).replace("&amp;", "&")) <= 255 and not any("," in t for t in cw.TYPES)
    assert '<dataValidations count="3">' in sheet
    assert '<col min="5" max="5" width="30"' in sheet and '<col min="6" max="6" width="40"' in sheet and '<col min="1" max="1" width="9"' in sheet     # the old E width (40) is now F's
    assert '<dimension ref="A1:X6"/>' in sheet and '<autoFilter ref="A1:X6"/>' in sheet and set(re.findall(r'spans="([^"]+)"', sheet)) <= {"1:24"}


def test_new_cells_take_the_style_of_the_type_column_in_the_same_row(cw, old):
    sheet = setup(cw)[SHEET].decode()
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', sheet, flags=re.S):
        r, body = int(m.group(1)), m.group(2)
        type_style = re.search(r'<c r="D%d" s="(\d+)"' % r, body).group(1)
        if r == 1:
            assert '<c r="E1" s="%s" t="inlineStr"><is><t xml:space="preserve">Touches</t></is></c>' % type_style in body
        else:
            assert '<c r="E%d" s="%s"/>' % (r, type_style) in body
        assert re.findall(r'<c r="([A-Z]+)%d"' % r, body) == [chr(65 + i) for i in range(24)]                                  # A to X, in order, none missing


def test_a_second_setup_is_refused_and_nothing_changes(cw, old, capsys):
    setup(cw)
    once = open(old.master, "rb").read()
    assert run(cw, "--classify-setup") == 2
    assert open(old.master, "rb").read() == once and "already has a 'Touches' column" in capsys.readouterr().out


def test_dry_run_excel_lock_and_backup(cw, old):
    original = open(old.master, "rb").read()
    assert run(cw, "--classify-setup", "--dry-run") == 0 and open(old.master, "rb").read() == original
    lock = os.path.join(os.path.dirname(old.master), "~$G_KelpWorks_ChangeControl.xlsx")
    open(lock, "w").close()
    assert run(cw, "--classify-setup") == 3 and open(old.master, "rb").read() == original
    os.remove(lock)
    assert run(cw, "--classify-setup") == 0 and [f for f in os.listdir(old.local) if f.endswith("_classify-setup.xlsx")]


def test_the_change_is_refused_when_the_layout_is_not_what_it_expects(cw, old, capsys):
    original = open(old.master, "rb").read()
    with zipfile.ZipFile(old.master) as z:
        sheet = z.read(SHEET).decode()
    rewrite(old.master, **{SHEET: sheet.replace(">Claude Code Prompt<", ">Prompt<")})
    mark = open(old.master, "rb").read()
    assert run(cw, "--classify-setup") == 2 and open(old.master, "rb").read() == mark and "no 'Claude Code Prompt' column" in capsys.readouterr().out
    rewrite(old.master, **{SHEET: sheet.replace(">Claude Code Prompt<", ">Claude Code Prompt<"), "xl/worksheets/sheet1.xml": SUMMARY.replace("$N:$N,B$4", "$L:$L,B$4")})
    mark = open(old.master, "rb").read()
    assert run(cw, "--classify-setup") == 2 and open(old.master, "rb").read() == mark and "refers to column L, which is being removed" in capsys.readouterr().out


# ---- values ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def ready(cw, old):
    setup(cw)
    return old


def test_before_the_setup_a_type_is_free_text_and_after_it_one_value_from_the_list(cw, old, ready, capsys):
    assert run(cw, "--id", "KW-01", "Type=  security & HARDENING ") == 0
    assert row(cw, ready.master, "KW-01")["Type"] == TYPE
    for bad in ("Feature", "Security & hardening; Samples & lab", ""):
        assert run(cw, "--id", "KW-01", "Type=%s" % bad) == 2
    assert "Type must be exactly one of: Security & hardening; Data integrity & traceability" in capsys.readouterr().out
    assert row(cw, ready.master, "KW-01")["Type"] == TYPE


def test_an_older_layout_keeps_free_text_types(cw, env):
    old_layout(env)
    assert run(cw, "--id", "KW-01", "Type=Feature") == 0 and row(cw, env.master, "KW-01")["Type"] == "Feature"
    assert run(cw, "--id", "KW-01", "Touches=Samples") == 2                                                   # no such column yet


def test_touches_takes_several_areas_in_list_order_without_repeats(cw, ready, capsys):
    assert run(cw, "--id", "KW-01", "Touches=product release; Production log ;production log") == 0
    assert row(cw, ready.master, "KW-01")["Touches"] == "Production log; Product release"
    for bad in ("Everything", "Production log; Nothing", " ; "):
        assert run(cw, "--id", "KW-01", "Touches=%s" % bad) == 2
    out = capsys.readouterr().out
    assert "Touches takes areas from this list" in out and "Touches needs at least one area" in out
    assert row(cw, ready.master, "KW-01")["Touches"] == "Production log; Product release"


def test_every_type_and_area_in_the_code_is_accepted(cw, ready):
    assert run(cw, "--id", "KW-01", "Touches=" + "; ".join(cw.TOUCHES)) == 0 and row(cw, ready.master, "KW-01")["Touches"] == "; ".join(cw.TOUCHES)
    for t in cw.TYPES:
        assert run(cw, "--id", "KW-01", "Type=" + t) == 0 and row(cw, ready.master, "KW-01")["Type"] == t


def test_released_wont_do_and_in_progress_items_can_be_classified(cw, ready):
    for kw in ("KW-03", "KW-04", "KW-05"):
        assert run(cw, "--id", kw, "Type=" + TYPE, "Touches=Admin / users") == 0 and row(cw, ready.master, kw)["Type"] == TYPE
    assert row(cw, ready.master, "KW-04")["Status"] == "Won't do" and row(cw, ready.master, "KW-03")["Status"] == "Released"


def test_a_classification_only_change_is_not_stamped_but_a_mixed_change_is(cw, ready):
    assert run(cw, "--id", "KW-01", "Type=" + TYPE, "Touches=Admin / users") == 0
    now = row(cw, ready.master, "KW-01")
    assert (now["Row Updated"], now["Updated By"]) == ("2026-10-20", "Claude Code")
    assert run(cw, "--id", "KW-02", "Type=Samples & lab", "Notes=changed too") == 0
    assert row(cw, ready.master, "KW-02")["Updated By"] == "Claude Code" and row(cw, ready.master, "KW-02")["Row Updated"] != "2026-10-20"
    mark = open(ready.master, "rb").read()
    assert run(cw, "--id", "KW-01", "Type=" + TYPE) == 0 and open(ready.master, "rb").read() == mark


def test_a_batch_classifies_many_items_with_one_backup(cw, ready, tmp_path):
    batch = tmp_path / "b.json"
    batch.write_text(json.dumps([{"id": "KW-0%d" % i, "set": {"Type": TYPE, "Touches": "Whole app"}} for i in range(1, 6)]), encoding="utf-8")
    before = len(os.listdir(ready.local))
    assert run(cw, "--batch", str(batch)) == 0 and len(os.listdir(ready.local)) == before + 1
    assert all(row(cw, ready.master, "KW-0%d" % i)["Touches"] == "Whole app" for i in range(1, 6))


def test_classification_is_not_a_way_around_the_other_rules(cw, ready):
    assert run(cw, "--id", "KW-02", "Type=" + TYPE, "Status=In progress") == 2                                 # KW-02 is not approved: nothing is written
    assert row(cw, ready.master, "KW-02")["Type"] == "Bug" and row(cw, ready.master, "KW-02")["Status"] == "Not started"


# ---- the inbox: a new item is classified and owned ----------------------------------------------------------------------------------------------------------------------------
def reason(env, name):
    return json.loads((env.inbox / "rejected" / (name + ".result.json")).read_text(encoding="utf-8"))["reason"]


def test_a_new_item_with_a_type_and_touches_is_applied_owned_by_the_default_owner_and_styled(cw, ready):
    note(ready, "n-0001", note_id="n-0001", **new_item(Type=TYPE.upper(), Touches="admin / users; Production log"))
    assert run(cw, "--apply-inbox") == 0
    r = row(cw, ready.master, "KW-06")
    assert r["Type"] == TYPE and r["Touches"] == "Production log; Admin / users" and r["Owner"] == "N. Wrana" and r["Status"] == "Not started"
    sheet = parts(ready.master)[SHEET].decode()
    assert re.search(r'<c r="E7" s="\d+" t="inlineStr"><is><t xml:space="preserve">Production log; Admin / users</t>', sheet) and re.search(r'<c r="O7" s="\d+" t="inlineStr">', sheet)


@pytest.mark.parametrize("fields,missing", [({"Type": ""}, "Type, Touches"), ({"Type": TYPE}, "Touches"), ({"Touches": "Samples", "Type": ""}, "Type")])
def test_a_new_item_without_a_classification_is_rejected_and_the_message_lists_the_values(cw, ready, fields, missing):
    note(ready, "n-0002", note_id="n-0002", **new_item(**fields))
    assert run(cw, "--apply-inbox") == 8
    why = reason(ready, "n-0002")
    assert "a new item needs %s" % missing in why and "Type is exactly one of: Security & hardening" in why and "Touches is one or more of: Production log" in why
    assert len(cw.Snapshot(ready.master).id_rows()) == 5


def test_an_invalid_value_in_a_new_item_is_rejected(cw, ready):
    note(ready, "n-0003", note_id="n-0003", **new_item(Type="Change", Touches="Samples"))
    note(ready, "n-0004", note_id="n-0004", **new_item(Type=TYPE, Touches="Everything"))
    assert run(cw, "--apply-inbox") == 8
    assert "Type must be exactly one of" in reason(ready, "n-0003") and "Touches takes areas from this list" in reason(ready, "n-0004")


def test_an_old_layout_still_takes_an_old_format_note_and_refuses_the_new_field(cw, old):
    note(old, "o-0001", note_id="o-0001", **new_item())
    note(old, "o-0002", note_id="o-0002", **new_item(**{"Change Request": "Another request", "Type": TYPE, "Touches": "Samples"}))
    assert run(cw, "--apply-inbox") == 8
    assert cw.Snapshot(old.master).row_of("KW-06") and "no 'Touches' column" in reason(old, "o-0002")


def test_a_revision_may_change_the_classification_of_an_item_not_yet_started(cw, ready):
    snap = row(cw, ready.master, "KW-01")
    note(ready, "r-0001", note_id="r-0001", kind="revision", id="KW-01", based_on={"Row Updated": snap["Row Updated"]}, revision_summary="classified",
         fields={"Type": "user experience", "Touches": "Labels / printing"}, **{"from": "Claude (chat)", "created": "2026-10-24T10:00:00Z"})
    assert run(cw, "--apply-inbox") == 0
    assert row(cw, ready.master, "KW-01")["Type"] == "User experience" and row(cw, ready.master, "KW-01")["Touches"] == "Labels / printing"


def test_a_row_added_later_keeps_the_drop_down_and_every_range_covering_every_row(cw, ready):
    note(ready, "n-0005", note_id="n-0005", **new_item(Type=TYPE, Touches="Samples"))
    assert run(cw, "--apply-inbox") == 0
    sheet = parts(ready.master)[SHEET].decode()
    assert 'sqref="D2:D7"' in sheet and 'sqref="J2:J7"' in sheet and '<autoFilter ref="A1:X7"' in sheet and '<dimension ref="A1:X7"/>' in sheet


# ---- the default owner -------------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_default_owner_fills_blanks_and_the_spelling_but_never_another_name_and_is_not_stamped(cw, old):
    assert run(cw, "--default-owner") == 0
    owners = {kw: row(cw, old.master, kw)["Owner"] for kw in ("KW-01", "KW-02", "KW-03", "KW-04", "KW-05")}
    assert owners == {"KW-01": "N. Wrana", "KW-02": "N. Wrana", "KW-03": "N. Wrana", "KW-04": "Someone Else", "KW-05": "N. Wrana"}
    assert row(cw, old.master, "KW-02")["Updated By"] == "N. Wrana" and row(cw, old.master, "KW-02")["Row Updated"] == "2026-10-20"
    mark = open(old.master, "rb").read()
    assert run(cw, "--default-owner") == 0 and open(old.master, "rb").read() == mark                              # a second run changes nothing


def test_the_owner_cannot_be_set_through_item_updates_or_notes(cw, old, capsys):
    assert run(cw, "--id", "KW-01", "Owner=Someone") == 2 and "belongs to a human" in capsys.readouterr().out
    note(old, "o-0003", note_id="o-0003", **new_item(Owner="Someone"))
    assert run(cw, "--apply-inbox") == 8 and "Owner" in reason(old, "o-0003")


# ---- the playbook sheet --------------------------------------------------------------------------------------------------------------------------------------------------------
def playbook_file(tmp_path, steps=3, assumptions=2, **over):
    spec = {"steps": [[str(i), "What %d" % i, "Command %d\nsecond line" % i, "Why " + "word " * 60] for i in range(1, steps + 1)], "assumptions": ["Assumption %d" % i for i in range(1, assumptions + 1)], "note": "The new note"}
    spec.update(over)
    path = tmp_path / "playbook.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def test_the_playbook_sheet_is_written_cell_by_cell_and_nothing_else_changes(cw, old, tmp_path):
    before = parts(old.master)
    assert run(cw, "--playbook-sheet", "--playbook-file", playbook_file(tmp_path)) == 0
    after = parts(old.master)
    assert [n for n in before if before[n] != after[n]] == ["xl/workbook.xml", "xl/worksheets/sheet9.xml"]
    snap = cw.Snapshot(old.master)
    assert snap.cell_ref("Claude Code Playbook", "B3") == "What 2" and snap.cell_ref("Claude Code Playbook", "C2") == "Command 1\nsecond line" and snap.cell_ref("Claude Code Playbook", "A1") == "Step"
    assert snap.cell_ref("Claude Code Playbook", "B7") == "Assumption 1" and snap.cell_ref("Claude Code Playbook", "B8") == "Assumption 2" and snap.cell_ref("Claude Code Playbook", "B10") == "The new note"
    assert snap.cell_ref("Claude Code Playbook", "A7") == "-"                                                     # the bullet column is left as it was
    sheet = after["xl/worksheets/sheet9.xml"].decode()
    assert float(re.search(r'<row r="2"[^>]*?\sht="([\d.]+)" customHeight="1"', sheet).group(1)) > 30      # the long text got a taller row
    assert run(cw, "--playbook-sheet", "--playbook-file", playbook_file(tmp_path)) == 0 and parts(old.master) == after                                           # a second run changes nothing


def test_the_playbook_file_must_match_the_rows_the_sheet_has(cw, old, tmp_path, capsys):
    original = open(old.master, "rb").read()
    assert run(cw, "--playbook-sheet", "--playbook-file", playbook_file(tmp_path, steps=4)) == 2
    assert run(cw, "--playbook-sheet", "--playbook-file", playbook_file(tmp_path, assumptions=3)) == 2
    assert run(cw, "--playbook-sheet", "--playbook-file", playbook_file(tmp_path, note=5)) == 2
    assert open(old.master, "rb").read() == original and "the numbers must match" in capsys.readouterr().out


# ---- the lists and files in the repository -------------------------------------------------------------------------------------------------------------------------------------
def block(text, name):
    m = re.search(r"<!-- %s -->(.*?)<!-- /%s -->" % (name, name), text, flags=re.S)
    assert m, name
    return [l[2:].strip() for l in m.group(1).splitlines() if l.startswith("- ")]


def test_the_intake_skill_text_lists_exactly_the_values_the_writer_accepts(cw):
    text = open(os.path.join(ROOT, "docs", "backlog", "intake-skill-update.md"), encoding="utf-8").read()
    assert block(text, "TYPES") == cw.TYPES and block(text, "TOUCHES") == cw.TOUCHES
    assert "Do not send `Owner`" in text


def test_the_inbox_readme_and_the_rules_sheet_name_every_value(cw):
    rules = json.dumps(json.load(open(os.path.join(ROOT, "docs", "backlog", "workflow-rules.json"), encoding="utf-8")))
    for v in cw.TYPES + cw.TOUCHES:
        assert v in cw.NOTE_FORMAT and v in rules, v
    assert "Type and Touches" in cw.NOTE_FORMAT and "Owner N. Wrana" in cw.NOTE_FORMAT


def test_the_classification_file_is_complete_valid_and_has_no_repeats(cw):
    items = list(csv.DictReader(open(os.path.join(ROOT, "docs", "backlog", "items.csv"), encoding="utf-8-sig")))
    data = json.load(open(os.path.join(ROOT, "docs", "backlog", "classification.json"), encoding="utf-8"))
    ids = [d["id"] for d in data]
    assert len(ids) == len(set(ids))
    for d in data:
        assert set(d["set"]) == {"Type", "Touches"}
        assert cw.check_value("Type", d["set"]["Type"], True) == d["set"]["Type"] and cw.check_value("Touches", d["set"]["Touches"]) == d["set"]["Touches"]
    for it in items:                                                                                         # a snapshot taken before the setup has the old free-text types: the file must cover it
        assert it["Type"] in cw.TYPES or it["ID"] in ids, "%s has no classification" % it["ID"]
    assert set(ids) <= {it["ID"] for it in items}


def test_the_playbook_file_has_the_rows_the_sheet_needs():
    spec = json.load(open(os.path.join(ROOT, "docs", "backlog", "claude-code-playbook.json"), encoding="utf-8"))
    assert len(spec["steps"]) == 10 and all(len(r) == 4 for r in spec["steps"]) and len(spec["assumptions"]) == 3 and spec["note"]
    assert "Flask" in spec["assumptions"][0] and "No Flask" in spec["assumptions"][0]                         # the corrected stack statement, not the old one


def test_the_archive_keeps_the_old_types_owners_and_prompts():
    path = os.path.join(ROOT, "docs", "backlog", "archive", "change-log-columns-before-KW-79.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert rows and list(rows[0]) == ["ID", "Type (before KW-79)", "Owner (before KW-79)", "Claude Code Prompt (removed by KW-79)"] and rows[0]["ID"] == "KW-01"
