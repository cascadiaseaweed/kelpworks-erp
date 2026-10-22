"""KW-66: the change log has ONE writer and a master outside OneDrive. tools/changelog_write.py backs the master up before every write (newest 30, a second copy in SharePoint Backups),
publishes a read-only copy to SharePoint after it, restores a backup, records Open Decisions answers, and applies the notes Claude (chat) leaves in the inbox (new items and revisions).
Everything runs in a temporary folder; no test touches the real workbook, SharePoint copy or inbox (standard library only)."""
import importlib.util
import json
import os
import re
import stat
import sys
import time
import zipfile
from xml.sax.saxutils import escape

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
NAME = "LOG_KelpWorks_ChangeControl.xlsx"
HEADERS = ["ID", "Module", "Section", "Type", "Change Request", "Context / Rationale", "Transcript Time", "Raised By", "Priority", "Lens", "Claude Code Mode", "Claude Code Prompt",
           "Acceptance Criteria", "Status", "Owner", "Notes", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date", "QM-001 Ref", "Row Updated", "Updated By"]
DEC_HEADERS = ["ID", "Decision Needed", "Options Discussed", "Recommendation (Claude)", "Decided By", "Decision"]
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
LAST = "X"


def col(n):
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cells(r, headers, values, style):
    out = ""
    for i, h in enumerate(headers):
        v = values.get(h)
        out += ('<c r="%s%d" s="%d" t="inlineStr"><is><t>%s</t></is></c>' % (col(i), r, style, escape(v))) if v else '<c r="%s%d" s="%d"/>' % (col(i), r, style)
    return out


ITEMS = [
    {"ID": "KW-01", "Change Request": "Add a notes box", "Priority": "P1", "Claude Code Mode": "Direct", "Status": "Not started", "Approved to Start": "Y", "Row Updated": "2026-10-20", "Updated By": "Claude (chat)"},
    {"ID": "KW-02", "Change Request": "Rename the tab", "Priority": "P2", "Claude Code Mode": "Plan mode", "Status": "Not started", "Approved to Start": "", "Row Updated": "2026-10-20", "Updated By": "Claude (chat)"},
    {"ID": "KW-03", "Change Request": "Fix the label", "Priority": "P2", "Claude Code Mode": "Direct", "Status": "Released", "Approved to Start": "Y", "PR #": "70", "Released Date": "2026-10-20", "Row Updated": "2026-10-20", "Updated By": "Claude Code"},
    {"ID": "KW-04", "Change Request": "Drop the report", "Priority": "P3", "Claude Code Mode": "Direct", "Status": "Won't do", "Approved to Start": "", "Row Updated": "2026-10-20", "Updated By": "N. Wrana"},
    {"ID": "KW-05", "Change Request": "Speed up the page", "Priority": "P1", "Claude Code Mode": "Plan mode", "Status": "In progress", "Approved to Start": "Y", "Row Updated": "2026-10-20", "Updated By": "Claude Code"},
]
DECISIONS = [
    {"ID": "KW-05", "Decision Needed": "Make it optional?"},
    {"ID": "KW-04", "Decision Needed": "Keep the old report?"},
    {"ID": "KW-04", "Decision Needed": "Archive the data?"},
]


def make(path, items=ITEMS):
    last = len(items) + 1
    rows = '<row r="1" spans="1:24" ht="30" customHeight="1">%s</row>' % cells(1, HEADERS, {h: h for h in HEADERS}, 1)
    for r, item in enumerate(items, 2):
        rows += '<row r="%d" spans="1:24" ht="60" customHeight="1">%s</row>' % (r, cells(r, HEADERS, item, 7 + (r % 2)))
    log = ('<?xml version="1.0"?><worksheet %s><dimension ref="A1:%s%d"/><cols><col min="1" max="1" width="9"/><col min="5" max="5" width="40"/></cols><sheetData>%s</sheetData>'
           '<autoFilter ref="A1:%s%d"/><conditionalFormatting sqref="I2:I%d"><cfRule type="expression" priority="1"/></conditionalFormatting>'
           '<conditionalFormatting sqref="A2:M%d"><cfRule type="expression" priority="2"/></conditionalFormatting>'
           '<dataValidations count="2"><dataValidation type="list" sqref="N2:N%d"><formula1>"Not started"</formula1></dataValidation>'
           '<dataValidation type="list" sqref="I2:I%d"><formula1>"P1"</formula1></dataValidation></dataValidations></worksheet>') % (NS, LAST, last, rows, LAST, last, last, last, last, last)
    dec = '<row r="1">%s</row>' % cells(1, DEC_HEADERS, {h: h for h in DEC_HEADERS}, 1)
    for r, d in enumerate(DECISIONS, 2):
        dec += '<row r="%d">%s</row>' % (r, cells(r, DEC_HEADERS, d, 7))
    rules_rows = ('<row r="1" spans="1:3" ht="17.25" customHeight="1"><c r="A1" s="1" t="inlineStr"><is><t>Old title</t></is></c></row>'
                  '<row r="2" spans="1:3" ht="15" customHeight="1"><c r="A2" s="2" t="inlineStr"><is><t>Old subtitle</t></is></c></row>'
                  '<row r="4" spans="1:3" ht="15" customHeight="1"><c r="A4" s="9" t="inlineStr"><is><t>1. Old heading</t></is></c></row>'
                  '<row r="5" spans="1:3" ht="15" customHeight="1"><c r="A5" s="10" t="inlineStr"><is><t>Owner</t></is></c><c r="B5" s="10" t="inlineStr"><is><t>Columns</t></is></c><c r="C5" s="10" t="inlineStr"><is><t>Rules</t></is></c></row>'
                  '<row r="6" spans="1:3" ht="15" customHeight="1"><c r="A6" s="11" t="inlineStr"><is><t>Old</t></is></c><c r="B6" s="11" t="inlineStr"><is><t>rule</t></is></c><c r="C6" s="11" t="inlineStr"><is><t>text</t></is></c></row>'
                  '<row r="8" spans="1:3" ht="100" customHeight="1"><c r="A8" s="28" t="inlineStr"><is><t>Old pasted box</t></is></c><c r="B8" s="28"/><c r="C8" s="28"/></row>')
    plan_rows = ""
    for r, (a_, b_, c_) in enumerate([("Step", "Who", "Done"), ("0. Decide", "N. Wrana", ""), ("1. Freeze", "N. Wrana", "")], 1):
        plan_rows += '<row r="%d">%s</row>' % (r, "".join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (col_, r, escape(v)) if v else '<c r="%s%d"/>' % (col_, r) for col_, v in zip("ABC", (a_, b_, c_))))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook %s><sheets><sheet name="Summary" sheetId="1" r:id="rId1"/><sheet name="Change Log" sheetId="2" r:id="rId2"/>'
                   '<sheet name="Open Decisions" sheetId="3" r:id="rId3"/><sheet name="Workflow Rules" sheetId="4" r:id="rId4"/><sheet name="KW-60 Clean Start Plan" sheetId="5" r:id="rId5"/></sheets><definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="1" hidden="1">\'Change Log\'!$A$1:$%s$%d</definedName></definedNames>'
                   '<calcPr calcId="1"/></workbook>' % (NS, LAST, last))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join('<Relationship Id="rId%d" Type="x" Target="worksheets/sheet%d.xml"/>' % (i, i) for i in (1, 2, 3, 4, 5)) + '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><worksheet %s><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Summary</t></is></c></row></sheetData></worksheet>' % NS)
        z.writestr("xl/worksheets/sheet2.xml", log)
        z.writestr("xl/worksheets/sheet3.xml", '<?xml version="1.0"?><worksheet %s><sheetData>%s</sheetData></worksheet>' % (NS, dec))
        z.writestr("xl/worksheets/sheet4.xml", '<?xml version="1.0"?><worksheet %s><dimension ref="A1:C8"/><cols><col min="1" max="1" width="20"/><col min="2" max="2" width="30"/><col min="3" max="3" width="60"/></cols>'
                   '<sheetData>%s</sheetData><mergeCells count="1"><mergeCell ref="A8:C8"/></mergeCells><pageMargins left="0.75"/></worksheet>' % (NS, rules_rows))
        z.writestr("xl/worksheets/sheet5.xml", '<?xml version="1.0"?><worksheet %s><dimension ref="A1:C3"/><sheetData>%s</sheetData></worksheet>' % (NS, plan_rows))
        z.writestr("customXml/item1.xml", "<keep/>")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cw():
    return load("changelog_write")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """master = tmp/ChangeControl/<name>; the 'SharePoint' folder is tmp/sp (snapshot, Backups mirror, Inbox)."""
    sp = tmp_path / "sp"
    (sp / "Inbox").mkdir(parents=True)
    master = tmp_path / "ChangeControl" / NAME
    master.parent.mkdir()
    make(str(master))
    monkeypatch.setenv("KELPWORKS_CHANGELOG", str(master))
    monkeypatch.setenv("KELPWORKS_SNAPSHOT", str(sp / NAME))
    monkeypatch.setenv("KELPWORKS_BACKUP_MIRROR", str(sp / "Backups"))
    monkeypatch.setenv("KELPWORKS_INBOX", str(sp / "Inbox"))
    return type("Env", (), {"master": str(master), "sp": sp, "snapshot": str(sp / NAME), "mirror": sp / "Backups", "inbox": sp / "Inbox", "local": master.parent / "Backups", "tmp": tmp_path})


def run(cw, *args, wait="0"):
    return cw.main(["--verify-wait", wait, *args])


def row(cw, path, kw):
    snap = cw.Snapshot(path)
    return snap.values(snap.row_of(kw))


def backups(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def read_only(path):
    return not (os.stat(path).st_mode & stat.S_IWRITE)


def note(env, name, **obj):
    (env.inbox / (name + ".json")).write_text(json.dumps(obj), encoding="utf-8")


def new_item(**over):
    fields = {"Module": "Production Log", "Section": "Notes", "Type": "Change", "Change Request": "Show the run lot on the card", "Context / Rationale": "Asked for in the meeting",
              "Raised By": "N. Wrana", "Priority": "P2", "Claude Code Mode": "Direct", "Acceptance Criteria": "The lot shows on the card"}
    fields.update(over)
    return {"kind": "new_item", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z", "fields": fields}


# ---- only the master is written ----------------------------------------------------------------------------------------------------------------------------------------------
def test_only_the_master_is_ever_written(cw, env, capsys):
    import shutil
    cw_path = load("changelog_path")
    shutil.copy(env.master, env.snapshot)
    onedrive = env.tmp / "OneDrive - Test" / "ChangeControl"
    onedrive.mkdir(parents=True)
    shutil.copy(env.master, onedrive / NAME)
    other_name = env.tmp / "ChangeControl" / "KelpWorks_old.xlsx"
    shutil.copy(env.master, other_name)
    wrong_folder = env.tmp / "Change_Control"
    wrong_folder.mkdir()
    shutil.copy(env.master, wrong_folder / NAME)
    for target in (env.snapshot, str(onedrive / NAME), str(other_name), str(wrong_folder / NAME)):
        before = open(target, "rb").read()
        assert run(cw, "--file", target, "--id", "KW-01", "Notes=x") == 2, target
        assert "Nothing was written" in capsys.readouterr().out and open(target, "rb").read() == before
        assert run(cw, "--file", target, "--id", "KW-01", "Notes=x", "--dry-run") == 0                       # looking is harmless
        assert run(cw, "--file", target, "--show", "KW-01") == 0
    assert cw_path.configured_path() == env.master
    assert run(cw, "--id", "KW-01", "Notes=x") == 0                                                          # the master is written (no --file: the configured path)
    assert row(cw, env.master, "KW-01")["Notes"] == "x"


def test_the_default_master_is_outside_onedrive_and_a_copy_has_no_sharepoint_files(monkeypatch):
    cp = load("changelog_path")
    for var in ("KELPWORKS_CHANGELOG", "KELPWORKS_SNAPSHOT", "KELPWORKS_BACKUP_MIRROR", "KELPWORKS_INBOX"):
        monkeypatch.delenv(var, raising=False)
    assert cp.DEFAULT_PATH == r"C:\KelpWorks\ChangeControl\LOG_KelpWorks_ChangeControl.xlsx" and not cp.inside_onedrive(cp.DEFAULT_PATH)
    assert cp.snapshot_for() and cp.mirror_for() and cp.inbox_for()                                           # the default master has all three
    monkeypatch.setenv("KELPWORKS_CHANGELOG", r"D:\somewhere\ChangeControl\LOG_KelpWorks_ChangeControl.xlsx")
    assert cp.snapshot_for() is None and cp.mirror_for() is None and cp.inbox_for() is None                    # a trial master never reaches the real SharePoint files


# ---- backups ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_backup_is_taken_before_every_write_the_newest_30_are_kept_and_each_is_mirrored(cw, env):
    originals = []
    for i in range(33):
        originals.append(open(env.master, "rb").read())
        assert run(cw, "--id", "KW-01", "Notes=n%d" % i) == 0
    local, mirror = backups(env.local), backups(env.mirror)
    assert len(local) == 30 and local == mirror                                                                # 33 writes, the oldest 3 dropped, the same files in SharePoint
    assert open(env.local / local[-1], "rb").read() == originals[-1]                                           # the newest backup is the file as it was before the last write
    assert open(env.local / local[0], "rb").read() == originals[3]
    assert all(re.match(r"LOG_KelpWorks_ChangeControl_\d{8}-\d{6}-\d{2}_.+\.xlsx$", n) for n in local)


def test_a_failed_mirror_is_a_warning_and_the_next_write_fills_it_in(cw, env, monkeypatch, capsys):
    monkeypatch.setenv("KELPWORKS_BACKUP_MIRROR", str(env.tmp / "no_such_folder" / "Backups"))
    assert run(cw, "--id", "KW-01", "Notes=a") == 0 and "WARNING" in capsys.readouterr().out and len(backups(env.local)) == 1
    monkeypatch.setenv("KELPWORKS_BACKUP_MIRROR", str(env.mirror))
    assert run(cw, "--id", "KW-01", "Notes=b") == 0
    assert backups(env.mirror) == backups(env.local) and len(backups(env.local)) == 2


def test_nothing_is_written_when_no_backup_can_be_made(cw, env, monkeypatch, capsys):
    original = open(env.master, "rb").read()
    monkeypatch.setattr(cw, "make_backup", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert run(cw, "--id", "KW-01", "Notes=a") == 9 and "Nothing was written" in capsys.readouterr().out
    assert open(env.master, "rb").read() == original and not os.path.exists(env.snapshot)


def test_backups_lists_both_folders(cw, env, capsys):
    assert run(cw, "--backups") == 0 and "No backups yet" in capsys.readouterr().out
    run(cw, "--id", "KW-01", "Notes=a")
    capsys.readouterr()
    assert run(cw, "--backups") == 0 and "+mirror" in capsys.readouterr().out


# ---- the read-only SharePoint copy -------------------------------------------------------------------------------------------------------------------------------------------
def test_every_write_refreshes_a_read_only_copy_in_sharepoint(cw, env):
    assert run(cw, "--id", "KW-01", "Notes=first") == 0
    assert open(env.snapshot, "rb").read() == open(env.master, "rb").read() and read_only(env.snapshot)
    assert run(cw, "--id", "KW-01", "Notes=second") == 0                                                     # replacing a read-only file works
    assert row(cw, env.snapshot, "KW-01")["Notes"] == "second" and read_only(env.snapshot)


def test_a_snapshot_that_cannot_be_refreshed_is_reported_and_publish_catches_up(cw, env, monkeypatch, capsys):
    monkeypatch.setenv("KELPWORKS_SNAPSHOT", str(env.tmp / "gone" / NAME))
    assert run(cw, "--id", "KW-01", "Notes=a") == 7 and "NOT refreshed" in capsys.readouterr().out
    assert row(cw, env.master, "KW-01")["Notes"] == "a"                                                      # the write itself stands
    monkeypatch.setenv("KELPWORKS_SNAPSHOT", env.snapshot)
    assert run(cw, "--publish") == 0 and open(env.snapshot, "rb").read() == open(env.master, "rb").read()


def test_publish_covers_a_hand_edit_and_only_publishes_the_master(cw, env, capsys):
    data = open(env.master, "rb").read() + b""
    assert run(cw, "--publish") == 0 and open(env.snapshot, "rb").read() == data
    assert run(cw, "--file", env.snapshot, "--publish") == 2 and "Nothing was published" in capsys.readouterr().out


# ---- restore ----------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_restore_puts_a_backup_back_after_taking_one_of_the_current_file(cw, env, capsys):
    run(cw, "--id", "KW-01", "Notes=one")
    after_one = open(env.master, "rb").read()
    run(cw, "--id", "KW-01", "Notes=two")
    assert row(cw, env.master, "KW-01")["Notes"] == "two"
    assert run(cw, "--restore", "latest") == 0                                                               # latest = the file as it was before the last write
    assert open(env.master, "rb").read() == after_one and row(cw, env.master, "KW-01")["Notes"] == "one"
    assert any(n.endswith("_before-restore.xlsx") for n in backups(env.local))
    assert open(env.snapshot, "rb").read() == after_one                                                      # the SharePoint copy follows
    oldest = backups(env.local)[0]
    assert run(cw, "--restore", oldest) == 0 and row(cw, env.master, "KW-01")["Notes"] == ""                  # by name: the original
    assert run(cw, "--restore", "LOG_KelpWorks_ChangeControl_20000101-000000-01_nothing.xlsx") == 2
    assert "no backup called" in capsys.readouterr().out


def test_restore_refuses_while_excel_has_the_master_open_and_can_use_the_sharepoint_copy(cw, env, capsys):
    run(cw, "--id", "KW-01", "Notes=one")
    lock = os.path.join(os.path.dirname(env.master), "~$G_KelpWorks_ChangeControl.xlsx")
    open(lock, "w").write("x")
    before = open(env.master, "rb").read()
    assert run(cw, "--restore", "latest") == 3 and "open in Excel" in capsys.readouterr().out and open(env.master, "rb").read() == before
    os.remove(lock)
    for n in backups(env.local):                                                                              # the local folder is lost: the mirror still has them
        os.remove(env.local / n)
    assert run(cw, "--restore", "latest") == 0 and row(cw, env.master, "KW-01")["Notes"] == ""


# ---- one time migration ----------------------------------------------------------------------------------------------------------------------------------------------
def test_migrate_copies_the_sharepoint_workbook_to_the_master_and_makes_it_read_only(cw, env, tmp_path, capsys):
    os.remove(env.master)
    make(env.snapshot)                                                                                        # today's file: the writable workbook in SharePoint
    original = open(env.snapshot, "rb").read()
    assert cw.main(["--migrate", "--file", env.snapshot]) == 0
    assert open(env.master, "rb").read() == original and open(env.snapshot, "rb").read() == original and read_only(env.snapshot)
    assert len(backups(env.local)) == 1 and (env.inbox / "README_NOTE_FORMAT.txt").exists()
    assert cw.main(["--migrate", "--file", env.snapshot]) == 2 and "already exists" in capsys.readouterr().out                # never twice
    assert run(cw, "--id", "KW-01", "Notes=x") == 0


def test_migrate_stops_while_the_workbook_is_open_in_excel(cw, env, capsys):
    os.remove(env.master)
    make(env.snapshot)
    open(os.path.join(env.sp, "~$G_KelpWorks_ChangeControl.xlsx"), "w").write("x")
    assert cw.main(["--migrate", "--file", env.snapshot]) == 3 and not os.path.exists(env.master)


# ---- Open Decisions ---------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_decision_is_recorded_with_a_backup_and_a_new_snapshot(cw, env, capsys):
    assert run(cw, "--decision", "KW-05", "--by", "N. Wrana (2026-10-22)", "--text", "Make optional") == 0
    snap = cw.Snapshot(env.master)
    assert snap.cell("Open Decisions", 2, "Decided By") == "N. Wrana (2026-10-22)" and snap.cell("Open Decisions", 2, "Decision") == "Make optional"
    assert len(backups(env.local)) == 1 and open(env.snapshot, "rb").read() == open(env.master, "rb").read()
    assert run(cw, "--decision", "KW-05", "--by", "N. Wrana (2026-10-22)", "--text", "Make optional") == 0 and "Nothing to change" in capsys.readouterr().out
    assert len(backups(env.local)) == 1                                                                         # no change, no backup


def test_a_decision_for_an_id_with_several_questions_needs_to_say_which(cw, env, capsys):
    original = open(env.master, "rb").read()
    assert run(cw, "--decision", "KW-04", "--by", "N", "--text", "Yes") == 2 and "--needed" in capsys.readouterr().out and open(env.master, "rb").read() == original
    assert run(cw, "--decision", "KW-04", "--needed", "archive", "--by", "N", "--text", "Yes") == 0
    snap = cw.Snapshot(env.master)
    assert snap.cell("Open Decisions", 3, "Decision") == "" and snap.cell("Open Decisions", 4, "Decision") == "Yes"
    assert run(cw, "--decision", "KW-99", "--by", "N", "--text", "Yes") == 2 and run(cw, "--decision", "KW-05", "--by", "", "--text", "") == 2


def test_the_old_writer_no_longer_writes_anything(capsys):
    old = load("update_backlog_xlsx")
    assert old.main(["update_backlog_xlsx.py", "x.xlsx", "u.json"]) == 2 and "changelog_write.py" in capsys.readouterr().out


# ---- one writer at a time ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_second_writer_waits_and_a_stale_lock_is_replaced(cw, env, capsys):
    lock = os.path.join(os.path.dirname(env.master), ".changelog_write.lock")
    open(lock, "w").write("another run")
    before = open(env.master, "rb").read()
    assert run(cw, "--id", "KW-01", "Notes=a") == 3 and "another change-log write" in capsys.readouterr().out and open(env.master, "rb").read() == before
    old = time.time() - 7200
    os.utime(lock, (old, old))
    assert run(cw, "--id", "KW-01", "Notes=a") == 0 and not os.path.exists(lock)                                # stale: replaced, then released


def test_the_lock_is_released_when_a_rule_refuses(cw, env):
    assert run(cw, "--id", "KW-02", "Status=In progress") == 2                                                  # not approved
    assert not os.path.exists(os.path.join(os.path.dirname(env.master), ".changelog_write.lock"))


# ---- the inbox ------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_new_item_note_adds_a_row_with_the_next_id_and_keeps_the_sheet_consistent(cw, env):
    note(env, "chat-20261022-001", note_id="chat-20261022-001", **new_item())
    before = cw.Snapshot(env.master)
    assert run(cw, "--apply-inbox") == 0
    snap = cw.Snapshot(env.master)
    item = snap.values(snap.row_of("KW-06"))
    assert item["Status"] == "Not started" and item["Approved to Start"] == "" and item["Change Request"] == "Show the run lot on the card" and item["Priority"] == "P2"
    assert item["Updated By"] == "Claude (chat) (inbox)" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["Row Updated"])
    assert snap.values(snap.row_of("KW-01")) == before.values(before.row_of("KW-01"))                         # every other row is untouched
    xml = snap.xml
    assert '<dimension ref="A1:X7"/>' in xml and '<autoFilter ref="A1:X7"/>' in xml                            # every range follows the new last row
    assert 'sqref="I2:I7"' in xml and 'sqref="A2:M7"' in xml and 'sqref="N2:N7"' in xml and "I2:I6" not in xml
    assert "'Change Log'!$A$1:$X$7" in snap.files["xl/workbook.xml"].decode() and 'fullCalcOnLoad="1"' in snap.files["xl/workbook.xml"].decode()
    assert re.search(r'<c r="E7" s="\d+" t="inlineStr">', xml) and re.search(r'<c r="O7" s="\d+"/>', xml)         # the cell styles of the row above
    assert re.search(r'<row r="7"[^>]* ht="[\d.]+" customHeight="1">', xml)
    assert snap.files["customXml/item1.xml"] == b"<keep/>"                                                     # the rest of the workbook is byte for byte
    assert not (env.inbox / "chat-20261022-001.json").exists()
    result = json.loads((env.inbox / "processed" / "chat-20261022-001.result.json").read_text(encoding="utf-8"))
    assert result["result"] == "applied" and result["id"] == "KW-06" and (env.inbox / "processed" / "chat-20261022-001.json").exists()
    assert len(backups(env.local)) == 1 and open(env.snapshot, "rb").read() == open(env.master, "rb").read()
    assert (env.inbox / "README_NOTE_FORMAT.txt").exists()


def test_a_revision_note_changes_only_the_named_intake_cells(cw, env):
    note(env, "chat-rev-0001", note_id="chat-rev-0001", kind="revision", id="KW-02", based_on={"Row Updated": "2026-10-20"}, fields={"Acceptance Criteria": "It works", "Priority": "P1"},
         revision_summary="Acceptance criteria written; priority raised", **{"from": "Claude (chat)"})
    before = row(cw, env.master, "KW-02")
    assert run(cw, "--apply-inbox") == 0
    after = row(cw, env.master, "KW-02")
    assert after["Acceptance Criteria"] == "It works" and after["Priority"] == "P1" and after["Updated By"] == "Claude (chat) (inbox)" and after["Row Updated"] != "2026-10-20"
    assert re.fullmatch(r"Revised \d{4}-\d{2}-\d{2} \(Claude chat\): Acceptance criteria written; priority raised", after["Context / Rationale"])
    assert {h: v for h, v in after.items() if h not in ("Acceptance Criteria", "Priority", "Row Updated", "Updated By", "Context / Rationale")} == {h: v for h, v in before.items() if h not in ("Acceptance Criteria", "Priority", "Row Updated", "Updated By", "Context / Rationale")}
    # the same note again (an older copy) is a duplicate; a new note built on the old Row Updated is stale
    note(env, "chat-rev-0001", note_id="chat-rev-0001", kind="revision", id="KW-02", based_on={"Row Updated": after["Row Updated"]}, fields={"Lens": "x"}, revision_summary="again")
    note(env, "chat-rev-0002", note_id="chat-rev-0002", kind="revision", id="KW-02", based_on={"Row Updated": "2026-10-20"}, fields={"Lens": "x"}, revision_summary="again")
    assert run(cw, "--apply-inbox") == 8
    reasons = {f[:-len(".result.json")]: json.loads((env.inbox / "rejected" / f).read_text(encoding="utf-8"))["reason"] for f in os.listdir(env.inbox / "rejected") if f.endswith(".result.json")}
    assert "duplicate" in reasons["chat-rev-0001"] and "stale" in reasons["chat-rev-0002"]
    assert row(cw, env.master, "KW-02")["Lens"] == ""


BAD_NOTES = [
    ("not json", "{broken", "not valid JSON"),
    ("no id", dict(new_item()), "note_id is required"),
    ("bad kind", dict(new_item(), note_id="chat-bad-0001", kind="delete_item"), "kind must be"),
    ("status", dict(new_item(Status="Released"), note_id="chat-bad-0002"), None),
    ("approved", dict(new_item(), note_id="chat-bad-0003", fields=dict(new_item()["fields"], **{"Approved to Start": "Y"})), "not an intake column"),
    ("notes", dict(new_item(), note_id="chat-bad-0004", fields=dict(new_item()["fields"], Notes="hello")), "not an intake column"),
    ("id", dict(new_item(), note_id="chat-bad-0005", fields=dict(new_item()["fields"], ID="KW-01")), "ID is assigned"),
    ("unknown column", dict(new_item(), note_id="chat-bad-0006", fields=dict(new_item()["fields"], Colour="red")), "no 'Colour' column"),
    ("control chars", dict(new_item(), note_id="chat-bad-0007", fields=dict(new_item()["fields"], **{"Change Request": "a\x07b"})), "control characters"),
    ("too long", dict(new_item(), note_id="chat-bad-0008", fields=dict(new_item()["fields"], **{"Change Request": "x" * 30001})), "too long"),
    ("not text", dict(new_item(), note_id="chat-bad-0009", fields=dict(new_item()["fields"], Priority=2)), "must be text"),
    ("missing field", dict(new_item(), note_id="chat-bad-0010", fields={"Module": "x", "Change Request": "something new"}), "a new item needs"),
    ("priority", dict(new_item(Priority="P9"), note_id="chat-bad-0011"), "Priority 'P9'"),
    ("mode", dict(new_item(), note_id="chat-bad-0012", fields=dict(new_item()["fields"], **{"Claude Code Mode": "Whatever"})), "Claude Code Mode"),
    ("same request", dict(new_item(**{"Change Request": "  add a NOTES box "}), note_id="chat-bad-0013"), "duplicate: the same Change Request is already KW-01"),
    ("unknown id", {"note_id": "chat-bad-0014", "kind": "revision", "id": "KW-77", "based_on": {"Row Updated": "x"}, "fields": {"Lens": "x"}, "revision_summary": "s"}, "KW-77 is not on"),
    ("released", {"note_id": "chat-bad-0015", "kind": "revision", "id": "KW-03", "based_on": {"Row Updated": "2026-10-20"}, "fields": {"Lens": "x"}, "revision_summary": "s"}, "raise a new item"),
    ("wont do", {"note_id": "chat-bad-0016", "kind": "revision", "id": "KW-04", "based_on": {"Row Updated": "2026-10-20"}, "fields": {"Lens": "x"}, "revision_summary": "s"}, "raise a new item"),
    ("in progress", {"note_id": "chat-bad-0021", "kind": "revision", "id": "KW-05", "based_on": {"Row Updated": "2026-10-20"}, "fields": {"Lens": "x"}, "revision_summary": "s"}, "Not started, Deferred or Blocked"),
    ("no summary", {"note_id": "chat-bad-0022", "kind": "revision", "id": "KW-01", "based_on": {"Row Updated": "2026-10-20"}, "fields": {"Lens": "x"}}, "needs revision_summary"),
    ("no based_on", {"note_id": "chat-bad-0017", "kind": "revision", "id": "KW-01", "fields": {"Lens": "x"}, "revision_summary": "s"}, "needs based_on"),
    ("stale", {"note_id": "chat-bad-0018", "kind": "revision", "id": "KW-01", "based_on": {"Row Updated": "2026-01-01"}, "fields": {"Lens": "x"}, "revision_summary": "s"}, "stale"),
    ("no fields", {"note_id": "chat-bad-0019", "kind": "revision", "id": "KW-01", "based_on": {"Row Updated": "2026-10-20"}}, "'fields' must be"),
    ("short id", dict(new_item(), note_id="a b"), "note_id is required"),
    ("sender", dict(new_item(), note_id="chat-bad-0020", **{"from": "x" * 80}), "'from' must be"),
]


@pytest.mark.parametrize("label,obj,reason", BAD_NOTES, ids=[b[0] for b in BAD_NOTES])
def test_a_bad_note_is_rejected_with_a_reason_and_nothing_is_written(cw, env, label, obj, reason):
    if isinstance(obj, str):
        (env.inbox / "bad.json").write_text(obj, encoding="utf-8")
    else:
        obj = dict(obj)
        obj.pop("Status", None)
        if label == "status":                                                  # a top-level Status key is simply not part of a note; the same cell in 'fields' is what is refused
            obj["fields"] = dict(obj["fields"], Status="Released")
            reason = "not an intake column"
        (env.inbox / "bad.json").write_text(json.dumps(obj), encoding="utf-8")
    before = open(env.master, "rb").read()
    assert run(cw, "--apply-inbox") == 8
    assert open(env.master, "rb").read() == before and backups(env.local) == []                                  # no write, no backup
    result = json.loads((env.inbox / "rejected" / "bad.result.json").read_text(encoding="utf-8"))
    assert result["result"] == "rejected" and reason in result["reason"], result
    assert (env.inbox / "rejected" / "bad.json").exists() and not (env.inbox / "bad.json").exists()


def test_several_notes_are_one_write_and_a_bad_one_does_not_stop_the_others(cw, env, capsys):
    note(env, "n1", note_id="chat-multi-001", **new_item(**{"Change Request": "First new thing"}))
    note(env, "n2", note_id="chat-multi-002", **new_item(**{"Change Request": "Second new thing"}))
    note(env, "n3", note_id="chat-multi-003", **new_item(**{"Change Request": "Third new thing", "Priority": "P9"}))
    note(env, "n4", note_id="chat-multi-004", **new_item(**{"Change Request": "First new thing"}))                  # the same request as n1, in the same run
    assert run(cw, "--apply-inbox") == 8
    snap = cw.Snapshot(env.master)
    assert snap.values(snap.row_of("KW-06"))["Change Request"] == "First new thing" and snap.values(snap.row_of("KW-07"))["Change Request"] == "Second new thing"
    assert [i for i, _r in snap.id_rows()][-2:] == ["KW-06", "KW-07"] and '<dimension ref="A1:X8"/>' in snap.xml
    assert len(backups(env.local)) == 1
    assert sorted(os.listdir(env.inbox / "processed")) == ["n1.json", "n1.result.json", "n2.json", "n2.result.json"]
    assert sorted(f for f in os.listdir(env.inbox / "rejected") if f.endswith(".json") and not f.endswith("result.json")) == ["n3.json", "n4.json"]
    out = capsys.readouterr().out
    assert "REJECTED" in out and "duplicate: the same Change Request is already KW-06" in out


def test_notes_wait_while_excel_has_the_master_open_and_a_dry_run_moves_nothing(cw, env, capsys):
    note(env, "n1", note_id="chat-wait-001", **new_item())
    before = open(env.master, "rb").read()
    assert run(cw, "--apply-inbox", "--dry-run") == 0 and "APPLIED KW-06" in capsys.readouterr().out
    assert open(env.master, "rb").read() == before and (env.inbox / "n1.json").exists() and not (env.inbox / "processed").exists()
    lock = os.path.join(os.path.dirname(env.master), "~$G_KelpWorks_ChangeControl.xlsx")
    open(lock, "w").write("x")
    assert run(cw, "--apply-inbox") == 3 and (env.inbox / "n1.json").exists() and open(env.master, "rb").read() == before
    os.remove(lock)
    assert run(cw, "--apply-inbox") == 0 and not (env.inbox / "n1.json").exists()


def test_a_revision_that_changes_nothing_is_filed_without_a_write(cw, env, capsys):
    note(env, "n1", note_id="chat-same-001", kind="revision", id="KW-01", based_on={"Row Updated": "2026-10-20"}, fields={"Priority": "P1"})
    assert run(cw, "--apply-inbox") == 0 and "UNCHANGED KW-01" in capsys.readouterr().out
    assert backups(env.local) == [] and (env.inbox / "processed" / "n1.json").exists()
    assert row(cw, env.master, "KW-01")["Row Updated"] == "2026-10-20"


def test_an_empty_inbox_and_a_missing_inbox(cw, env, monkeypatch, capsys):
    assert run(cw, "--apply-inbox") == 0 and "empty" in capsys.readouterr().out
    monkeypatch.setenv("KELPWORKS_INBOX", str(env.tmp / "nowhere"))
    assert run(cw, "--apply-inbox") == 2 and "not found" in capsys.readouterr().out


def test_the_export_reads_the_master_with_no_argument(cw, env, monkeypatch):
    export = load("export_backlog")
    monkeypatch.setattr(export.os.path, "dirname", lambda p, _d=os.path.dirname: str(env.tmp / "tools") if p.endswith("export_backlog.py") else _d(p))
    assert export.main(["export_backlog.py"]) == 0 and (env.tmp / "docs" / "backlog" / "items.csv").exists()
    run(cw, "--apply-inbox")                                                                                    # (empty inbox) the export also reads a master with an added row
    note(env, "n1", note_id="chat-export-001", **new_item())
    assert run(cw, "--apply-inbox") == 0 and export.main(["export_backlog.py"]) == 0
    text = (env.tmp / "docs" / "backlog" / "items.csv").read_text(encoding="utf-8-sig")
    assert "KW-06" in text and "Show the run lot on the card" in text


def test_a_note_id_is_remembered_whatever_the_file_was_called(cw, env):
    note(env, "first-file-name", note_id="chat-name-0001", **new_item())
    assert run(cw, "--apply-inbox") == 0
    note(env, "a-different-file-name", note_id="chat-name-0001", **new_item(**{"Change Request": "Something else entirely"}))
    assert run(cw, "--apply-inbox") == 8
    result = json.loads((env.inbox / "rejected" / "a-different-file-name.result.json").read_text(encoding="utf-8"))
    assert "duplicate note" in result["reason"] and [i for i, _r in cw.Snapshot(env.master).id_rows()][-1] == "KW-06"


def test_publish_alone_does_not_claim_a_write_happened(cw, env, monkeypatch, capsys):
    monkeypatch.setenv("KELPWORKS_SNAPSHOT", str(env.tmp / "gone" / NAME))
    assert run(cw, "--publish") == 7
    out = capsys.readouterr().out
    assert "NOT refreshed" in out and "WRITTEN" not in out


def test_backups_made_in_the_same_second_keep_their_order_even_after_old_ones_are_pruned(cw, env, monkeypatch):
    monkeypatch.setattr(cw, "utc_stamp", lambda: "20261022-120000")                                              # a fast machine: every backup in one second
    for i in range(45):
        before = open(env.master, "rb").read()
        assert run(cw, "--id", "KW-01", "Notes=s%d" % i) == 0
        newest = backups(env.local)[-1]
        assert open(env.local / newest, "rb").read() == before                                                  # the newest sorts last, always
    names = backups(env.local)
    assert len(names) == 30 and names[-1].startswith("LOG_KelpWorks_ChangeControl_20261022-120000-45_")


# ---- KW-67: decisions and plan cells from the inbox, the revision rules, the Workflow Rules sheet -----------------------------------------------------------------------------
def decision(**over):
    fields = {"ID": "KW-02", "Decision Needed": "Rename to what?", "Options Discussed": "A / B", "Recommendation (Claude)": "Use A"}
    fields.update(over)
    return {"kind": "new_decision", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z", "fields": fields}


def result_of(env, folder, name):
    return json.loads((env.inbox / folder / (name + ".result.json")).read_text(encoding="utf-8"))


def test_a_new_decision_note_adds_a_row_to_open_decisions_and_never_sets_the_human_columns(cw, env):
    note(env, "d1", note_id="chat-dec-0001", **decision())
    note(env, "d2", note_id="chat-dec-0002", **decision(**{"Decision Needed": "Second question"}))
    assert run(cw, "--apply-inbox") == 0
    snap = cw.Snapshot(env.master)
    assert snap.cell("Open Decisions", 5, "ID") == "KW-02" and snap.cell("Open Decisions", 5, "Decision Needed") == "Rename to what?" and snap.cell("Open Decisions", 5, "Recommendation (Claude)") == "Use A"
    assert snap.cell("Open Decisions", 5, "Decided By") == "" and snap.cell("Open Decisions", 5, "Decision") == "" and snap.cell("Open Decisions", 6, "Decision Needed") == "Second question"
    dxml = snap.files[snap.paths["Open Decisions"]].decode()
    assert re.search(r'<c r="A5" s="7"', dxml) and '<row r="6"' in dxml                                            # styles copied from the row above
    assert snap.cell("Open Decisions", 3, "Decision Needed") == "Keep the old report?"                              # the other rows are untouched
    assert len(backups(env.local)) == 1 and result_of(env, "processed", "d1")["id"] == "KW-02"


BAD_DECISIONS = [
    ("unknown id", decision(ID="KW-99"), "KW-99 is not an item"),
    ("duplicate question", decision(ID="KW-05", **{"Decision Needed": "  make it OPTIONAL? "}), "duplicate"),
    ("decided by", dict(decision(), fields=dict(decision()["fields"], **{"Decided By": "N. Wrana"})), "cannot be set from a note"),
    ("decision", dict(decision(), fields=dict(decision()["fields"], Decision="Yes")), "cannot be set from a note"),
    ("missing", dict(decision(), fields={"ID": "KW-02", "Decision Needed": "x"}), "a decision needs"),
    ("not text", dict(decision(), fields=dict(decision()["fields"], **{"Options Discussed": 5})), "must be text"),
    ("no fields", {"kind": "new_decision"}, "'fields' must be"),
]


@pytest.mark.parametrize("label,obj,reason", BAD_DECISIONS, ids=[b[0] for b in BAD_DECISIONS])
def test_a_bad_decision_note_is_rejected_with_a_reason(cw, env, label, obj, reason):
    note(env, "bad", note_id="chat-baddec-%s" % label.replace(" ", ""), **obj)
    before = open(env.master, "rb").read()
    assert run(cw, "--apply-inbox") == 8
    assert open(env.master, "rb").read() == before and reason in result_of(env, "rejected", "bad")["reason"]
    if label == "duplicate question":
        note(env, "ok", note_id="chat-baddec-ok", **decision(ID="KW-05", **{"Decision Needed": "A different question"}))      # the same ID with another question is fine
        assert run(cw, "--apply-inbox") == 0


def test_an_existing_decision_question_is_a_duplicate_even_within_one_run(cw, env):
    note(env, "a", note_id="chat-dd-0001", **decision())
    note(env, "b", note_id="chat-dd-0002", **decision())
    assert run(cw, "--apply-inbox") == 8
    assert (env.inbox / "processed" / "a.json").exists() and "duplicate" in result_of(env, "rejected", "b")["reason"]


def plan(cells, **over):
    obj = {"kind": "plan_cells", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z", "sheet": "KW-60 Clean Start Plan", "cells": cells}
    obj.update(over)
    return obj


def test_a_plan_cells_note_sets_named_cells_on_a_kw_plan_sheet(cw, env):
    note(env, "p1", note_id="chat-plan-0001", **plan([{"cell": "C2", "was": "", "now": "2026-10-23 NW"}, {"cell": "d3", "was": "", "now": "a cell the row did not have"}, {"cell": "B2", "was": "N. Wrana", "now": "N. Wrana / D. Pedde"}]))
    assert run(cw, "--apply-inbox") == 0
    snap = cw.Snapshot(env.master)
    assert snap.cell_ref("KW-60 Clean Start Plan", "C2") == "2026-10-23 NW" and snap.cell_ref("KW-60 Clean Start Plan", "D3") == "a cell the row did not have" and snap.cell_ref("KW-60 Clean Start Plan", "B2") == "N. Wrana / D. Pedde"
    assert snap.cell_ref("KW-60 Clean Start Plan", "A2") == "0. Decide" and snap.cell_ref("KW-60 Clean Start Plan", "C3") == ""            # nothing else moved
    assert re.search(r'<row r="3"><c r="A3"[^>]*>.*?<c r="D3" t="inlineStr">', snap.files[snap.paths["KW-60 Clean Start Plan"]].decode(), re.S)       # the new cell is in column order
    assert [i for i, _r in snap.id_rows()] == ["KW-01", "KW-02", "KW-03", "KW-04", "KW-05"]                                     # the Change Log was not touched
    assert result_of(env, "processed", "p1")["reason"].startswith("cells set: C2, D3, B2")


BAD_PLANS = [
    ("not a plan sheet", plan([{"cell": "C2", "was": "", "now": "x"}], sheet="Change Log"), "must be a KW plan sheet"),
    ("another sheet", plan([{"cell": "C2", "was": "", "now": "x"}], sheet="Open Decisions"), "must be a KW plan sheet"),
    ("no such sheet", plan([{"cell": "C2", "was": "", "now": "x"}], sheet="KW-61 Other Plan"), "no sheet called"),
    ("stale", plan([{"cell": "B2", "was": "somebody else", "now": "x"}]), "stale"),
    ("bad cell", plan([{"cell": "C2:D3", "was": "", "now": "x"}]), "not a single cell"),
    ("twice", plan([{"cell": "C2", "was": "", "now": "x"}, {"cell": "c2", "was": "", "now": "y"}]), "named twice"),
    ("missing row", plan([{"cell": "C40", "was": "", "now": "x"}]), "has no row 40"),
    ("no was", plan([{"cell": "C2", "now": "x"}]), "needs cell, was and now"),
    ("not text", plan([{"cell": "C2", "was": "", "now": 5}]), "must be text"),
    ("too many", plan([{"cell": "C2", "was": "", "now": "x"}] * 51), "1-50"),
    ("control", plan([{"cell": "C2", "was": "", "now": "a\x07"}]), "control characters"),
]


@pytest.mark.parametrize("label,obj,reason", BAD_PLANS, ids=[b[0] for b in BAD_PLANS])
def test_a_bad_plan_cells_note_is_rejected_whole_and_nothing_is_written(cw, env, label, obj, reason):
    note(env, "bad", note_id="chat-badplan-%s" % label.replace(" ", ""), **obj)
    before = open(env.master, "rb").read()
    assert run(cw, "--apply-inbox") == 8
    assert open(env.master, "rb").read() == before and backups(env.local) == [] and reason in result_of(env, "rejected", "bad")["reason"]


def test_a_plan_note_that_changes_nothing_is_filed_without_a_write(cw, env, capsys):
    note(env, "p1", note_id="chat-plan-same", **plan([{"cell": "B2", "was": "N. Wrana", "now": "N. Wrana"}]))
    assert run(cw, "--apply-inbox") == 0 and "UNCHANGED" in capsys.readouterr().out and backups(env.local) == []


def test_a_revision_follows_the_intake_rules_status_summary_and_reapproval(cw, env):
    items = [dict(i) for i in ITEMS] + [{"ID": "KW-06", "Change Request": "Deferred thing", "Priority": "P2", "Claude Code Mode": "Direct", "Status": "Deferred", "Approved to Start": "", "Row Updated": "2026-10-20", "Updated By": "x"},
                                        {"ID": "KW-07", "Change Request": "Blocked thing", "Priority": "P2", "Claude Code Mode": "Direct", "Status": "Blocked", "Approved to Start": "", "Row Updated": "2026-10-20", "Updated By": "x"}]
    make(env.master, items)
    for i, kw in enumerate(("KW-06", "KW-07")):                                                                     # Deferred and Blocked may be revised
        note(env, "r%d" % i, note_id="chat-revok-000%d" % i, kind="revision", id=kw, based_on={"Row Updated": "2026-10-20"}, fields={"Lens": "new lens"}, revision_summary="lens added")
    note(env, "r9", note_id="chat-revok-0009", kind="revision", id="KW-01", based_on={"Row Updated": "2026-10-20"}, fields={"Acceptance Criteria": "A new scope", "Context / Rationale": "Why"},
         revision_summary="scope changed")
    assert run(cw, "--apply-inbox") == 0
    assert row(cw, env.master, "KW-06")["Lens"] == "new lens" and row(cw, env.master, "KW-07")["Context / Rationale"].endswith("(Claude chat): lens added")
    kw1 = row(cw, env.master, "KW-01")                                                                             # KW-01 is Approved to Start = Y: the summary is appended after the new text, and the result warns
    assert kw1["Context / Rationale"].startswith("Why\nRevised ") and kw1["Context / Rationale"].endswith("(Claude chat): scope changed")
    warn = result_of(env, "processed", "r9")["reason"]
    assert "WARNING" in warn and "Approved to Start is Y" in warn and "approved again" in warn
    assert "WARNING" not in result_of(env, "processed", "r0")["reason"]


# ---- the Workflow Rules sheet --------------------------------------------------------------------------------------------------------------------------------------------------
def rules_json(tmp, **over):
    spec = {"title": "New title", "subtitle": "New subtitle", "sections": [
        {"heading": "1. First", "columns": ["A", "B", "C"], "rows": [["x", "y", "z"], ["a long rule " * 20, "b", "c"]]},
        {"heading": "2. Notes", "text": "One paragraph\nwith two lines."}]}
    spec.update(over)
    p = tmp / "rules.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return str(p)


def test_the_rules_sheet_is_rewritten_from_a_file_with_the_sheets_own_styles_and_nothing_else_changes(cw, env, capsys):
    before = cw.Snapshot(env.master)
    assert run(cw, "--rules-sheet", "--rules-file", rules_json(env.tmp)) == 0
    snap = cw.Snapshot(env.master)
    xml = snap.files[snap.paths["Workflow Rules"]].decode()
    texts = [snap.cell_ref("Workflow Rules", r) for r in ("A1", "A2", "A4", "A5", "B5", "A6", "C6", "A7", "C7", "A9", "A10")]
    assert texts[:7] == ["New title", "New subtitle", "1. First", "A", "B", "x", "z"] and texts[7].startswith("a long rule") and texts[9] == "2. Notes" and texts[10] == "One paragraph\nwith two lines."
    assert re.search(r'<c r="A1" s="1"', xml) and re.search(r'<c r="A2" s="2"', xml) and re.search(r'<c r="A4" s="9"', xml) and re.search(r'<c r="A5" s="10"', xml) and re.search(r'<c r="B6" s="11"', xml)
    assert '<dimension ref="A1:C10"/>' in xml and '<mergeCells count="1"><mergeCell ref="A10:C10"/></mergeCells>' in xml and "Old pasted box" not in xml and "<cols>" in xml and "<pageMargins" in xml
    assert float(re.search(r'<row r="7"[^>]* ht="([\d.]+)"', xml).group(1)) > 30                                    # the long rule row is taller
    for name in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml", "xl/worksheets/sheet5.xml", "customXml/item1.xml"):
        assert snap.files[name] == before.files[name], name                                                         # every other part is byte for byte
    assert len(backups(env.local)) == 1 and open(env.snapshot, "rb").read() == open(env.master, "rb").read()
    capsys.readouterr()
    assert run(cw, "--rules-sheet", "--rules-file", rules_json(env.tmp)) == 0 and "Nothing to change" in capsys.readouterr().out and len(backups(env.local)) == 1


def test_a_bad_rules_file_or_a_locked_workbook_is_refused(cw, env, capsys):
    original = open(env.master, "rb").read()
    bad = env.tmp / "bad.json"
    for body in ("{not json", json.dumps({"title": "t"}), json.dumps({"title": "t", "subtitle": "s", "sections": [{"heading": "h", "columns": ["a", "b"], "rows": []}]}),
                 json.dumps({"title": "t", "subtitle": "s", "sections": [{"heading": "h", "columns": ["a", "b", "c"], "rows": [["1", "2"]]}]}),
                 json.dumps({"title": "t", "subtitle": "s", "sections": [{"heading": "h\x07", "text": "x"}]})):
        bad.write_text(body, encoding="utf-8")
        assert run(cw, "--rules-sheet", "--rules-file", str(bad)) == 2 and "Nothing was written" in capsys.readouterr().out
        assert open(env.master, "rb").read() == original
    assert run(cw, "--rules-sheet", "--rules-file", str(env.tmp / "missing.json")) == 2
    lock = os.path.join(os.path.dirname(env.master), "~$G_KelpWorks_ChangeControl.xlsx")
    open(lock, "w").write("x")
    assert run(cw, "--rules-sheet", "--rules-file", rules_json(env.tmp)) == 3 and open(env.master, "rb").read() == original


def test_the_rules_the_repo_keeps_are_valid_and_describe_the_released_process(cw, env):
    path = os.path.join(ROOT, "docs", "backlog", "workflow-rules.json")
    spec = cw.load_rules(path)
    text = json.dumps(spec)
    for needle in ("C:\\\\KelpWorks\\\\ChangeControl", "--publish", "--restore", "Inbox", "Revised <date> (Claude chat)", "Not started, Deferred or Blocked", "new_decision", "plan_cells", "Approved to Start", "READ-ONLY"):
        assert needle in text, needle
    assert "Claude (chat) writes the workbook" not in text and "only this chat allocates" not in text and "60 s" not in text
    assert run(cw, "--rules-sheet", "--rules-file", path) == 0
    snap = cw.Snapshot(env.master)
    assert snap.cell_ref("Workflow Rules", "A1") == "Change Log Workflow Rules" and "C:\\KelpWorks\\ChangeControl" in snap.cell_ref("Workflow Rules", "B6")
