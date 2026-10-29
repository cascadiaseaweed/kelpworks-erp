"""KW-71: every change-log item records its effect on the Quality Manual (QM-001) before it can be Released. `changelog_write.py --qm-setup` adds the 'QM-001 Impact' column and the 'QM-001 Changes'
register sheet; the writer refuses Released without a confirmed impact (and, for a change, a register entry); entries are drafted by Claude Code or from an inbox note and decided only by the Quality
Manager; a backfill proposes impacts for Released items; a report lists what is still open. Standard library only; every test runs in a temporary folder."""
import json
import os
import re
import zipfile
from xml.etree import ElementTree

import pytest

from test_changelog_storage import NAME, cw, env, make, new_item, note, read_only, row, run  # noqa: F401  (the fixtures and helpers of the storage tests)

SHEET = "xl/worksheets/sheet2.xml"
GOOD_ENTRY = {"kw_items": ["KW-01"], "section": "2.2.2 User roles", "change": "Replace", "before": "Only a Quality Manager may place a hold.", "after": "Only a Quality Manager may place or lift a hold.",
              "reason": "KW-01 changes who can lift a hold"}


def parts(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


@pytest.fixture
def qm(cw, env):
    assert run(cw, "--qm-setup") == 0
    return env


def register(cw, path):
    snap = cw.Snapshot(path)
    return [r for _n, r in cw.table_rows(snap, cw.QM_SHEET)[1]]


def entry(cw, env, tmp_name="entry.json", **over):
    """Write an entry file and apply it. -> exit code."""
    spec = dict(GOOD_ENTRY, **over)
    path = env.tmp / tmp_name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return run(cw, "--qm-entry", str(path))


def decide(cw, env, ref, **columns):
    """What the Quality Manager does in Excel: write the human columns of one register row."""
    snap = cw.Snapshot(env.master)
    xml = snap.files[snap.paths[cw.QM_SHEET]].decode("utf-8")
    headers, rows = cw.table_rows(snap, cw.QM_SHEET)
    n = [n for n, r in rows if r.get("Ref") == ref][0]
    for header, value in columns.items():
        xml = cw.set_cell(xml, headers[header], n, value)
    cw.save(snap, {snap.paths[cw.QM_SHEET]: xml})


def release(cw, kw="KW-01", *extra):
    return run(cw, "--id", kw, "Status=Released", "PR #=80", "Released Date=2026-10-29", *extra)


# ---- the one-time setup ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_setup_adds_the_column_and_the_sheet_and_changes_nothing_else(cw, env):
    before = parts(env.master)
    assert run(cw, "--qm-setup") == 0
    after = parts(env.master)
    assert [n for n in before if before[n] != after[n]] == ["xl/workbook.xml", "xl/_rels/workbook.xml.rels", SHEET]                   # nothing else changes, byte for byte
    assert [n for n in after if n not in before] == ["xl/worksheets/sheet6.xml"]
    for text in after.values():
        if text.startswith(b"<?xml"):
            ElementTree.fromstring(text)
    snap = cw.Snapshot(env.master)
    assert snap.headers["QM-001 Impact"] == "Y" and snap.headers["Updated By"] == "X"
    sheet = after[SHEET].decode()
    assert '<dimension ref="A1:Y6"/>' in sheet and '<autoFilter ref="A1:Y6"/>' in sheet and set(re.findall(r'spans="([^"]+)"', sheet)) <= {"1:25"}
    assert '<col min="25" max="25" width="46" customWidth="1"/>' in sheet
    assert "$A$1:$Y$6" in after["xl/workbook.xml"].decode()                                                                           # the filter's defined name follows
    for kw, r in cw.Snapshot(env.master).id_rows():
        assert snap.values(r)["QM-001 Impact"] == ""


def test_every_old_cell_is_byte_identical_after_the_setup(cw, env):
    was = dict(cw.xl.cells_of(cw.Snapshot(env.master).xml))
    assert run(cw, "--qm-setup") == 0
    now = dict(cw.xl.cells_of(cw.Snapshot(env.master).xml))
    assert {ref: el for ref, el in now.items() if not ref.startswith("Y")} == was
    assert sorted(ref for ref in now if ref.startswith("Y")) == ["Y1", "Y2", "Y3", "Y4", "Y5", "Y6"]


def test_new_cells_take_the_style_of_the_qm_ref_cell_in_the_same_row(cw, env):
    assert run(cw, "--qm-setup") == 0
    sheet = parts(env.master)[SHEET].decode()
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', sheet, flags=re.S):
        r, body = int(m.group(1)), m.group(2)
        style = re.search(r'<c r="V%d" s="(\d+)"' % r, body).group(1)
        assert re.search(r'<c r="Y%d" s="%s"' % (r, style), body)


def test_the_register_sheet_is_registered_like_an_excel_sheet_after_open_decisions(cw, env):
    assert run(cw, "--qm-setup") == 0
    after = parts(env.master)
    wb, rels = after["xl/workbook.xml"].decode(), after["xl/_rels/workbook.xml.rels"].decode()
    assert re.findall(r'<sheet name="([^"]+)"', wb) == ["Summary", "Change Log", "Open Decisions", "QM-001 Changes", "Workflow Rules", "KW-60 Clean Start Plan"]
    assert 'sheetId="6" r:id="rId6"' in wb and 'Id="rId6"' in rels and 'Target="worksheets/sheet6.xml"' in rels
    snap = cw.Snapshot(env.master)
    assert snap.paths["QM-001 Changes"] == "xl/worksheets/sheet6.xml"
    headers = cw.xl.header_columns(after["xl/worksheets/sheet6.xml"].decode(), snap.sst)
    assert list(sorted(headers, key=lambda h: cw.colnum(headers[h]))) == cw.QM_COLUMNS
    sheet = after["xl/worksheets/sheet6.xml"].decode()
    assert 'state="frozen"' in sheet and sheet.count("<dataValidation ") == 2 and "Approved,Rejected,Needs change" in sheet and "Replace,Add,Delete" in sheet


def test_a_sheet_index_reference_that_would_move_puts_the_new_sheet_last(cw, env):
    with zipfile.ZipFile(env.master) as z:
        wb = z.read("xl/workbook.xml").decode()
    wb = wb.replace('<definedNames>', '<definedNames><definedName name="X" localSheetId="3">\'Workflow Rules\'!$A$1</definedName>')
    from test_changelog_classification import rewrite
    rewrite(env.master, **{"xl/workbook.xml": wb})
    assert run(cw, "--qm-setup") == 0
    assert re.findall(r'<sheet name="([^"]+)"', parts(env.master)["xl/workbook.xml"].decode())[-1] == "QM-001 Changes"


def test_content_types_and_app_properties_are_kept_in_step_when_the_workbook_has_them(cw, env):
    from test_changelog_classification import rewrite
    rewrite(env.master, **{"[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="x"/></Types>',
                           "docProps/app.xml": ('<?xml version="1.0"?><Properties xmlns:vt="v"><HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>'
                                                '<vt:variant><vt:i4>5</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="5" baseType="lpstr"><vt:lpstr>Summary</vt:lpstr>'
                                                '<vt:lpstr>Change Log</vt:lpstr><vt:lpstr>Open Decisions</vt:lpstr><vt:lpstr>Workflow Rules</vt:lpstr><vt:lpstr>KW-60 Clean Start Plan</vt:lpstr></vt:vector></TitlesOfParts></Properties>')})
    assert run(cw, "--qm-setup") == 0
    after = parts(env.master)
    assert b'PartName="/xl/worksheets/sheet6.xml"' in after["[Content_Types].xml"]
    app = after["docProps/app.xml"].decode()
    assert "<vt:i4>6</vt:i4>" in app and 'size="6" baseType="lpstr"' in app and app.index("Open Decisions") < app.index("QM-001 Changes") < app.index("Workflow Rules")
    for text in after.values():
        if text.startswith(b"<?xml"):
            ElementTree.fromstring(text)


def test_a_second_setup_is_refused_and_so_is_a_workbook_that_has_only_one_of_the_two(cw, env, capsys):
    assert run(cw, "--qm-setup") == 0
    once = open(env.master, "rb").read()
    assert run(cw, "--qm-setup") == 2 and open(env.master, "rb").read() == once and "already has a 'QM-001 Impact' column" in capsys.readouterr().out


def test_dry_run_excel_lock_and_backup(cw, env):
    original = open(env.master, "rb").read()
    assert run(cw, "--qm-setup", "--dry-run") == 0 and open(env.master, "rb").read() == original
    lock = os.path.join(os.path.dirname(env.master), "~$G_KelpWorks_ChangeControl.xlsx")
    open(lock, "w").close()
    assert run(cw, "--qm-setup") == 3 and open(env.master, "rb").read() == original
    os.remove(lock)
    assert run(cw, "--qm-setup") == 0 and [f for f in os.listdir(env.local) if f.endswith("_qm-setup.xlsx")]


# ---- the impact value -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_an_impact_is_a_class_and_one_line_and_is_written_in_standard_form(cw, qm, capsys):
    assert run(cw, "--id", "KW-01", "QM-001 Impact=  requirement CHANGED -   who can lift a hold ") == 0
    assert row(cw, qm.master, "KW-01")["QM-001 Impact"] == "Requirement changed - who can lift a hold"
    for bad in ("None", "None -", "Nothing - x", "Requirement changed: x", "", "PROPOSED"):
        assert run(cw, "--id", "KW-01", "QM-001 Impact=%s" % bad) == 2, bad
    assert "'<class> - <one line>' where class is one of: None; Requirement changed; Requirement added; Requirement relaxed; Record / control changed" in capsys.readouterr().out
    assert run(cw, "--id", "KW-02", "QM-001 Impact=proposed record / control changed - the log is a controlled record") == 0
    assert row(cw, qm.master, "KW-02")["QM-001 Impact"] == "PROPOSED Record / control changed - the log is a controlled record"


def test_an_impact_alone_is_not_stamped_but_a_status_change_is(cw, qm):
    before = row(cw, qm.master, "KW-01")
    assert run(cw, "--id", "KW-01", "QM-001 Impact=None - no requirement involved") == 0
    after = row(cw, qm.master, "KW-01")
    assert (after["Row Updated"], after["Updated By"]) == (before["Row Updated"], before["Updated By"])
    assert run(cw, "--id", "KW-01", "Notes=building") == 0 and row(cw, qm.master, "KW-01")["Updated By"] == "Claude Code"


def test_show_prints_the_impact(cw, qm, capsys):
    assert run(cw, "--id", "KW-01", "QM-001 Impact=None - no requirement involved") == 0
    assert run(cw, "--show", "KW-01") == 0 and "QM-001 Impact" in capsys.readouterr().out


def test_the_impact_cannot_be_written_into_a_workbook_that_has_no_column(cw, env, capsys):
    assert run(cw, "--id", "KW-01", "QM-001 Impact=None - x") == 2 and "no 'QM-001 Impact' column" in capsys.readouterr().out


# ---- the release gate -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_released_is_refused_without_an_impact(cw, qm, capsys):
    original = open(qm.master, "rb").read()
    assert release(cw) == 2
    out = capsys.readouterr().out
    assert "cannot be Released without a QM-001 Impact" in out and "None - bug fix, no requirement involved" in out and open(qm.master, "rb").read() == original
    assert row(cw, qm.master, "KW-01")["Status"] == "Not started"


def test_released_is_refused_while_the_impact_is_only_proposed(cw, qm, capsys):
    assert run(cw, "--id", "KW-01", "QM-001 Impact=PROPOSED None - a guess") == 0
    assert release(cw) == 2 and "still PROPOSED" in capsys.readouterr().out
    assert release(cw, "KW-01", "QM-001 Impact=None - confirmed: a plain fix") == 0                                                    # confirming it in the same call is enough
    assert row(cw, qm.master, "KW-01")["Status"] == "Released"


def test_none_with_a_reason_always_releases_and_needs_no_ref(cw, qm):
    assert release(cw, "KW-01", "QM-001 Impact=None - bug fix, no requirement involved") == 0
    r = row(cw, qm.master, "KW-01")
    assert (r["Status"], r["QM-001 Ref"], r["QM-001 Impact"]) == ("Released", "", "None - bug fix, no requirement involved")


@pytest.mark.parametrize("cls", ["Requirement changed", "Requirement added", "Requirement relaxed", "Record / control changed"])
def test_a_change_needs_a_ref_that_is_on_the_register(cw, qm, cls, capsys):
    impact = "QM-001 Impact=%s - the manual must say so" % cls
    assert release(cw, "KW-01", impact) == 2 and "needs a register entry" in capsys.readouterr().out                                   # no ref at all
    assert release(cw, "KW-01", impact, "QM-001 Ref=C1") == 2 and "QM-001 Ref C1 is not on the 'QM-001 Changes' sheet" in capsys.readouterr().out
    assert entry(cw, qm) == 0
    assert release(cw, "KW-01", impact, "QM-001 Ref=C1") == 0                                                                          # a drafted entry is enough: the Quality Manager's decision is chased, not awaited
    assert row(cw, qm.master, "KW-01")["Status"] == "Released"


def test_a_change_with_several_refs_needs_every_one_of_them(cw, qm, capsys):
    assert entry(cw, qm) == 0
    assert release(cw, "KW-01", "QM-001 Impact=Requirement changed - x", "QM-001 Ref=C1 / C2") == 2 and "C2" in capsys.readouterr().out
    assert entry(cw, qm, section="2.2.3 Document control", tmp_name="e2.json") == 0
    assert release(cw, "KW-01", "QM-001 Impact=Requirement changed - x", "QM-001 Ref=C1 / C2") == 0


def test_the_gate_uses_the_impact_already_on_the_row_and_only_when_released_is_being_set(cw, qm):
    assert run(cw, "--id", "KW-01", "QM-001 Impact=None - fine") == 0
    assert release(cw) == 0                                                                                                            # nothing given now: the row's own impact counts
    assert run(cw, "--id", "KW-01", "Notes=a note on a released item") == 0                                                            # an edit that is not a status change is not judged
    assert run(cw, "--id", "KW-01", "Status=Released") == 0                                                                            # the same status again is not judged again
    assert run(cw, "--id", "KW-03", "Notes=old released item without an impact") == 0                                                  # KW-03 was Released before the column existed


def test_a_workbook_with_the_column_but_no_register_sheet_says_to_run_the_setup(cw, qm, capsys):
    from test_changelog_classification import rewrite
    with zipfile.ZipFile(qm.master) as z:
        wb = z.read("xl/workbook.xml").decode()
    rewrite(qm.master, **{"xl/workbook.xml": re.sub(r'<sheet name="QM-001 Changes"[^>]*/>', "", wb)})
    assert release(cw, "KW-01", "QM-001 Impact=Requirement changed - x", "QM-001 Ref=C1") == 2 and "no 'QM-001 Changes' sheet" in capsys.readouterr().out


# ---- register entries -----------------------------------------------------------------------------------------------------------------------------------------------------
def test_an_entry_gets_the_next_c_number_and_fills_only_the_drafting_columns(cw, qm):
    assert run(cw, "--id", "KW-03", "QM-001 Ref=C20") == 0                                                                             # the manual's own numbers continue
    assert entry(cw, qm) == 0
    [r] = register(cw, qm.master)
    assert r["Ref"] == "C21" and r["KW items"] == "KW-01" and r["Change"] == "Replace" and r["Manual section / clause"] == "2.2.2 User roles"
    assert r["Before"] == GOOD_ENTRY["before"] and r["After"] == GOOD_ENTRY["after"] and r["Reason"] == GOOD_ENTRY["reason"]
    assert r["Drafted by"] == "Claude Code" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["Date"])
    for h in cw.QM_HUMAN + ["Drafted In", "Insert after"]:
        assert r[h] == "", h                                                                                                           # the Quality Manager's columns stay blank
    assert entry(cw, qm, tmp_name="e2.json", section="2.2.3") == 0
    assert [r["Ref"] for r in register(cw, qm.master)] == ["C21", "C22"]


def test_the_next_number_also_follows_the_register_and_an_empty_workbook_starts_at_c1(cw, qm):
    assert entry(cw, qm) == 0
    assert entry(cw, qm, tmp_name="e2.json", section="x") == 0 and register(cw, qm.master)[1]["Ref"] == "C2"                          # nothing else carries a C number here: C1, then C2


def test_human_columns_are_shaded_like_the_open_decisions_answer_columns(cw, qm):
    assert entry(cw, qm) == 0
    sheet = parts(qm.master)["xl/worksheets/sheet6.xml"].decode()
    assert re.search(r'<c r="A2" s="7"', sheet) and re.search(r'<c r="K2" s="7"', sheet)                                                # (the fixture's Open Decisions body style is 7 throughout)
    assert '<dimension ref="A1:P2"/>' in sheet


@pytest.mark.parametrize("over,message", [
    ({"kw_items": ["KW-99"]}, "KW-99 is not an item"),
    ({"kw_items": []}, "kw_items must name at least one"),
    ({"section": ""}, "needs section"),
    ({"reason": ""}, "needs reason"),
    ({"change": "Rewrite"}, "change must be one of"),
    ({"after": ""}, "Replace needs"),
    ({"after": GOOD_ENTRY["before"]}, "Replace needs"),
    ({"change": "Delete", "after": "x"}, "Delete needs"),
    ({"change": "Add", "before": "", "after": "new text"}, "Add needs"),
    ({"insert_after": "x"}, "insert_after is only for an Add"),
    ({"QM Decision": "Approved"}, "takes only"),
    ({"decided_by": "N. Wrana"}, "takes only"),
])
def test_an_entry_is_validated_and_nothing_is_written_when_it_is_not(cw, qm, over, message, capsys):
    original = open(qm.master, "rb").read()
    assert entry(cw, qm, **over) == 2 and message in capsys.readouterr().out and open(qm.master, "rb").read() == original


def test_add_and_delete_entries(cw, qm):
    assert entry(cw, qm, change="Add", before="", after="A new paragraph.", insert_after="2.2.2 User roles") == 0
    assert entry(cw, qm, tmp_name="d.json", change="Delete", after="", section="2.3") == 0
    add, delete = register(cw, qm.master)
    assert (add["Change"], add["After"], add["Insert after"], add["Before"]) == ("Add", "A new paragraph.", "2.2.2 User roles", "")
    assert (delete["Change"], delete["Before"], delete["After"]) == ("Delete", GOOD_ENTRY["before"], "")


def test_the_same_change_twice_is_a_duplicate(cw, qm, capsys):
    assert entry(cw, qm) == 0 and entry(cw, qm, tmp_name="again.json") == 2
    assert "duplicate: C1 already holds this change" in capsys.readouterr().out and len(register(cw, qm.master)) == 1


def test_an_entry_can_name_several_items_in_any_form(cw, qm):
    assert entry(cw, qm, kw_items="KW-05; KW-01") == 0 and register(cw, qm.master)[0]["KW items"] == "KW-01; KW-05"


def test_an_undecided_entry_can_be_revised_and_a_decided_one_cannot(cw, qm, capsys):
    assert entry(cw, qm) == 0
    assert entry(cw, qm, tmp_name="rev.json", ref="C1", after="Only a Quality Manager may place or lift a hold, with a reason.") == 0
    [r] = register(cw, qm.master)
    assert r["After"].endswith("with a reason.") and r["Ref"] == "C1" and len(register(cw, qm.master)) == 1
    decide(cw, qm, "C1", **{"QM Decision": "Needs change", "Decided By": "N. Wrana"})
    assert entry(cw, qm, tmp_name="rev2.json", ref="C1", after="Something else entirely.") == 2
    assert "already has QM Decision" in capsys.readouterr().out and register(cw, qm.master)[0]["After"].endswith("with a reason.")
    assert entry(cw, qm, tmp_name="rev3.json", ref="C99") == 2 and "C99 is not on the" in capsys.readouterr().out


def test_the_tool_never_writes_the_quality_managers_columns(cw, qm, capsys):
    for column in cw.QM_HUMAN:
        assert run(cw, "--id", "KW-01", "%s=Approved" % column) == 2, column                                                           # not a Change Log column, not owned
    assert entry(cw, qm) == 0
    assert all(r[h] == "" for r in register(cw, qm.master) for h in cw.QM_HUMAN)


def test_several_entries_in_one_file_are_written_together(cw, qm):
    path = qm.tmp / "two.json"
    path.write_text(json.dumps([dict(GOOD_ENTRY), dict(GOOD_ENTRY, section="2.2.3")]), encoding="utf-8")
    assert run(cw, "--qm-entry", str(path)) == 0 and [r["Ref"] for r in register(cw, qm.master)] == ["C1", "C2"]
    bad = qm.tmp / "bad.json"
    bad.write_text(json.dumps([dict(GOOD_ENTRY, section="2.4"), dict(GOOD_ENTRY, kw_items=["KW-99"])]), encoding="utf-8")
    assert run(cw, "--qm-entry", str(bad)) == 2 and len(register(cw, qm.master)) == 2                                                   # one bad entry stops the whole file


def test_entries_need_the_setup_first(cw, env, capsys):
    path = env.tmp / "e.json"
    path.write_text(json.dumps(GOOD_ENTRY), encoding="utf-8")
    assert run(cw, "--qm-entry", str(path)) == 2 and "--qm-setup" in capsys.readouterr().out


# ---- entries from the inbox -----------------------------------------------------------------------------------------------------------------------------------------------
def qm_note(env, name, **fields):
    note(env, name, kind="qm_entry", note_id=name, **{"from": "Claude (chat)", "fields": dict(GOOD_ENTRY, **fields)})


def test_an_inbox_note_can_draft_an_entry_and_the_result_names_the_ref(cw, qm):
    qm_note(qm, "chat-qm-0001")
    assert run(cw, "--apply-inbox") == 0
    [r] = register(cw, qm.master)
    assert r["Ref"] == "C1" and r["Drafted by"] == "Claude (chat)"
    result = json.loads((qm.inbox / "processed" / "chat-qm-0001.result.json").read_text(encoding="utf-8"))
    assert result["result"] == "applied" and result["id"] == "C1" and "register entry C1 added" in result["reason"]


def test_two_notes_in_one_run_get_different_refs_and_a_note_can_revise_an_entry_an_earlier_note_made(cw, qm):
    qm_note(qm, "chat-qm-0003", section="2.2.2")
    qm_note(qm, "chat-qm-0004", section="2.2.3")
    assert run(cw, "--apply-inbox") == 0
    assert [r["Ref"] for r in register(cw, qm.master)] == ["C1", "C2"]
    qm_note(qm, "chat-qm-0008", ref="C2", after="Only a Quality Manager may place or lift a hold, and must give a reason.")
    assert run(cw, "--apply-inbox") == 0 and register(cw, qm.master)[1]["After"].endswith("must give a reason.")


@pytest.mark.parametrize("fields,message", [
    ({"QM Decision": "Approved"}, "takes only"),
    ({"kw_items": ["KW-99"]}, "KW-99 is not an item"),
    ({"change": "Replace", "after": ""}, "Replace needs"),
    ({"ref": "C77"}, "C77 is not on the"),
])
def test_a_bad_register_note_is_rejected_with_the_reason(cw, qm, fields, message):
    qm_note(qm, "chat-qm-0005", **fields)
    assert run(cw, "--apply-inbox") == 8 and register(cw, qm.master) == []
    result = json.loads((qm.inbox / "rejected" / "chat-qm-0005.result.json").read_text(encoding="utf-8"))
    assert result["result"] == "rejected" and message in result["reason"]


def test_a_register_note_before_the_setup_is_rejected_and_the_format_text_describes_the_kind(cw, env):
    qm_note(env, "chat-qm-0006")
    assert run(cw, "--apply-inbox") == 8
    assert "run --qm-setup" in json.loads((env.inbox / "rejected" / "chat-qm-0006.result.json").read_text(encoding="utf-8"))["reason"]
    assert "qm_entry" in cw.NOTE_FORMAT and "Five kinds of note" in cw.NOTE_FORMAT and "QM Decision" in cw.NOTE_FORMAT
    note(env, "chat-qm-0007", kind="delete_item", note_id="chat-qm-0007", fields={"x": "y"})
    run(cw, "--apply-inbox")
    assert "or 'qm_entry'" in json.loads((env.inbox / "rejected" / "chat-qm-0007.result.json").read_text(encoding="utf-8"))["reason"]


# ---- the backfill ---------------------------------------------------------------------------------------------------------------------------------------------------------
def backfill(env, impacts, register=None, name="backfill.json"):
    path = env.tmp / name
    path.write_text(json.dumps({"impacts": impacts, "register": register or []}), encoding="utf-8")
    return str(path)


def test_the_backfill_writes_impacts_for_released_items_only_where_blank_and_never_stamps(cw, qm):
    before = row(cw, qm.master, "KW-03")
    f = backfill(qm, [{"id": "KW-03", "impact": "PROPOSED Requirement changed - the label text is in the manual"}])
    assert run(cw, "--qm-backfill", f) == 0
    after = row(cw, qm.master, "KW-03")
    assert after["QM-001 Impact"] == "PROPOSED Requirement changed - the label text is in the manual" and after["Row Updated"] == before["Row Updated"]
    f2 = backfill(qm, [{"id": "KW-03", "impact": "None - overwritten?"}], name="again.json")
    assert run(cw, "--qm-backfill", f2) == 0 and row(cw, qm.master, "KW-03")["QM-001 Impact"].startswith("PROPOSED Requirement changed")    # an existing impact is never overwritten


def test_the_backfill_refuses_a_file_that_names_an_item_that_is_not_released(cw, qm, capsys):
    original = open(qm.master, "rb").read()
    f = backfill(qm, [{"id": "KW-03", "impact": "None - x"}, {"id": "KW-01", "impact": "None - x"}])
    assert run(cw, "--qm-backfill", f) == 2 and "KW-01 is 'Not started', not Released" in capsys.readouterr().out and open(qm.master, "rb").read() == original


def test_the_backfill_validates_every_impact(cw, qm, capsys):
    f = backfill(qm, [{"id": "KW-03", "impact": "probably nothing"}])
    assert run(cw, "--qm-backfill", f) == 2 and "'<class> - <one line>'" in capsys.readouterr().out


def test_the_backfill_creates_a_register_row_once_for_each_ref_the_manual_already_carries(cw, qm):
    f = backfill(qm, [], register=[{"ref": "C10", "kw_items": ["KW-03"], "date": "2026-10-01"}, {"ref": "B11", "kw_items": "KW-03; KW-05", "date": "2026-09-01"}])
    assert run(cw, "--qm-backfill", f) == 0
    rows = register(cw, qm.master)
    assert [r["Ref"] for r in rows] == ["C10", "B11"] and rows[0]["Drafted by"] == "backfill" and rows[0]["Manual section / clause"] == "see the manual's change list" and rows[1]["KW items"] == "KW-03; KW-05"
    assert all(r[h] == "" for r in rows for h in cw.QM_HUMAN)
    assert run(cw, "--qm-backfill", backfill(qm, [], register=[{"ref": "C10", "kw_items": ["KW-03"]}], name="again.json")) == 0 and len(register(cw, qm.master)) == 2     # not created twice
    assert entry(cw, qm) == 0 and register(cw, qm.master)[2]["Ref"] == "C11"                                                                                                # and the numbering continues after C10


def test_a_released_item_can_cite_a_backfilled_ref_for_a_later_change(cw, qm):
    assert run(cw, "--qm-backfill", backfill(qm, [], register=[{"ref": "C10", "kw_items": ["KW-03"]}])) == 0
    assert run(cw, "--id", "KW-05", "QM-001 Impact=Requirement changed - already in the manual", "QM-001 Ref=C10", "PR #=81", "Released Date=2026-10-29", "Status=Released") == 0


def test_a_bad_register_row_in_the_backfill_stops_the_whole_file(cw, qm, capsys):
    original = open(qm.master, "rb").read()
    f = backfill(qm, [{"id": "KW-03", "impact": "None - x"}], register=[{"ref": "X1", "kw_items": ["KW-03"]}])
    assert run(cw, "--qm-backfill", f) == 2 and open(qm.master, "rb").read() == original and "needs a ref like C10 or B11" in capsys.readouterr().out


# ---- marking drafts, and the report ---------------------------------------------------------------------------------------------------------------------------------------
def test_only_an_approved_entry_can_be_marked_as_drafted_into_a_revision(cw, qm, capsys):
    assert entry(cw, qm) == 0
    assert run(cw, "--qm-drafted", "C1", "--text", "QM-001_Rev5.2_DRAFT.docx (2026-10-29)") == 2 and "C1 is not Approved by the Quality Manager" in capsys.readouterr().out
    assert run(cw, "--qm-drafted", "C9", "--text", "x.docx") == 2 and "C9 is not on the" in capsys.readouterr().out
    decide(cw, qm, "C1", **{"QM Decision": "Approved", "Decided By": "N. Wrana", "Decision Date": "2026-10-29"})
    assert run(cw, "--qm-drafted", "C1", "--text", "QM-001_Rev5.2_DRAFT.docx (2026-10-29)") == 0
    assert register(cw, qm.master)[0]["Drafted In"] == "QM-001_Rev5.2_DRAFT.docx (2026-10-29)"
    decide(cw, qm, "C1", **{"Entered in Manual (date)": "2026-11-01", "Manual Revision": "5.2"})
    assert run(cw, "--qm-drafted", "C1", "--text", "another.docx") == 2 and "already entered in the manual" in capsys.readouterr().out


def report(cw, capsys):
    assert run(cw, "--qm-report") == 0
    return capsys.readouterr().out


def test_the_report_lists_the_three_things_that_are_open(cw, qm, capsys):
    assert run(cw, "--id", "KW-03", "QM-001 Impact=PROPOSED None - a guess") == 0
    assert entry(cw, qm) == 0 and entry(cw, qm, tmp_name="b.json", section="2.4") == 0 and entry(cw, qm, tmp_name="c.json", section="2.5") == 0
    decide(cw, qm, "C2", **{"QM Decision": "Approved", "Decided By": "N. Wrana", "Decision Date": "2026-10-29"})
    decide(cw, qm, "C3", **{"QM Decision": "Approved", "Decided By": "N. Wrana", "Decision Date": "2026-10-29", "Drafted In": "QM-001_Rev5.2_DRAFT.docx"})
    out = report(cw, capsys)
    assert "1. Released items with no confirmed QM-001 impact: 1 of 1" in out and "KW-03" in out and "PROPOSED, not yet confirmed" in out
    assert "2. Register entries waiting for the Quality Manager's decision: 1" in out and "C1  KW-01" in out
    assert "3. Approved entries not yet in the manual: 2 (1 not yet in a draft revision, 1 drafted)" in out and "[not yet drafted]" in out and "[drafted in: QM-001_Rev5.2_DRAFT.docx]" in out
    decide(cw, qm, "C3", **{"Entered in Manual (date)": "2026-11-01"})
    assert "Approved entries not yet in the manual: 1 (1 not yet in a draft revision, 0 drafted)" in report(cw, capsys)


def test_the_report_puts_the_candidates_first_and_does_not_chase_backfilled_refs(cw, qm, capsys):
    items = [dict(id="KW-03", impact="PROPOSED None - x")]
    assert run(cw, "--qm-backfill", backfill(qm, items, register=[{"ref": "C10", "kw_items": ["KW-03"]}])) == 0
    out = report(cw, capsys)
    assert "2. Register entries waiting for the Quality Manager's decision: 0" in out and "1 register row(s) were entered in the manual before the register existed" in out
    assert cw.QM_CANDIDATES == ("KW-23", "KW-51", "KW-66")


def test_a_rejected_entry_is_listed_for_information_only(cw, qm, capsys):
    assert entry(cw, qm) == 0
    decide(cw, qm, "C1", **{"QM Decision": "Rejected", "Decided By": "N. Wrana"})
    out = report(cw, capsys)
    assert "2. Register entries waiting for the Quality Manager's decision: 0" in out and "4. Rejected or 'needs change' entries (for information): 1" in out


def test_the_report_says_what_to_run_when_the_workbook_has_no_setup(cw, env, capsys):
    assert "run --qm-setup" in report(cw, capsys)


def test_the_report_is_published_beside_the_workbook_copy_on_every_publish_and_is_read_only(cw, qm):
    assert run(cw, "--id", "KW-03", "QM-001 Impact=PROPOSED None - x") == 0
    target = qm.sp / "QM-001_Status_Report.txt"
    assert target.exists() and read_only(str(target)) and "Released items with no confirmed QM-001 impact: 1 of 1" in target.read_text(encoding="utf-8")
    assert run(cw, "--id", "KW-03", "QM-001 Impact=None - checked") == 0
    assert "Released items with no confirmed QM-001 impact: 0 of 1" in target.read_text(encoding="utf-8")                              # refreshed, although it is read-only


def test_a_failed_report_is_a_warning_not_an_error(cw, qm, monkeypatch, capsys):
    monkeypatch.setattr(cw, "qm_report", lambda snap: (_ for _ in ()).throw(OSError("disk full")))
    assert run(cw, "--id", "KW-03", "QM-001 Impact=None - checked") == 0
    assert "WARNING: the QM-001 status report could not be written (disk full)" in capsys.readouterr().out


def test_no_report_file_is_written_while_the_workbook_has_no_setup(cw, env):
    assert run(cw, "--id", "KW-01", "Notes=x") == 0 and not (env.sp / "QM-001_Status_Report.txt").exists()


# ---- the whole path -------------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_whole_path_from_a_draft_to_released_and_chased(cw, qm, capsys):
    assert entry(cw, qm) == 0                                                                                                          # 1. Claude Code drafts the manual change (C1)
    assert release(cw, "KW-01", "QM-001 Impact=Requirement changed - who can lift a hold", "QM-001 Ref=C1") == 0                       # 2. the gate lets the release through
    r = row(cw, qm.master, "KW-01")
    assert (r["Status"], r["QM-001 Ref"]) == ("Released", "C1")
    assert "Register entries waiting for the Quality Manager's decision: 1" in report(cw, capsys)                                      # 3. the report chases the decision
    decide(cw, qm, "C1", **{"QM Decision": "Approved", "Decided By": "N. Wrana", "Decision Date": "2026-10-29"})                       # 4. the Quality Manager decides in Excel
    assert "1 not yet in a draft revision" in report(cw, capsys)
    assert run(cw, "--qm-drafted", "C1", "--text", "QM-001_Rev5.2_DRAFT.docx") == 0                                                    # 5. a draft revision is generated
    assert "0 not yet in a draft revision, 1 drafted" in report(cw, capsys)
    decide(cw, qm, "C1", **{"Manual Revision": "5.2", "Entered in Manual (date)": "2026-11-05"})                                       # 6. entered in the manual
    assert "Approved entries not yet in the manual: 0" in report(cw, capsys)


# ---- the export, the shipped files and the documents ----------------------------------------------------------------------------------------------------------------------
def test_the_export_carries_the_impact_column_and_the_register_when_the_workbook_has_them(cw, qm, monkeypatch):
    from test_changelog_storage import load
    export = load("export_backlog")
    monkeypatch.setattr(export.os.path, "dirname", lambda p, _d=os.path.dirname: str(qm.tmp / "tools") if p.endswith("export_backlog.py") else _d(p))
    assert entry(cw, qm) == 0 and run(cw, "--id", "KW-03", "QM-001 Impact=None - checked") == 0
    assert export.main(["export_backlog.py"]) == 0
    out = qm.tmp / "docs" / "backlog"
    items = (out / "items.csv").read_text(encoding="utf-8-sig").splitlines()
    assert "QM-001 Impact" in items[0] and any("None - checked" in line for line in items)
    register_csv = (out / "qm001-changes.csv").read_text(encoding="utf-8-sig").splitlines()
    assert register_csv[0].startswith("Ref,Date,KW items,Manual section / clause,Change,Before,After") and register_csv[1].startswith("C1,")


def test_the_export_writes_no_register_file_for_a_workbook_without_the_sheet(cw, env, monkeypatch):
    from test_changelog_storage import load
    export = load("export_backlog")
    monkeypatch.setattr(export.os.path, "dirname", lambda p, _d=os.path.dirname: str(env.tmp / "tools") if p.endswith("export_backlog.py") else _d(p))
    assert export.main(["export_backlog.py"]) == 0 and not (env.tmp / "docs" / "backlog" / "qm001-changes.csv").exists()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_shipped_backfill_file_is_valid_and_names_real_items(cw):
    spec = json.loads(repo_text("docs", "backlog", "qm001-backfill.json"))
    import csv
    items = {r["ID"]: r for r in csv.DictReader(open(os.path.join(ROOT, "docs", "backlog", "items.csv"), encoding="utf-8-sig"))}
    ids = [i["id"] for i in spec["impacts"]]
    assert len(ids) == len(set(ids)) and all(i in items for i in ids)
    for i in spec["impacts"]:
        assert cw.check_impact(i["impact"]) == i["impact"], i["id"]                                                                     # every value is already in standard form
    assert all(re.fullmatch(r"[BC]\d+", r["ref"]) and r["kw_items"] for r in spec["register"])
    assert len({r["ref"] for r in spec["register"]}) == len(spec["register"])
    confirmed = [i for i in spec["impacts"] if not i["impact"].startswith("PROPOSED")]
    assert confirmed and all(re.search(r"QM-001|no requirement changed", i["impact"]) for i in confirmed)                              # only what the item's own record settles is confirmed


def test_the_rules_sheet_text_describes_the_gate_the_register_and_the_draft_revision(cw):
    rules = repo_text("docs", "backlog", "workflow-rules.json")
    for needle in ("QM-001 Impact", "QM-001 Changes", "qm_entry", "--qm-setup", "--qm-report", "qm_draft_revision.py", "docs/qm001-change-tracking.md", "PROPOSED") + tuple(cw.IMPACT_CLASSES):
        assert needle in rules, needle
    for human in cw.QM_HUMAN:
        assert human in rules or human == "Decided By" or human == "Decision Date", human
    spec = cw.load_rules(os.path.join(ROOT, "docs", "backlog", "workflow-rules.json"))
    assert [s["heading"][:2] for s in spec["sections"]] == ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8."]


def test_the_process_documents_carry_the_impact_rule():
    guide = repo_text("docs", "qm001-change-tracking.md")
    for needle in ("QM-001 Impact", "QM-001 Changes", "--qm-entry", "qm_draft_revision.py", "--qm-report", "--qm-backfill", "PROPOSED", "refuses", "never"):
        assert needle in guide, needle
    for name, needles in {
        os.path.join(".claude", "commands", "kw-item.md"): ("QM-001 impact", "--qm-entry", "pull_request_template", "qm_draft_revision.py"),
        os.path.join("docs", "sop-change-requests.md"): ("QM-001 Impact", "QM-001 Changes", "qm_draft_revision.py"),
        os.path.join("docs", "release-guide.md"): ("QM-001 impact (KW-71)", "docs/qm001-change-tracking.md"),
        "CLAUDE.md": ("QM-001 impact tracking (KW-71", "impact_gate", "qm_draft_revision.py"),
        os.path.join("docs", "backlog", "inbox-note-format.md"): ("qm_entry", "Replace", "insert_after"),
        os.path.join("docs", "backlog", "README.md"): ("qm001-backfill.json", "qm_draft_revision.py"),
    }.items():
        text = repo_text(name)
        for n in needles:
            assert n in text, (name, n)
    template = repo_text(".github", "pull_request_template.md")
    assert "## QM-001 impact" in template and "Requirement changed" in template and "Record / control changed" in template


def test_every_impact_class_and_register_column_is_written_in_the_guide(cw):
    guide = repo_text("docs", "qm001-change-tracking.md")
    for c in cw.IMPACT_CLASSES[1:]:
        assert c in guide, c
    for column in cw.QM_COLUMNS:
        assert column.split(" (")[0] in guide, column