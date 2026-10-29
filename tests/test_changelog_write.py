"""tools/changelog_write.py (KW-61): the only way Claude Code updates the change log. It writes only the columns Claude Code owns, never starts an item that is not approved, never sets Deferred /
Won't do, stops when Excel has the file open or the file changed while it ran, changes nothing but the named cells (and the two stamps), and checks after saving that the values stuck."""
import importlib.util
import os
import sys
import zipfile
from xml.sax.saxutils import escape

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def load():
    spec = importlib.util.spec_from_file_location("changelog_write", os.path.join(ROOT, "tools", "changelog_write.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HEADERS = ["ID", "Module", "Section", "Type", "Change Request", "Context / Rationale", "Transcript Time", "Raised By", "Priority", "Lens", "Claude Code Mode", "Claude Code Prompt",
           "Acceptance Criteria", "Status", "Owner", "Notes", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date", "QM-001 Ref", "Row Updated", "Updated By",
           "QM-001 Impact"]
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def row_xml(r, values, skip=()):
    cells = ""
    for i, h in enumerate(HEADERS):
        v = values.get(h)
        if h in skip:
            continue                                                         # a cell that does not exist in the file at all
        if v is None:
            cells += '<c r="%s%d" s="7"/>' % (col(i), r)
        else:
            cells += '<c r="%s%d" s="7" t="inlineStr"><is><t>%s</t></is></c>' % (col(i), r, escape(v))
    return '<row r="%d">%s</row>' % (r, cells)


ROWS = [
    dict(ID="KW-01", Status="Not started", **{"Approved to Start": "Y", "Change Request": "keep me", "Owner": "NW"}),
    dict(ID="KW-02", Status="Not started", **{"Approved to Start": ""}),
    dict(ID="KW-03", Status="Not started", **{"Approved to Start": "Pre-rule"}),
    dict(ID="KW-04", Status="Deferred", **{"Approved to Start": "Y"}),
    dict(ID="KW-05", Status="In progress", **{"Approved to Start": "Y", "PR #": "70", "Released Date": "2026-10-20"}),
    dict(ID="KW-06", Status="Not started", **{"Approved to Start": "Y"}),
    dict(ID="KW-06", Status="Not started", **{"Approved to Start": "Y"}),                 # a repeated ID
]


def make(path, headers=HEADERS):
    global HEADERS
    rows = "".join(row_xml(r, v, skip=("PR #",) if v["ID"] == "KW-03" else ()) for r, v in enumerate(ROWS, 2))     # KW-03 has no PR # cell: it must be added in column order
    head = '<row r="1">%s</row>' % "".join('<c r="%s1" s="1" t="inlineStr"><is><t>%s</t></is></c>' % (col(i), escape(h)) for i, h in enumerate(headers))
    sheet = '<?xml version="1.0"?><worksheet %s><sheetData>%s%s</sheetData><dataValidations count="1"><dataValidation type="list" sqref="N2:N8"><formula1>"Not started"</formula1></dataValidation></dataValidations></worksheet>' % (NS, head, rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook %s><sheets><sheet name="Summary" sheetId="1" r:id="rId1"/><sheet name="Change Log" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="1"/></workbook>' % NS)
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><worksheet %s><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Summary</t></is></c></row></sheetData></worksheet>' % NS)
        z.writestr("xl/worksheets/sheet2.xml", sheet)
        z.writestr("customXml/item1.xml", "<keep/>")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test may touch the real SharePoint copy, mirror or inbox: all three point into the test folder."""
    for var, folder in (("KELPWORKS_SNAPSHOT", "sp/" + "LOG_KelpWorks_ChangeControl.xlsx"), ("KELPWORKS_BACKUP_MIRROR", "sp/Backups"), ("KELPWORKS_INBOX", "sp/Inbox")):
        monkeypatch.setenv(var, str(tmp_path / folder))
    (tmp_path / "sp").mkdir()
    monkeypatch.delenv("KELPWORKS_CHANGELOG", raising=False)


@pytest.fixture
def cw():
    return load()


@pytest.fixture
def wb(tmp_path):
    folder = tmp_path / "ChangeControl"
    folder.mkdir()
    p = folder / "LOG_KelpWorks_ChangeControl.xlsx"
    make(str(p))
    return str(p)


def row_of(cw, path, kw, nth=0):
    snap = cw.Snapshot(path)
    return snap.values(snap.row_of(kw))


def write(cw, path, *args, wait="0"):
    return cw.main(["--file", path, "--verify-wait", wait, *args])


def test_it_writes_only_the_named_cells_and_stamps_the_row(cw, wb):
    with zipfile.ZipFile(wb) as z:
        before = {n: z.read(n) for n in z.namelist()}
    assert write(cw, wb, "--id", "KW-01", "Status=In progress", "Notes=building it") == 0
    after = row_of(cw, wb, "KW-01")
    assert after["Status"] == "In progress" and after["Notes"] == "building it" and after["Updated By"] == "Claude Code" and after["Row Updated"]
    assert after["Change Request"] == "keep me" and after["Owner"] == "NW" and after["Approved to Start"] == "Y"                    # nothing else in the row changed
    assert row_of(cw, wb, "KW-02")["Status"] == "Not started"                                                                        # nor any other row
    with zipfile.ZipFile(wb) as z:
        for n in z.namelist():
            if n not in ("xl/worksheets/sheet2.xml", "xl/workbook.xml"):
                assert z.read(n) == before[n], n                                                                                      # the other parts are byte-for-byte what they were
        sheet = z.read("xl/worksheets/sheet2.xml").decode()
    assert '<dataValidation type="list" sqref="N2:N8">' in sheet and 's="7"' in sheet                                                # drop-down and styles survive


@pytest.mark.parametrize("column", ["Approved to Start", "Owner", "Verified By (on live)", "Verified Date", "Change Request", "Priority", "ID", "Row Updated", "Updated By", "Nonsense"])
def test_a_column_claude_code_does_not_own_is_refused(cw, wb, column, capsys):
    original = open(wb, "rb").read()
    assert write(cw, wb, "--id", "KW-01", "%s=x" % column) == 2
    assert open(wb, "rb").read() == original and "Nothing was written" in capsys.readouterr().out


def test_deferred_and_wont_do_are_a_humans_and_a_human_status_is_not_changed_back(cw, wb, capsys):
    for status in ("Deferred", "Won't do"):
        assert write(cw, wb, "--id", "KW-01", "Status=%s" % status) == 2
    assert write(cw, wb, "--id", "KW-04", "Status=In progress") == 2                                    # KW-04 is Deferred: only a human moves it
    assert write(cw, wb, "--id", "KW-01", "Status=Finished") == 2
    assert "KW-04 is 'Deferred'" in capsys.readouterr().out
    assert row_of(cw, wb, "KW-04")["Status"] == "Deferred"


def test_an_item_that_is_not_approved_cannot_be_started_but_pre_rule_and_y_can(cw, wb, capsys):
    assert write(cw, wb, "--id", "KW-02", "Status=In progress") == 2
    assert "Approved to Start is 'blank'" in capsys.readouterr().out
    assert write(cw, wb, "--id", "KW-02", "Status=On staging") == 2
    assert write(cw, wb, "--id", "KW-02", "Status=Blocked", "Notes=waiting for a decision") == 0                 # not starting work
    assert write(cw, wb, "--id", "KW-03", "Status=In progress") == 0 and write(cw, wb, "--id", "KW-01", "Status=In progress") == 0
    assert row_of(cw, wb, "KW-02")["Status"] == "Blocked" and row_of(cw, wb, "KW-03")["Status"] == "In progress"


def test_released_needs_a_pr_number_and_a_date_and_they_are_checked(cw, wb, capsys):
    assert write(cw, wb, "--id", "KW-03", "Status=Released") == 2 and "PR #" in capsys.readouterr().out
    assert write(cw, wb, "--id", "KW-01", "Status=Released", "PR #=75") == 2 and "Released Date" in capsys.readouterr().out
    assert write(cw, wb, "--id", "KW-01", "Status=Released", "PR #=seventy", "Released Date=2026-10-21") == 2
    assert write(cw, wb, "--id", "KW-01", "Status=Released", "PR #=75", "Released Date=21/10/2026") == 2
    assert write(cw, wb, "--id", "KW-01", "Status=Released", "PR #=#75", "Released Date=2026-10-21", "QM-001 Ref=C20", "QM-001 Impact=None - fixture item") == 0
    r = row_of(cw, wb, "KW-01")
    assert (r["Status"], r["PR #"], r["Released Date"], r["QM-001 Ref"]) == ("Released", "75", "2026-10-21", "C20")
    assert write(cw, wb, "--id", "KW-05", "Status=Released", "QM-001 Impact=None - fixture item") == 0      # the row already has its PR # and date


def test_released_is_refused_when_the_workbook_has_no_impact_column_and_says_what_to_run(cw, tmp_path, capsys):
    (tmp_path / "ChangeControl").mkdir()
    p = tmp_path / "ChangeControl" / "LOG_KelpWorks_ChangeControl.xlsx"
    make(str(p), headers=[h for h in HEADERS if h != "QM-001 Impact"])
    original = p.read_bytes()
    assert write(cw, str(p), "--id", "KW-05", "Status=Released") == 2
    assert "--qm-setup" in capsys.readouterr().out and p.read_bytes() == original


def test_rows_are_found_by_id_and_an_unknown_or_repeated_id_is_refused(cw, wb, capsys):
    assert write(cw, wb, "--id", "KW-99", "Notes=x") == 2 and "inbox note" in capsys.readouterr().out
    assert write(cw, wb, "--id", "KW-06", "Notes=x") == 2 and "2 times" in capsys.readouterr().out
    assert write(cw, wb, "--id", "KW-01", "Status") == 2                                                 # not column=value


def test_a_missing_cell_is_added_in_its_column_order(cw, wb):
    assert "PR #" not in {k for k, v in row_of(cw, wb, "KW-03").items() if v}
    assert write(cw, wb, "--id", "KW-03", "PR #=12") == 0
    assert row_of(cw, wb, "KW-03")["PR #"] == "12"
    snap = cw.Snapshot(wb)
    refs = [r for r, _c in cw.xl.cells_of(snap.xml) if r.endswith("4")]
    nums = [int(sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed("".join(c for c in r if c.isalpha()))))) for r in refs]
    assert nums == sorted(nums)


def test_nothing_is_written_when_the_workbook_already_says_it(cw, wb, capsys):
    assert write(cw, wb, "--id", "KW-05", "Status=In progress") == 0
    assert "Nothing to change" in capsys.readouterr().out and row_of(cw, wb, "KW-05")["Updated By"] == ""


def test_a_dry_run_checks_the_rules_but_writes_nothing(cw, wb):
    original = open(wb, "rb").read()
    assert write(cw, wb, "--id", "KW-01", "Status=In progress", "--dry-run") == 0
    assert write(cw, wb, "--id", "KW-02", "Status=In progress", "--dry-run") == 2
    assert open(wb, "rb").read() == original


def test_it_stops_when_excel_has_the_workbook_open(cw, wb, tmp_path, capsys):
    original = open(wb, "rb").read()
    lock = tmp_path / "ChangeControl" / "~$G_KelpWorks_ChangeControl.xlsx"
    lock.write_text("x")
    assert write(cw, wb, "--id", "KW-01", "Status=In progress") == 3 and "open in Excel" in capsys.readouterr().out
    assert open(wb, "rb").read() == original
    assert write(cw, wb, "--show", "KW-01") == 0                                                         # reading is still fine
    lock.unlink()
    assert write(cw, wb, "--id", "KW-01", "Status=In progress") == 0


def test_it_aborts_when_the_file_changed_while_it_ran(cw, wb, monkeypatch, capsys):
    snap = cw.Snapshot(wb)
    assert snap.unchanged()
    with zipfile.ZipFile(wb) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["customXml/item1.xml"] = b"<changed/>"
    with zipfile.ZipFile(wb, "w") as z:                                                                  # someone saves the workbook after we read it
        for n, b in parts.items():
            z.writestr(n, b)
    assert not snap.unchanged()
    # and through the command: the file changes between planning and saving
    make(wb)
    real = cw.Snapshot.unchanged
    state = {"n": 0}
    def changed_once(self):
        state["n"] += 1
        return False
    monkeypatch.setattr(cw.Snapshot, "unchanged", changed_once)
    original = open(wb, "rb").read()
    assert write(cw, wb, "--id", "KW-01", "Status=In progress") == 4 and "changed while this was running" in capsys.readouterr().out
    assert open(wb, "rb").read() == original


def test_a_value_that_does_not_stick_is_reported_as_a_revert(cw, wb, monkeypatch, capsys):
    stale = open(wb, "rb").read()
    def revert(_seconds):                                                                                 # something saves an older copy over our change
        with open(wb, "wb") as f:
            f.write(stale)
    monkeypatch.setattr(cw.time, "sleep", revert)
    assert write(cw, wb, "--id", "KW-01", "Status=In progress", wait="5") == 5
    out = capsys.readouterr().out
    assert "REVERT DETECTED" in out and "KW-01 Status" in out
    assert row_of(cw, wb, "KW-01")["Status"] == "Not started"


def test_a_batch_is_checked_as_a_whole_and_written_once(cw, wb, tmp_path, capsys):
    import json
    good = tmp_path / "u.json"
    good.write_text(json.dumps([{"id": "KW-01", "set": {"Status": "In progress"}}, {"id": "KW-03", "set": {"Status": "In progress", "Notes": "n"}}]), encoding="utf-8")
    assert write(cw, wb, "--batch", str(good)) == 0
    assert row_of(cw, wb, "KW-01")["Status"] == "In progress" and row_of(cw, wb, "KW-03")["Notes"] == "n"
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps([{"id": "KW-05", "set": {"Notes": "ok"}}, {"id": "KW-02", "set": {"Status": "In progress"}}]), encoding="utf-8")
    assert write(cw, wb, "--batch", str(bad)) == 2
    assert row_of(cw, wb, "KW-05")["Notes"] == ""                                                         # one refusal stops the whole batch
    twice = tmp_path / "t.json"
    twice.write_text(json.dumps([{"id": "KW-05", "set": {"Notes": "a"}}, {"id": "KW-05", "set": {"Notes": "b"}}]), encoding="utf-8")
    assert write(cw, wb, "--batch", str(twice)) == 2


def test_a_workbook_without_the_approval_column_cannot_start_an_item(cw, tmp_path, capsys):
    (tmp_path / "ChangeControl").mkdir()
    p = tmp_path / "ChangeControl" / "LOG_KelpWorks_ChangeControl.xlsx"
    make(str(p), headers=[h if h != "Approved to Start" else "Approval" for h in HEADERS])
    assert write(cw, str(p), "--id", "KW-01", "Status=In progress") == 2 and "Approved to Start" in capsys.readouterr().out


def test_show_prints_a_rows_workflow_cells_and_changes_nothing(cw, wb, capsys):
    original = open(wb, "rb").read()
    assert write(cw, wb, "--show", "KW-05") == 0
    out = capsys.readouterr().out
    assert "Approved to Start" in out and "70" in out and open(wb, "rb").read() == original
    assert write(cw, wb, "--show", "KW-99") == 2


# ---- KW-62: one change log, one setting; a write never goes to any other file
def test_a_write_to_a_copy_an_old_name_or_another_folder_is_refused_but_reading_and_dry_runs_are_not(cw, wb, tmp_path, capsys):
    import shutil
    others = {"copy": tmp_path / "ChangeControl" / "LOG_KelpWorks_ChangeControl - Copy.xlsx",
              "old name": tmp_path / "ChangeControl" / "KelpWorks_ChangeLog_2026-10-16.xlsx",
              "other folder": tmp_path / "Elsewhere" / "LOG_KelpWorks_ChangeControl.xlsx"}
    for label, p in others.items():
        p.parent.mkdir(exist_ok=True)
        shutil.copy(wb, p)
        before = p.read_bytes()
        assert write(cw, str(p), "--id", "KW-01", "Status=In progress") == 2, label
        out = capsys.readouterr().out
        assert "is not the change log" in out and "LOG_KelpWorks_ChangeControl.xlsx" in out and "Nothing was written" in out, label
        assert p.read_bytes() == before, label
        assert write(cw, str(p), "--show", "KW-01") == 0                                                   # looking is harmless
        assert write(cw, str(p), "--id", "KW-01", "Status=In progress", "--dry-run") == 0                  # so is a dry run
        assert p.read_bytes() == before
    assert write(cw, wb, "--id", "KW-01", "Status=In progress") == 0                                        # the real one is written


def test_the_path_is_one_setting_shared_by_the_writer_the_old_writer_and_the_reader(cw, monkeypatch, tmp_path):
    import changelog_path
    assert changelog_path.CHANGE_LOG_NAME == "LOG_KelpWorks_ChangeControl.xlsx" and changelog_path.DEFAULT_PATH.endswith("ChangeControl\\LOG_KelpWorks_ChangeControl.xlsx")
    assert cw.DEFAULT_PATH == changelog_path.DEFAULT_PATH
    monkeypatch.delenv("KELPWORKS_CHANGELOG", raising=False)
    assert changelog_path.configured_path() == changelog_path.DEFAULT_PATH
    other_pc = tmp_path / "SomeOtherPC" / "ChangeControl" / "LOG_KelpWorks_ChangeControl.xlsx"        # another PC: a different folder above, the same name and folder
    monkeypatch.setenv("KELPWORKS_CHANGELOG", str(other_pc))
    assert changelog_path.configured_path() == str(other_pc)
    changelog_path.check_writable(changelog_path.configured_path())


def test_no_file_in_the_repo_still_uses_the_old_name():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    old = "KelpWorks_ChangeLog"
    skip = {".git", "__pycache__", "node_modules"}
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.relative_to(root).parts) & skip) and p.suffix.lower() in (".py", ".md", ".txt", ".yml", ".yaml", ".json", ".bat", ".html", ".js", ".toml", ".cfg"):
            if p.name == "test_changelog_write.py":
                continue                                                                                   # this test names the old name on purpose
            if old in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(p.relative_to(root)))
    assert hits == [], hits
