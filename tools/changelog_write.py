#!/usr/bin/env python3
"""The ONLY writer of the KelpWorks change log (KW-61, KW-66). Standard library only.

The MASTER is C:\\KelpWorks\\ChangeControl\\LOG_KelpWorks_ChangeControl.xlsx (outside OneDrive). After every write the tool publishes a read-only copy to SharePoint
(QAQC > KelpWorks ERP > Change_Control); Claude (chat) and everyone else read that copy. Paths and names: tools/changelog_path.py.

    python tools/changelog_write.py --id KW-46 Status=Released "PR #=75" "Released Date=2026-10-21" Notes="what changed" [--dry-run]
    python tools/changelog_write.py --batch updates.json          # [{"id": "KW-46", "set": {"Status": "Released", "PR #": "75"}}, ...]  (one backup, one check)
    python tools/changelog_write.py --decision KW-05 --by "N. Wrana (2026-10-22)" --text "Make optional" [--needed "part of the question"]   # an Open Decisions answer
    python tools/changelog_write.py --apply-inbox [--dry-run]     # apply the notes Claude (chat) left in Change_Control\\Inbox (new items and revisions)
    python tools/changelog_write.py --show KW-46                  # read-only: the row's Status, Approved to Start, PR #, ...
    python tools/changelog_write.py --rules-sheet [--rules-file f.json]   # rewrite the Workflow Rules sheet from docs/backlog/workflow-rules.json
    python tools/changelog_write.py --publish                     # copy the master to SharePoint now (after a hand edit in Excel)
    python tools/changelog_write.py --backups                     # list the backups (local + SharePoint mirror)
    python tools/changelog_write.py --restore latest|<backup name># put a backup back as the master (backs the current one up first)
    python tools/changelog_write.py --migrate [--file <old workbook>]   # one time: copy the SharePoint workbook to the master folder

What it enforces (the 'Workflow Rules' sheet of the workbook):

* Ownership: item updates may write only Status, Notes, PR #, Released Date and QM-001 Ref (Row Updated and Updated By are stamped automatically). Every other column belongs to the
  intake chat or to a human, and is refused. The intake columns change, and rows are added, ONLY from a valid inbox note.
* Status: Not started, In progress, On staging, Released or Blocked. Deferred and Won't do are a human's decision and are refused; so is moving an item OUT of them.
  In progress / On staging / Released need Approved to Start = Y or Pre-rule. Released also needs a PR # and a Released Date (given now or already in the row).
* Only the master is written (the right file name in a folder called ChangeControl, never inside OneDrive, never the SharePoint copy). Reading (--show, --dry-run) may use any copy.
* Safe write: it stops if Excel has the master open (a ~$ lock file) or another run is writing (.changelog_write.lock), reads the file fresh and records its modified time and checksum,
  finds rows by ID (never by row number), re-checks both just before saving, takes a BACKUP (newest 30 kept, second copy in SharePoint Backups), saves atomically changing only the
  named cells, waits (10 s, --verify-wait) and re-opens the file to confirm every value stuck, then refreshes the SharePoint copy. A revert is reported, never retried.

Exit codes: 0 done, 1 usage, 2 refused by a rule (nothing written), 3 the master is open in Excel / locked / another run is writing, 4 the file changed while this ran (nothing written),
5 a value did not stick (revert), 6 the workbook could not be read, 7 written but the SharePoint copy was NOT refreshed (run --publish), 8 some inbox notes were rejected (the valid
ones were applied), 9 no backup could be made (nothing written).
"""
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_backlog_xlsx as xl                                                  # the XML helpers (sheet lookup, cells, escaping)
import changelog_path as cp                                                       # the ONE place that says where the change log is (KW-62, KW-66)

DEFAULT_PATH = cp.DEFAULT_PATH
SHEET = "Change Log"
DECISIONS = "Open Decisions"
RULES = "Workflow Rules"
RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "backlog", "workflow-rules.json")
OWNED = ["Status", "Notes", "PR #", "Released Date", "QM-001 Ref"]
STAMPS = {"Row Updated": lambda: datetime.date.today().isoformat(), "Updated By": lambda: "Claude Code"}
STATUSES = ["Not started", "In progress", "On staging", "Released", "Blocked"]
HUMAN_STATUSES = ["Deferred", "Won't do"]
NEEDS_APPROVAL = ("In progress", "On staging", "Released")
APPROVED = ("y", "pre-rule")
HUMAN_COLUMNS = {"Owner", "Approved to Start", "Verified By (on live)", "Verified Date"}
INTAKE_COLUMNS = {"ID", "Module", "Section", "Type", "Change Request", "Context / Rationale", "Transcript Time", "Raised By", "Priority", "Lens", "Claude Code Mode",
                  "Claude Code Prompt", "Acceptance Criteria"}
NEW_ITEM_REQUIRED = ["Module", "Section", "Type", "Change Request", "Raised By", "Priority", "Claude Code Mode", "Acceptance Criteria"]
PR_RE = re.compile(r"^\d+(\s*(,|&|and|/)\s*\d+)*$")
BACKUP_RE = re.compile(r"^LOG_KelpWorks_ChangeControl_(\d{8}-\d{6}-\d{2})_([A-Za-z0-9-]+)\.xlsx$")
NOTE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{6,80}$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
VERIFY_WAIT = 10.0
LOCK_STALE_SECONDS = 1800
MAX_NOTE_BYTES = 1000000

NOTE_FORMAT = r"""KelpWorks change log - how to leave a note here (Claude chat)

Write ONE file per note into this folder, named <note_id>.json (UTF-8). Do not edit the workbook. Claude Code applies the notes (tools/changelog_write.py --apply-inbox);
applied notes move to processed\, rejected ones to rejected\, each with <note_id>.result.json saying what happened or why not. The workbook in Change_Control is a read-only copy.
note_id must be unique for ever (letters, digits, dot, dash, underscore; 6-80 characters). Four kinds of note:

1. NEW ITEM
{"note_id": "chat-20261022-001", "kind": "new_item", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z",
 "fields": {"Module": "...", "Section": "...", "Type": "...", "Change Request": "...", "Context / Rationale": "...", "Transcript Time": "...", "Raised By": "...",
            "Priority": "P2", "Lens": "...", "Claude Code Mode": "Direct", "Acceptance Criteria": "..."}}
The writer gives it the next free KW number (Status Not started, Approved to Start blank: a human approves it). Required: Module, Section, Type, Change Request, Raised By,
Priority, Claude Code Mode, Acceptance Criteria. Priority and Claude Code Mode must be values already used in the workbook.

2. REVISION of an item's intake columns (A-M)
{"note_id": "chat-20261022-002", "kind": "revision", "from": "Claude (chat)", "created": "...", "id": "KW-66",
 "based_on": {"Row Updated": "2026-10-22"}, "revision_summary": "Acceptance criteria now include the restore test",
 "fields": {"Acceptance Criteria": "..."}}
Allowed only while the item is Not started, Deferred or Blocked (never In progress or On staging; a Released or Won't do item needs a new item). based_on is the item's Row Updated
value you saw in the copy: if the item changed since, the note is rejected as stale. The writer appends "Revised <date> (Claude chat): <revision_summary>" to Context / Rationale.
If Approved to Start is Y and the scope changed, the result says so: the human must clear it so the item is approved again.

3. NEW DECISION (a row on the Open Decisions sheet)
{"note_id": "chat-20261022-003", "kind": "new_decision", "from": "Claude (chat)", "created": "...",
 "fields": {"ID": "KW-67", "Decision Needed": "...", "Options Discussed": "A / B", "Recommendation (Claude)": "..."}}
The ID must be an item on the Change Log. Decided By and Decision are a human's: a note cannot set them. The same ID with the same Decision Needed is a duplicate.

4. PLAN CELLS (named cells on a KW plan sheet, for example "KW-60 Clean Start Plan")
{"note_id": "chat-20261022-004", "kind": "plan_cells", "from": "Claude (chat)", "created": "...", "sheet": "KW-60 Clean Start Plan",
 "cells": [{"cell": "C37", "was": "", "now": "2026-10-23 NW"}]}
The sheet name must be KW-<number> <words> Plan. "was" is the text the cell holds in the copy you read; if it differs now, the note is rejected as stale. At most 50 cells per note,
only cells in rows that exist.

Only the intake columns Module, Section, Type, Change Request, Context / Rationale, Transcript Time, Raised By, Priority, Lens, Claude Code Mode, Claude Code Prompt and
Acceptance Criteria can be set on an item. Status, Notes, Approved to Start, PR #, Owner and every other column are refused. A note with the same note_id as an applied one, or a
new item whose Change Request is already on the sheet, is rejected as a duplicate.
"""


class Refused(Exception):
    """A rule of the workflow stops this update (exit 2)."""


class Stale(Exception):
    pass


class Locked(Exception):
    """Another run is writing (exit 3)."""


class Reject(Exception):
    """One inbox note is not acceptable; the message is the reason."""


# ---- reading ---------------------------------------------------------------------------------------------------------------------------------------------------------------
class Snapshot:
    def __init__(self, path):
        self.path = path
        st = os.stat(path)
        self.mtime_ns, self.size = st.st_mtime_ns, st.st_size
        with open(path, "rb") as f:
            raw = f.read()
        self.sha = hashlib.sha256(raw).hexdigest()
        try:
            with zipfile.ZipFile(path) as z:
                self.infos = z.infolist()
                self.files = {i.filename: z.read(i.filename) for i in self.infos}
        except zipfile.BadZipFile as e:
            raise ValueError("%s is not a readable .xlsx file (%s)" % (path, e))
        self.paths = xl.sheet_paths(self.files)
        if SHEET not in self.paths:
            raise ValueError("the workbook has no sheet called '%s'" % SHEET)
        self.part = self.paths[SHEET]
        self.sst = xl.shared_strings(self.files)
        self.xml = self.files[self.part].decode("utf-8")
        self.headers = xl.header_columns(self.xml, self.sst)               # {header: column letters}
        self.by_lower = {h.strip().lower(): h for h in self.headers if h}
        if "ID" not in self.headers:
            raise ValueError("'%s' has no ID column in row 1" % SHEET)

    def unchanged(self):
        try:
            st = os.stat(self.path)
            if st.st_mtime_ns != self.mtime_ns or st.st_size != self.size:
                return False
            with open(self.path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest() == self.sha
        except OSError:
            return False

    def id_rows(self, xml=None):
        """[(ID, row)] of every item on the Change Log sheet, in sheet order."""
        id_col = self.headers["ID"]
        out = []
        for ref, c in xl.cells_of(xml or self.xml):
            if re.fullmatch(id_col + r"\d+", ref) and ref != id_col + "1":
                text = xl.cell_text(c, self.sst).strip()
                if text:
                    out.append((text, int(ref[len(id_col):])))
        return out

    def row_of(self, kw, xml=None):
        """The sheet row of an ID (by looking the ID up, never by position). Refuses an unknown or a repeated ID."""
        hits = [row for i, row in self.id_rows(xml) if i == kw]
        if not hits:
            raise Refused("%s is not on the '%s' sheet (rows are added only from an inbox note)" % (kw, SHEET))
        if len(hits) > 1:
            raise Refused("%s appears %d times on the '%s' sheet: fix the duplicate before updating it" % (kw, len(hits), SHEET))
        return hits[0]

    def values(self, row, xml=None):
        """{header: text} of one row."""
        xml = xml or self.xml
        out = {}
        letter_to_header = {v: k for k, v in self.headers.items()}
        for ref, c in xl.cells_of(xml):
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
            if m and int(m.group(2)) == row and m.group(1) in letter_to_header:
                out[letter_to_header[m.group(1)]] = xl.cell_text(c, self.sst).strip()
        return out

    def cell_ref(self, sheet, ref):
        """One cell of any sheet by its reference (C37)."""
        xml = self.files[self.paths[sheet]].decode("utf-8")
        for r, c in xl.cells_of(xml):
            if r == ref:
                return xl.cell_text(c, self.sst).strip()
        return ""

    def cell(self, sheet, row, header):
        """One cell of any sheet, by row number and header text."""
        xml = self.files[self.paths[sheet]].decode("utf-8")
        col = xl.header_columns(xml, self.sst).get(header)
        for ref, c in xl.cells_of(xml):
            if col and ref == "%s%d" % (col, row):
                return xl.cell_text(c, self.sst).strip()
        return ""


def lock_files(path):
    """Excel (and LibreOffice) leave a lock file beside a workbook that is open."""
    folder, name = os.path.split(os.path.abspath(path))
    found = []
    for f in os.listdir(folder or "."):
        low = f.lower()
        if low.startswith("~$"):
            rest = f[2:]
            if name.lower().endswith(rest.lower()) or "kelpworks_changecontrol" in rest.lower():          # (Excel may shorten the name: ~$G_Kelp...; any KelpWorks_ChangeControl lock stops us)
                found.append(f)
        elif low.startswith(".~lock.") and name.lower() in low:
            found.append(f)
    return found


class WriteLock:
    """One writer at a time: .changelog_write.lock in the master folder (created exclusively, a stale one older than 30 minutes is replaced)."""

    def __init__(self, folder):
        self.path = os.path.join(folder, ".changelog_write.lock")

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > LOCK_STALE_SECONDS:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                raise Locked("another change-log write is running (%s). Wait for it, or delete that file if you know nothing is running." % self.path)
            os.write(fd, ("pid %d, %s\n" % (os.getpid(), datetime.datetime.now().isoformat())).encode("ascii"))
            os.close(fd)
            return self
        raise Locked("could not take the write lock %s" % self.path)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


# ---- the rules -------------------------------------------------------------------------------------------------------------------------------------------------------------
def canonical(snap, column):
    h = snap.by_lower.get(column.strip().lower())
    if not h:
        raise Refused("the workbook has no '%s' column (columns: %s)" % (column, ", ".join(sorted(snap.headers))))
    return h


def check_text(column, value):
    value = "" if value is None else str(value).strip()
    if CTRL_RE.search(value):
        raise Refused("%s contains control characters" % column)
    if len(value) > 30000:
        raise Refused("%s is too long (limit 30,000 characters)" % column)
    return value


def check_value(column, value):
    value = "" if value is None else str(value).strip()
    if column == "Status":
        if value in HUMAN_STATUSES:
            raise Refused("Status '%s' is set by a human; Claude Code records the reason in Notes" % value)
        if value not in STATUSES:
            raise Refused("Status must be one of: %s" % ", ".join(STATUSES))
    elif column == "Released Date":
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise Refused("Released Date must be a date written YYYY-MM-DD (got '%s')" % value)
    elif column == "PR #":
        value = value.lstrip("#").strip()
        if not PR_RE.match(value):
            raise Refused("PR # must be the pull request number(s), for example 75 or 70, 71 (got '%s')" % value)
    else:
        value = check_text(column, value)
    return value


def plan_update(snap, xml, kw, setting):
    """Validate one item's update against the row as it is now and return (new xml, [(header, old, new)], expected {(sheet, id, header): text}). Raises Refused."""
    if not setting:
        raise Refused("%s: nothing to write (give column=value pairs)" % kw)
    row = snap.row_of(kw, xml)
    current = snap.values(row, xml)
    new = {}
    for column, value in setting.items():
        h = canonical(snap, column)
        if h in STAMPS:
            raise Refused("%s is stamped automatically; do not set it" % h)
        if h not in OWNED:
            who = "a human" if h in HUMAN_COLUMNS else ("the intake chat, through an inbox note" if h in INTAKE_COLUMNS else "someone else")
            raise Refused("'%s' belongs to %s: Claude Code may write only %s" % (h, who, ", ".join(OWNED)))
        new[h] = check_value(h, value)
    status = new.get("Status")
    if status is not None:
        was = current.get("Status", "")
        if was in HUMAN_STATUSES and status != was:
            raise Refused("%s is '%s': a human decided that, and only a human changes it back" % (kw, was))
        if status in NEEDS_APPROVAL:
            if "Approved to Start" not in snap.headers:
                raise Refused("the workbook has no 'Approved to Start' column, so %s cannot be started" % kw)
            if current.get("Approved to Start", "").strip().lower() not in APPROVED:
                raise Refused("%s cannot be set to '%s': Approved to Start is '%s' (it must be Y or Pre-rule)" % (kw, status, current.get("Approved to Start", "") or "blank"))
        if status == "Released":
            for need in ("PR #", "Released Date"):
                if not (new.get(need) or current.get(need, "")):
                    raise Refused("%s cannot be Released without a %s (give it in the same call, or fill it in first)" % (kw, need))
    if all(current.get(h, "") == text for h, text in new.items()):
        return xml, [], {}                                               # already as asked: nothing is written, not even the stamps
    for stamp, make in STAMPS.items():
        if stamp not in snap.headers:
            raise Refused("the workbook has no '%s' column to stamp" % stamp)
        new[stamp] = make()
    changes = []
    for h, text in new.items():
        old = current.get(h, "")
        xml = set_cell(xml, snap.headers[h], row, text)
        if old != text:
            changes.append((h, old, text))
    return xml, changes, {(SHEET, kw, h): text for h, text in new.items()}


def colnum(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def set_cell(xml, col, row, text):
    """Write one text cell, keeping the cell's style. A missing cell is added to its row in column order."""
    ref = "%s%d" % (col, row)
    pat = re.compile(r'<c r="%s"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:/>|>.*?</c>)' % ref, flags=re.S)
    m = pat.search(xml)
    cell = lambda attrs: ('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, attrs, xl.escape(text))) if text != "" else '<c r="%s"%s/>' % (ref, attrs)
    if m:
        return xml[:m.start()] + cell(re.sub(r'\s+t="[^"]*"', "", m.group(1))) + xml[m.end():]
    rm = re.search(r'<row r="%d"(?:\s+[^>]*?)?>(.*?)</row>' % row, xml, flags=re.S)
    if not rm:
        raise Refused("row %d is not in the sheet" % row)
    body, pos = rm.group(1), None
    for cm in re.finditer(r'<c r="([A-Z]+)\d+"', body):
        if colnum(cm.group(1)) > colnum(col):
            pos = cm.start()
            break
    new_body = (body[:pos] + cell("") + body[pos:]) if pos is not None else body + cell("")
    return xml[:rm.start(1)] + new_body + xml[rm.end(1):]


# ---- Open Decisions ---------------------------------------------------------------------------------------------------------------------------------------------------------
def plan_decision(snap, kw, by, text, needed=None):
    """Record who decided and what, on the Open Decisions sheet. -> (part, new xml, [(header, old, new)], expected)."""
    if DECISIONS not in snap.paths:
        raise Refused("the workbook has no sheet called '%s'" % DECISIONS)
    by, text = check_text("Decided By", by), check_text("Decision", text)
    if not by or not text:
        raise Refused("a decision needs --by (who decided) and --text (what was decided)")
    part = snap.paths[DECISIONS]
    xml = snap.files[part].decode("utf-8")
    headers = xl.header_columns(xml, snap.sst)
    for need in ("ID", "Decided By", "Decision"):
        if need not in headers:
            raise Refused("'%s' has no '%s' column in row 1" % (DECISIONS, need))
    id_col, needed_col = headers["ID"], headers.get("Decision Needed")
    rows = [int(ref[len(id_col):]) for ref, c in xl.cells_of(xml) if re.fullmatch(id_col + r"\d+", ref) and ref != id_col + "1" and xl.cell_text(c, snap.sst).strip() == kw]
    def question(r):
        for ref, c in xl.cells_of(xml):
            if needed_col and ref == "%s%d" % (needed_col, r):
                return xl.cell_text(c, snap.sst).strip()
        return ""
    if needed:
        rows = [r for r in rows if needed.lower() in question(r).lower()]
    if not rows:
        raise Refused("%s has no row on the '%s' sheet%s" % (kw, DECISIONS, " matching '%s'" % needed if needed else ""))
    if len(rows) > 1:
        raise Refused("%s has %d decision rows; say which with --needed \"part of the question\": %s" % (kw, len(rows), " | ".join(question(r)[:60] for r in rows)))
    row, changes, expected = rows[0], [], {}
    for header, new in (("Decided By", by), ("Decision", text)):
        old = snap.cell(DECISIONS, row, header)
        if old != new:
            xml = xl.set_text(xml, "%s%d" % (headers[header], row), new)
            changes.append((header, old, new))
        expected[(DECISIONS, row, header)] = new
    return part, xml, [("%s %s" % (kw, h), o, n) for h, o, n in changes], expected


# ---- adding a row (inbox: a new item, a new decision) --------------------------------------------------------------------------------------------------------------------
def last_row_number(xml):
    nums = [int(n) for n in re.findall(r'<row r="(\d+)"', xml)]
    return max(nums) if nums else 1


def column_widths(xml):
    out = {}
    for m in re.finditer(r'<col\b[^>]*?\bmin="(\d+)"[^>]*?\bmax="(\d+)"[^>]*?\bwidth="([\d.]+)"', xml):
        for i in range(int(m.group(1)), int(m.group(2)) + 1):
            out[i] = float(m.group(3))
    return out


def text_lines(text, width):
    per_line = max(8.0, width * 1.1)
    return sum(max(1, math.ceil(len(p) / per_line)) for p in str(text).split("\n"))


def row_height(lines, minimum=15.0):
    return ("%.1f" % min(409.5, max(minimum, 13.5 * lines + 3))).rstrip("0").rstrip(".")


def add_row(xml, headers, values, what=SHEET):
    """Append one row below the last one of a sheet (cell styles copied from the last row). headers = {header: column letters}. -> (new xml, row number)."""
    last = last_row_number(xml)
    lm = re.search(r'<row r="%d"((?:\s+[^>]*?)?)>(.*?)</row>' % last, xml, flags=re.S)
    if not lm:
        raise Refused("the '%s' sheet has no row to copy the layout from" % what)
    styles = {m.group(1): m.group(2) for m in re.finditer(r'<c r="([A-Z]+)\d+"(?:[^>]*?\bs="(\d+)")?', lm.group(2))}
    attrs = re.sub(r'\s+(?:ht|customHeight)="[^"]*"', "", lm.group(1))
    widths, lines = column_widths(xml), 1
    row = last + 1
    cells = ""
    for header, letters in sorted(headers.items(), key=lambda kv: colnum(kv[1])):
        text = values.get(header, "")
        style = styles.get(letters)
        s_attr = ' s="%s"' % style if style else ""
        if text:
            cells += '<c r="%s%d"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (letters, row, s_attr, xl.escape(text))
            lines = max(lines, text_lines(text, widths.get(colnum(letters), 12.0)))
        else:
            cells += '<c r="%s%d"%s/>' % (letters, row, s_attr)
    new_row = '<row r="%d"%s ht="%s" customHeight="1">%s</row>' % (row, attrs, row_height(lines, 30.0), cells)
    return xml.replace("</sheetData>", new_row + "</sheetData>", 1), row


def extend_ranges(xml, wb_xml, old_last, new_last, sheet=SHEET):
    """Make every range that ended at the old last row (autofilter, data validation, conditional formatting, the filter name, the sheet dimension) end at the new one."""
    def bump_ref(m):
        return m.group(1) + (str(new_last) if int(m.group(2)) == old_last else m.group(2))
    xml = re.sub(r'(<dimension ref="[A-Z]+\d+:[A-Z]+)(\d+)', bump_ref, xml)
    xml = re.sub(r'(<autoFilter\b[^>]*?\bref="[A-Z]+\d+:[A-Z]+)(\d+)', bump_ref, xml)
    def bump_sqref(m):
        return m.group(1) + re.sub(r"([A-Z]+\d+:[A-Z]+)(\d+)", bump_ref, m.group(2)) + '"'
    xml = re.sub(r'(<(?:dataValidation|conditionalFormatting)\b[^>]*?\bsqref=")([^"]*)"', bump_sqref, xml)
    wb_xml = re.sub(r"(<definedName\b[^>]*>[^<]*'" + re.escape(sheet) + r"'![$A-Z]+\$\d+:\$[A-Z]+\$)(\d+)", bump_ref, wb_xml)
    return xml, wb_xml


# ---- the Workflow Rules sheet (written from docs/backlog/workflow-rules.json) -----------------------------------------------------------------------------------------------
def sheet_sig(xml, sst):
    texts = ["%s:%s" % (ref, xl.cell_text(c, sst).strip()) for ref, c in xl.cells_of(xml) if xl.cell_text(c, sst).strip()]
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()


def load_rules(path):
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("the rules file %s could not be read (%s)" % (path, e))
    def need(cond, msg):
        if not cond:
            raise Refused("the rules file %s: %s" % (path, msg))
    need(isinstance(spec, dict) and isinstance(spec.get("title"), str) and isinstance(spec.get("subtitle"), str) and isinstance(spec.get("sections"), list) and spec["sections"], "needs title, subtitle and sections")
    for s in spec["sections"]:
        need(isinstance(s, dict) and isinstance(s.get("heading"), str), "every section needs a heading")
        if "text" in s:
            need(isinstance(s["text"], str), "'text' must be text")
        else:
            need(isinstance(s.get("columns"), list) and len(s["columns"]) == 3 and all(isinstance(c, str) for c in s["columns"]), "'%s' needs 3 column names" % s["heading"])
            need(isinstance(s.get("rows"), list) and all(isinstance(r, list) and len(r) == 3 and all(isinstance(c, str) for c in r) for r in s["rows"]), "'%s' rows must each have 3 texts" % s["heading"])
    texts = [spec["title"], spec["subtitle"]] + [x for s in spec["sections"] for x in ([s["heading"], s.get("text", "")] + list(s.get("columns", [])) + [c for r in s.get("rows", []) for c in r])]
    for t in texts:
        check_text("rules text", t)
    return spec


def build_rules_sheet(snap, spec):
    """The new XML of the Workflow Rules sheet: the existing cell styles are reused by role (title, subtitle, heading, table header, table body)."""
    if RULES not in snap.paths:
        raise Refused("the workbook has no sheet called '%s'" % RULES)
    xml = snap.files[snap.paths[RULES]].decode("utf-8")
    roles, three = {}, []
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', xml, flags=re.S):
        cells = re.findall(r'<c r="([A-Z]+)\d+"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:/>|>)', m.group(2))
        style = lambda attrs: (re.search(r'\bs="(\d+)"', attrs) or [None, None])[1]
        if len(cells) == 1:
            roles.setdefault("title" if "title" not in roles else ("subtitle" if "subtitle" not in roles else "heading"), style(cells[0][1]))
        elif len(cells) == 3:
            three.append(style(cells[0][1]))
    if len(three) < 2 or len(roles) < 3:
        raise Refused("the '%s' sheet does not have the title / subtitle / heading / table rows this tool copies the styles from" % RULES)
    roles["header"], roles["body"] = three[0], three[1]
    widths = column_widths(xml)
    w = [widths.get(i, 20.0) for i in (1, 2, 3)]
    rows, merges, r = [], [], 0
    def cell(col, row, text, role):
        s = ' s="%s"' % roles[role] if roles.get(role) else ""
        return '<c r="%s%d"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (col, row, s, xl.escape(text)) if text else '<c r="%s%d"%s/>' % (col, row, s)
    def add(cells_text, role, merged=False):
        nonlocal r
        r += 1
        if merged:
            lines = text_lines(cells_text[0], sum(w))
            body = cell("A", r, cells_text[0], role) + cell("B", r, "", role) + cell("C", r, "", role)
            merges.append("A%d:C%d" % (r, r))
        else:
            lines = max(text_lines(t, w[i]) for i, t in enumerate(cells_text))
            body = "".join(cell("ABC"[i], r, t, role) for i, t in enumerate(cells_text))
        height = {"title": "17.25", "subtitle": "15"}.get(role) or row_height(lines)          # the title and the one-line subtitle keep the sheet's own heights (the subtitle runs across)
        rows.append('<row r="%d" spans="1:3" ht="%s" customHeight="1" x14ac:dyDescent="0.25">%s</row>' % (r, height, body))
    add([spec["title"]], "title")
    add([spec["subtitle"]], "subtitle")
    for s in spec["sections"]:
        r += 1                                                         # one blank row before every section
        add([s["heading"]], "heading")
        if "text" in s:
            add([s["text"]], "body", merged=True)
        else:
            add(s["columns"], "header")
            for row in s["rows"]:
                add(row, "body")
    sheet = re.sub(r"<sheetData>.*?</sheetData>", lambda _m: "<sheetData>" + "".join(rows) + "</sheetData>", xml, flags=re.S)
    sheet = re.sub(r'<dimension ref="[^"]*"/>', '<dimension ref="A1:C%d"/>' % r, sheet)
    sheet = re.sub(r"<mergeCells\b.*?</mergeCells>", "", sheet, flags=re.S)
    if merges:
        sheet = sheet.replace("</sheetData>", '</sheetData><mergeCells count="%d">%s</mergeCells>' % (len(merges), "".join('<mergeCell ref="%s"/>' % m for m in merges)), 1)
    return sheet, r


# ---- the inbox (notes left by Claude chat) ------------------------------------------------------------------------------------------------------------------------------------
REVISABLE = ("", "Not started", "Deferred", "Blocked")                 # the intake rule of 2026-10-22: never while In progress / On staging; a Released item gets a new row
RESCOPE_COLUMNS = {"Module", "Section", "Type", "Change Request", "Acceptance Criteria", "Priority", "Claude Code Mode"}
DECISION_FIELDS = {"id": "ID", "decision needed": "Decision Needed", "options discussed": "Options Discussed", "recommendation (claude)": "Recommendation (Claude)", "recommendation": "Recommendation (Claude)"}
PLAN_SHEET_RE = re.compile(r"^KW-\d+ .*Plan$")
CELL_RE = re.compile(r"^[A-Z]{1,2}[1-9]\d{0,3}$")


def norm_text(s):
    return " ".join(str(s).lower().split())


def list_notes(inbox):
    if not os.path.isdir(inbox):
        return []
    return sorted(f for f in os.listdir(inbox) if f.lower().endswith(".json") and not f.lower().endswith(".result.json") and os.path.isfile(os.path.join(inbox, f)))


def processed_ids(inbox):
    """The note_id of every note already applied (read from the result files, so the file name does not matter)."""
    folder = os.path.join(inbox, "processed")
    out = set()
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.endswith(".result.json"):
                try:
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        out.add(json.load(fh).get("note_id"))
                except (OSError, ValueError, AttributeError):
                    out.add(f[:-len(".result.json")])
            elif f.endswith(".json"):
                out.add(f[:-5])
    return out


def read_note(path):
    if os.path.getsize(path) > MAX_NOTE_BYTES:
        raise Reject("the note file is larger than %d bytes" % MAX_NOTE_BYTES)
    try:
        with open(path, encoding="utf-8") as f:
            note = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        raise Reject("not valid JSON (%s)" % e)
    if not isinstance(note, dict):
        raise Reject("a note is one JSON object")
    return note


def note_text(label, value):
    if not isinstance(value, str):
        raise Reject("'%s' must be text" % label)
    try:
        return check_text(label, value)
    except Refused as e:
        raise Reject(str(e))


def note_fields(snap, note):
    fields = note.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise Reject("'fields' must be an object naming the columns to set")
    out = {}
    for key, value in fields.items():
        h = snap.by_lower.get(str(key).strip().lower())
        if not h:
            raise Reject("the workbook has no '%s' column" % key)
        if h == "ID":
            raise Reject("ID is assigned by the writer and cannot be set")
        if h not in INTAKE_COLUMNS:
            who = "a human" if h in HUMAN_COLUMNS else "Claude Code"
            raise Reject("'%s' is not an intake column (it belongs to %s); a note can set only: %s" % (h, who, ", ".join(sorted(INTAKE_COLUMNS - {"ID"}))))
        out[h] = note_text(h, value)
    return out


def column_values(snap, xml, header):
    col = snap.headers.get(header)
    vals = set()
    for ref, c in xl.cells_of(xml):
        if col and re.fullmatch(col + r"\d+", ref) and ref != col + "1":
            t = xl.cell_text(c, snap.sst).strip()
            if t:
                vals.add(t)
    return vals


class Work:
    """The sheets of the workbook as the notes before this one left them."""

    def __init__(self, snap):
        self.snap, self.xml, self.wb = snap, {}, snap.files["xl/workbook.xml"].decode("utf-8")
        self.xml[SHEET] = snap.xml

    def sheet(self, name):
        if name not in self.xml:
            if name not in self.snap.paths:
                raise Reject("the workbook has no sheet called '%s'" % name)
            self.xml[name] = self.snap.files[self.snap.paths[name]].decode("utf-8")
        return self.xml[name]

    def parts(self):
        out = {}
        for name, xml in self.xml.items():
            if xml != self.snap.files[self.snap.paths[name]].decode("utf-8"):
                out[self.snap.paths[name]] = xml
        return out


class Ctx:
    pass


def do_new_item(c, note, sender):
    snap, work = c.snap, c.work
    fields = note_fields(snap, note)
    missing = [h for h in NEW_ITEM_REQUIRED if not fields.get(h)]
    if missing:
        raise Reject("a new item needs %s" % ", ".join(missing))
    if fields["Priority"] not in c.priorities:
        raise Reject("Priority '%s' is not a value used in the workbook (%s)" % (fields["Priority"], ", ".join(sorted(c.priorities))))
    if fields["Claude Code Mode"] not in c.modes:
        raise Reject("Claude Code Mode '%s' is not a value used in the workbook (%s)" % (fields["Claude Code Mode"], ", ".join(sorted(c.modes))))
    dup = c.requests.get(norm_text(fields["Change Request"]))
    if dup:
        raise Reject("duplicate: the same Change Request is already %s" % dup)
    numbers = [int(m.group(1)) for m in (re.fullmatch(r"KW-(\d+)", i) for i in c.ids) if m]
    new_id = "KW-%02d" % ((max(numbers) if numbers else 0) + 1)
    values = dict(fields, ID=new_id, Status="Not started", **c.stamps)
    xml, row = add_row(work.sheet(SHEET), snap.headers, values)
    work.xml[SHEET] = xml
    c.ids.add(new_id)
    c.requests[norm_text(fields["Change Request"])] = new_id
    for h in values:
        c.expected[(SHEET, new_id, h)] = values[h]
    c.lines.append("%s: new item (%s): %s" % (new_id, c.res["file"], fields["Change Request"][:70]))
    c.res.update(status="applied", id=new_id, reason="new item added at row %d" % row)


def do_revision(c, note, sender):
    snap, work = c.snap, c.work
    fields = note_fields(snap, note)
    kw = note.get("id")
    if not isinstance(kw, str) or not re.fullmatch(r"KW-\d+", kw):
        raise Reject("a revision needs 'id' (for example KW-66)")
    xml = work.sheet(SHEET)
    try:
        row = snap.row_of(kw, xml)
    except Refused as e:
        raise Reject(str(e))
    current = snap.values(row, xml)
    status = current.get("Status", "")
    if status not in REVISABLE:
        raise Reject("%s is '%s': an item can be revised only while it is Not started, Deferred or Blocked; %s" % (kw, status, "raise a new item instead" if status in ("Released", "Won't do") else "wait until it is finished or blocked"))
    based = note.get("based_on")
    if not isinstance(based, dict) or "Row Updated" not in based:
        raise Reject("a revision needs based_on: {\"Row Updated\": \"<the value you saw>\"}")
    if str(based["Row Updated"]).strip() != current.get("Row Updated", ""):
        raise Reject("stale: %s was changed after your copy (Row Updated is '%s', your note saw '%s'); read the copy again" % (kw, current.get("Row Updated", ""), str(based["Row Updated"]).strip()))
    changes = {h: v for h, v in fields.items() if current.get(h, "") != v}
    if not changes:
        c.res.update(status="unchanged", id=kw, reason="the sheet already says this")
        return
    summary = note_text("revision_summary", note.get("revision_summary", ""))
    if not summary or len(summary) > 300:
        raise Reject("a revision needs revision_summary: what changed, in up to 300 characters (it is appended to Context / Rationale)")
    who = sender.replace("(", "").replace(")", "")
    base = changes.get("Context / Rationale", current.get("Context / Rationale", ""))
    changes["Context / Rationale"] = (base + "\n" if base else "") + "Revised %s (%s): %s" % (c.today, who, summary)
    for h, v in dict(changes, **c.stamps).items():
        xml = set_cell(xml, snap.headers[h], row, v)
        c.expected[(SHEET, kw, h)] = v
        if h in changes:
            old = current.get(h, "")
            c.lines.append("%s: %s: %s -> %s" % (kw, h, (old[:50] + "...") if len(old) > 50 else (old or "(blank)"), (v[:70] + "...") if len(v) > 70 else v))
    work.xml[SHEET] = xml
    warning = ""
    if current.get("Approved to Start", "").strip().lower() in APPROVED and set(changes) & RESCOPE_COLUMNS:
        warning = " | WARNING: Approved to Start is %s and the scope changed: the human must clear it so the item is approved again" % current["Approved to Start"]
    c.res.update(status="applied", id=kw, reason="revised: %s%s" % (", ".join(h for h in changes), warning))


def do_new_decision(c, note, sender):
    snap, work = c.snap, c.work
    fields = note.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise Reject("'fields' must be an object with ID, Decision Needed, Options Discussed and Recommendation")
    values = {}
    for key, value in fields.items():
        h = DECISION_FIELDS.get(str(key).strip().lower())
        if not h:
            raise Reject("'%s' cannot be set from a note: a decision row takes only ID, Decision Needed, Options Discussed and Recommendation (a human records Decided By and Decision)" % key)
        values[h] = note_text(h, value)
    missing = [h for h in ("ID", "Decision Needed", "Options Discussed", "Recommendation (Claude)") if not values.get(h)]
    if missing:
        raise Reject("a decision needs %s" % ", ".join(missing))
    if values["ID"] not in c.ids:
        raise Reject("%s is not an item on the '%s' sheet" % (values["ID"], SHEET))
    key = (values["ID"], norm_text(values["Decision Needed"]))
    if key in c.decisions:
        raise Reject("duplicate: %s already has the decision '%s'" % (values["ID"], values["Decision Needed"][:60]))
    xml = work.sheet(DECISIONS)
    headers = xl.header_columns(xml, snap.sst)
    for h in ("ID", "Decision Needed", "Options Discussed", "Recommendation (Claude)"):
        if h not in headers:
            raise Reject("'%s' has no '%s' column" % (DECISIONS, h))
    old_last = last_row_number(xml)
    xml, row = add_row(xml, headers, values, DECISIONS)
    xml, work.wb = extend_ranges(xml, work.wb, old_last, row, DECISIONS)
    work.xml[DECISIONS] = xml
    c.decisions.add(key)
    for h, v in values.items():
        c.expected[(DECISIONS, row, h)] = v
    c.lines.append("%s: new decision (%s): %s" % (values["ID"], c.res["file"], values["Decision Needed"][:70]))
    c.res.update(status="applied", id=values["ID"], reason="decision added at row %d of '%s'" % (row, DECISIONS))


def do_plan_cells(c, note, sender):
    snap, work = c.snap, c.work
    name = note.get("sheet")
    if not isinstance(name, str) or not PLAN_SHEET_RE.match(name):
        raise Reject("'sheet' must be a KW plan sheet: a name like 'KW-60 Clean Start Plan' (KW number, then words, ending in Plan)")
    cells = note.get("cells")
    if not isinstance(cells, list) or not cells or len(cells) > 50:
        raise Reject("'cells' must be a list of 1-50 {\"cell\": \"C37\", \"was\": \"<text now>\", \"now\": \"<new text>\"}")
    xml = work.sheet(name)
    seen, new_xml, done, exp, lines = set(), xml, [], {}, []                  # collected locally: a rejected note leaves nothing behind
    for item in cells:
        if not isinstance(item, dict) or not isinstance(item.get("cell"), str) or "was" not in item or "now" not in item:
            raise Reject("every cell needs cell, was and now")
        ref = item["cell"].strip().upper()
        if not CELL_RE.match(ref) or ref in seen:
            raise Reject("'%s' is not a single cell like C37, or it is named twice" % item["cell"])
        seen.add(ref)
        was, now = note_text("was", item["was"]).strip(), note_text("now", item["now"])
        m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
        if not re.search(r'<row r="%s"' % m.group(2), new_xml):
            raise Reject("the sheet '%s' has no row %s" % (name, m.group(2)))
        found = [xl.cell_text(cl, snap.sst).strip() for r2, cl in xl.cells_of(new_xml) if r2 == ref]
        current = found[0] if found else ""
        if current != was:
            raise Reject("stale: %s!%s now says '%s', your note saw '%s'; read the copy again" % (name, ref, current[:60], was[:60]))
        if current == now:
            continue
        new_xml = set_cell(new_xml, m.group(1), int(m.group(2)), now)
        exp[(name, ref, "")] = now
        done.append(ref)
        lines.append("%s!%s: %s -> %s" % (name, ref, (current[:50] + "...") if len(current) > 50 else (current or "(blank)"), (now[:70] + "...") if len(now) > 70 else now))
    if not done:
        c.res.update(status="unchanged", id=name, reason="the sheet already says this")
        return
    work.xml[name] = new_xml
    c.expected.update(exp)
    c.lines.extend(lines)
    c.res.update(status="applied", id=name, reason="cells set: %s" % ", ".join(done))


def plan_inbox(snap, inbox, today):
    """Judge every note against the workbook as it will be after the notes before it. -> (parts {part: xml}, workbook xml, results, lines, expected).
    results = [{file, note_id, status applied|rejected|unchanged, id, reason}]"""
    c = Ctx()
    c.snap, c.work, c.today = snap, Work(snap), today
    c.expected, c.lines = {}, []
    done = processed_ids(inbox)
    seen, results = set(), []
    c.ids = {i for i, _r in snap.id_rows()}
    c.requests = {}
    for i, r in snap.id_rows():
        c.requests.setdefault(norm_text(snap.values(r).get("Change Request", "")), i)
    c.priorities, c.modes = column_values(snap, snap.xml, "Priority"), column_values(snap, snap.xml, "Claude Code Mode")
    c.decisions = set()
    if DECISIONS in snap.paths:
        dxml = snap.files[snap.paths[DECISIONS]].decode("utf-8")
        dh = xl.header_columns(dxml, snap.sst)
        id_col, need_col = dh.get("ID"), dh.get("Decision Needed")
        by_row = {}
        for ref, cell in xl.cells_of(dxml):
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
            if m and int(m.group(2)) > 1 and m.group(1) in (id_col, need_col):
                by_row.setdefault(int(m.group(2)), {})[m.group(1)] = xl.cell_text(cell, snap.sst).strip()
        c.decisions = {(v.get(id_col, ""), norm_text(v.get(need_col, ""))) for v in by_row.values()}
    for name in list_notes(inbox):
        c.res = res = {"file": name, "note_id": name[:-5], "status": "rejected", "id": "", "reason": ""}
        results.append(res)
        try:
            note = read_note(os.path.join(inbox, name))
            note_id = note.get("note_id")
            if not isinstance(note_id, str) or not NOTE_ID_RE.match(note_id):
                raise Reject("note_id is required: 6-80 letters, digits, dot, dash or underscore, unique for every note")
            res["note_id"] = note_id
            if note_id in done or note_id in seen:
                raise Reject("duplicate note: note_id '%s' was already applied" % note_id)
            seen.add(note_id)
            sender = note.get("from", "Claude (chat)")
            if not isinstance(sender, str) or not sender.strip() or CTRL_RE.search(sender) or len(sender) > 60:
                raise Reject("'from' must be a short text such as 'Claude (chat)'")
            sender = sender.strip()
            c.stamps = {"Row Updated": today, "Updated By": "%s (inbox)" % sender}
            for stamp in c.stamps:
                if stamp not in snap.headers:
                    raise Reject("the workbook has no '%s' column to stamp" % stamp)
            handler = {"new_item": do_new_item, "revision": do_revision, "new_decision": do_new_decision, "plan_cells": do_plan_cells}.get(note.get("kind"))
            if not handler:
                raise Reject("kind must be 'new_item', 'revision', 'new_decision' or 'plan_cells'")
            handler(c, note, sender)
        except Reject as e:
            res["reason"] = str(e)
    xml = c.work.xml[SHEET]
    old_last, new_last = last_row_number(snap.xml), last_row_number(xml)
    wb = c.work.wb
    if new_last != old_last:
        xml, wb = extend_ranges(xml, wb, old_last, new_last)
        c.work.xml[SHEET] = xml
    return c.work.parts(), wb, results, c.lines, c.expected


def file_notes(inbox, results, write=True):
    """Move each judged note to processed\\ or rejected\\ with a <note>.result.json beside it."""
    for res in results:
        sub = "rejected" if res["status"] == "rejected" else "processed"
        folder = os.path.join(inbox, sub)
        os.makedirs(folder, exist_ok=True)
        stem = res["file"][:-5]
        target = os.path.join(folder, res["file"])
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(folder, "%s-%d.json" % (stem, n))
        shutil.move(os.path.join(inbox, res["file"]), target)
        with open(target[:-5] + ".result.json", "w", encoding="utf-8") as f:
            json.dump({"note_id": res["note_id"], "result": res["status"], "id": res["id"], "reason": res["reason"], "handled": datetime.datetime.now(datetime.timezone.utc).isoformat()}, f, indent=2)


# ---- backups, the SharePoint copy, restore ---------------------------------------------------------------------------------------------------------------------------------
def utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")


def backup_names(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if BACKUP_RE.match(f))


def prune(folder, keep=cp.BACKUP_KEEP):
    for f in backup_names(folder)[:-keep]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass


def make_backup(master, action):
    """Copy the master to Backups\\ before it is changed; keep the newest 30. Raises OSError if it cannot be done."""
    folder = cp.backup_dir(master)
    os.makedirs(folder, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9]+", "-", action).strip("-")[:30] or "write"
    stamp = utc_stamp()                                                # <UTC second>-<nn>: names sort in the order the backups were made
    same_second = [int(m.group(1)[-2:]) for m in (BACKUP_RE.match(f) for f in os.listdir(folder)) if m and m.group(1).startswith(stamp + "-")]
    n = min(max(same_second, default=0) + 1, 99)                       # one more than the highest taken, never a gap that pruning left (a reused low number would sort as the OLDEST)
    name = "LOG_KelpWorks_ChangeControl_%s-%02d_%s.xlsx" % (stamp, n, label)
    path = os.path.join(folder, name)
    shutil.copyfile(master, path)
    prune(folder)
    return path


def mirror_backups(master):
    """The second copy of the backups in SharePoint (best effort). -> warning text or ''."""
    mirror = cp.mirror_for(master)
    if not mirror:
        return ""
    try:
        if not os.path.isdir(os.path.dirname(mirror)):
            return "the SharePoint folder %s was not found, so the backups were not mirrored" % os.path.dirname(mirror)
        os.makedirs(mirror, exist_ok=True)
        local = cp.backup_dir(master)
        have = set(backup_names(mirror))
        for f in backup_names(local)[-cp.BACKUP_KEEP:]:
            if f not in have:
                shutil.copyfile(os.path.join(local, f), os.path.join(mirror, f))
        prune(mirror)
    except OSError as e:
        return "the backups could not be mirrored to SharePoint (%s); the next write will copy them" % e
    return ""


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def publish(master, snapshot):
    """Copy the master over the read-only SharePoint copy (temp file + replace), set it read-only and compare checksums. Raises OSError."""
    folder = os.path.dirname(os.path.abspath(snapshot))
    if not os.path.isdir(folder):
        raise OSError("the SharePoint folder was not found: %s" % folder)
    fd, tmp = tempfile.mkstemp(prefix=".publish_", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(master, tmp)
        if os.path.exists(snapshot):
            os.chmod(snapshot, stat.S_IREAD | stat.S_IWRITE)
        os.replace(tmp, snapshot)
        os.chmod(snapshot, stat.S_IREAD)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if sha_of(snapshot) != sha_of(master):
        raise OSError("the SharePoint copy does not match the master after copying")


def replace_file(source, target):
    """Put `source` in place of `target` atomically (temp file beside the target)."""
    fd, tmp = tempfile.mkstemp(prefix=".changelog_write_", suffix=".tmp", dir=os.path.dirname(os.path.abspath(target)))
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---- saving and verifying ---------------------------------------------------------------------------------------------------------------------------------------------
def save(snap, parts, wb_xml=None):
    """parts = {part name: new xml text}. Everything else in the file is copied byte for byte."""
    files = dict(snap.files)
    for name, xml in parts.items():
        files[name] = xml.encode("utf-8")
    wb = wb_xml if wb_xml is not None else files["xl/workbook.xml"].decode("utf-8")
    if "fullCalcOnLoad" not in wb:                                      # the Summary sheet recalculates when Excel opens the file
        wb = re.sub(r"<calcPr\b([^>]*?)/>", r'<calcPr\1 fullCalcOnLoad="1"/>', wb) if re.search(r"<calcPr\b", wb) else wb.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
    files["xl/workbook.xml"] = wb.encode("utf-8")
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=".changelog_write_", dir=os.path.dirname(os.path.abspath(snap.path)))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for i in snap.infos:
                z.writestr(i.filename, files[i.filename])
        os.replace(tmp, snap.path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def verify(path, expected):
    """Re-open the saved workbook and compare every written cell. expected = {(sheet, id or row, header): text}. -> [(id, header, wanted, found)] of the values that did not stick."""
    snap = Snapshot(path)
    bad = []
    for (sheet, key, h), want in expected.items():
        try:
            if h == "#sig":                                                  # a whole sheet written from a file (the Workflow Rules sheet)
                found = sheet_sig(snap.files[snap.paths[sheet]].decode("utf-8"), snap.sst)
            elif h == "":                                                    # one cell by reference (a KW plan sheet)
                found = snap.cell_ref(sheet, key)
            elif sheet == SHEET:
                found = snap.values(snap.row_of(key)).get(h, "")
            else:
                found = snap.cell(sheet, key, h)
        except Refused:
            found = "(row not found)"
        if found != want:
            bad.append((key, h, want, found))
    return bad


# ---- command line ------------------------------------------------------------------------------------------------------------------------------------------------------------
def parse_pairs(tokens):
    out = {}
    for t in tokens:
        if "=" not in t:
            raise Refused("'%s' is not column=value" % t)
        k, v = t.split("=", 1)
        out[k.strip()] = v
    return out


VALUE_OPTIONS = ("--file", "--id", "--batch", "--show", "--verify-wait", "--decision", "--by", "--text", "--needed", "--restore", "--rules-file")
FLAG_OPTIONS = ("--dry-run", "--apply-inbox", "--publish", "--backups", "--migrate", "--rules-sheet")


def parse_args(argv):
    a = {"file": cp.configured_path(), "file_given": False, "dry": False, "wait": VERIFY_WAIT, "id": None, "pairs": [], "batch": None, "show": None, "decision": None, "by": None, "text": None,
         "needed": None, "restore": None, "apply_inbox": False, "publish": False, "backups": False, "migrate": False, "rules_sheet": False, "rules_file": RULES_FILE}
    i = 0
    while i < len(argv):
        t = argv[i]
        if t in ("-h", "--help"):
            return None
        if t in FLAG_OPTIONS:
            a[t[2:].replace("-", "_") if t != "--dry-run" else "dry"] = True
        elif t in VALUE_OPTIONS:
            i += 1
            if i >= len(argv):
                return None
            v = argv[i]
            if t == "--verify-wait":
                a["wait"] = float(v)
            else:
                a[t[2:].replace("-", "_")] = v
                if t == "--file":
                    a["file_given"] = True
        elif t.startswith("--"):
            return None
        else:
            a["pairs"].append(t)
        i += 1
    return a


def commit(a, snap, parts, wb_xml, lines, expected, label, after=None):
    """The common tail of every write: show, stale check, backup, save, wait, verify, hand over the notes, refresh the SharePoint copy. -> exit code."""
    path = a["path"]
    for l in lines:
        print(l)
    if not lines:
        print("Nothing to change: the workbook already says this.")
        return 0
    if a["dry"]:
        print("(dry run: every rule passed; the workbook was not written)")
        return 0
    if not snap.unchanged():
        print("STOPPED: the workbook changed while this was running (modified time or contents differ). Nothing was written; read it again and re-apply.")
        return 4
    if lock_files(path):
        print("STOPPED: the workbook was opened in Excel just now. Nothing was written.")
        return 3
    try:
        make_backup(path, label)
    except OSError as e:
        print("STOPPED: a backup could not be made (%s). Nothing was written." % e)
        return 9
    warning = mirror_backups(path)
    if warning:
        print("WARNING: %s." % warning)
    try:
        save(snap, parts, wb_xml)
    except PermissionError:
        print("STOPPED: the workbook is locked (open in Excel or being synced). Nothing was written.")
        return 3
    print("saved %d change(s) in %s; checking again in %d s..." % (len(lines), os.path.basename(path), a["wait"]))
    if a["wait"] > 0:
        time.sleep(a["wait"])
    try:
        bad = verify(path, expected)
    except (ValueError, OSError) as e:
        print("REVERT SUSPECTED: the workbook could not be read back (%s)." % e)
        return 5
    if bad:
        print("REVERT DETECTED: these values did not stick (someone or something saved an older copy over this change):")
        for kw, h, want, found in bad:
            print("  %s %s: wanted '%s', found '%s'" % (kw, h, want[:60], found[:60]))
        print("Stopped. Do not retry blindly: close every open copy of the workbook, check --backups, then run it again.")
        return 5
    print("VERIFIED: every value is still in the file after %d s." % a["wait"])
    if after:
        after()
    return finish_publish(path)


def finish_publish(path, wrote=True):
    snapshot = cp.snapshot_for(path)
    if not snapshot:
        print("(no SharePoint copy is configured for this file, so none was refreshed)")
        return 0
    try:
        publish(path, snapshot)
    except OSError as e:
        print("%sthe SharePoint copy was NOT refreshed (%s). Fix that (close the SharePoint copy if it is open) and run: python tools/changelog_write.py --publish" % ("WRITTEN, but " if wrote else "", e))
        return 7
    print("SharePoint copy refreshed (read-only): %s" % snapshot)
    return 0


def show(snap, kw):
    try:
        row = snap.values(snap.row_of(kw))
    except Refused as e:
        print("Refused: %s" % e)
        return 2
    for h in ("ID", "Status", "Owner", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date", "QM-001 Ref", "Row Updated", "Updated By", "Notes"):
        if h in row:
            print("%-24s %s" % (h, (row[h][:200] + "...") if len(row[h]) > 200 else row[h]))
    return 0


def list_backups(path):
    local, mirror = cp.backup_dir(path), cp.mirror_for(path)
    names = backup_names(local)
    mirrored = set(backup_names(mirror)) if mirror else set()
    if not names and not mirrored:
        print("No backups yet (%s)." % local)
        return 0
    print("Backups in %s%s:" % (local, ("; mirror %s" % mirror) if mirror else ""))
    for n in sorted(set(names) | mirrored):
        print("  %s  %s%s" % (n, "local" if n in names else "mirror only", "+mirror" if n in names and n in mirrored else ""))
    return 0


def find_backup(path, which):
    local, mirror = cp.backup_dir(path), cp.mirror_for(path)
    pools = [(local, backup_names(local))] + ([(mirror, backup_names(mirror))] if mirror else [])
    everything = sorted({n for _f, ns in pools for n in ns})
    if not everything:
        raise Refused("there are no backups to restore from")
    name = everything[-1] if which == "latest" else which
    if name not in everything:
        raise Refused("no backup called '%s' (see --backups)" % which)
    for folder, names in pools:
        if name in names:
            return os.path.join(folder, name)


def main(argv=None):
    a = parse_args(list(sys.argv[1:] if argv is None else argv))
    modes = [m for m in ("id", "batch", "show", "decision", "restore") if a and a[m]] + [m for m in ("apply_inbox", "publish", "backups", "migrate", "rules_sheet") if a and a[m]]
    if a is None or len(modes) != 1:
        print(__doc__)
        return 1
    if a["migrate"] and not a["file_given"]:
        a["file"] = cp.snapshot_for(cp.configured_path()) or cp.configured_path()          # --migrate reads the SharePoint workbook unless --file says otherwise
    path = a["path"] = a["file"]
    master = cp.configured_path()
    if a["migrate"]:
        return run_migrate(a, path, master)
    if a["backups"]:
        return list_backups(path)
    if not os.path.isfile(path) and not a["restore"]:
        print("STOPPED: the workbook was not found: %s" % path)
        return 6
    if a["show"] or a["publish"]:
        try:
            snap = Snapshot(path)
        except (ValueError, OSError) as e:
            print("STOPPED: %s" % e)
            return 6
        if a["show"]:
            return show(snap, a["show"])
        snapshot = cp.snapshot_for(path)
        if not snapshot:
            print("STOPPED: no SharePoint copy is configured for %s (KELPWORKS_SNAPSHOT)." % path)
            return 2
        try:
            cp.check_writable(path)                              # only the master is ever published over the read-only copy
        except ValueError as e:
            print("Refused: %s\nNothing was published." % e)
            return 2
        return finish_publish(path, wrote=False)
    writes = not a["dry"]
    if writes:
        try:
            cp.check_writable(path)
        except ValueError as e:
            print("Refused: %s\nNothing was written." % e)
            return 2
    try:
        if writes:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        lock = WriteLock(os.path.dirname(os.path.abspath(path))) if writes else None
        if lock:
            lock.__enter__()
    except (Locked, OSError) as e:
        print("STOPPED: %s" % e)
        return 3
    try:
        return run_write(a, path, writes)
    finally:
        if lock:
            lock.__exit__()


def run_write(a, path, writes):
    locks = lock_files(path)
    if a["restore"]:
        return run_restore(a, path, locks)
    if locks:
        print("STOPPED: the workbook is open in Excel (lock file %s). Close it everywhere (desktop and browser) and run this again. Nothing was written." % locks[0])
        return 3
    try:
        snap = Snapshot(path)
    except (ValueError, OSError) as e:
        print("STOPPED: %s" % e)
        return 6
    after, label = None, "write"
    try:
        if a["apply_inbox"]:
            inbox = cp.inbox_for(path)
            if not inbox:
                raise Refused("no inbox is configured for %s (KELPWORKS_INBOX)" % path)
            if not os.path.isdir(inbox):
                raise Refused("the inbox folder was not found: %s" % inbox)
            if writes and not os.path.exists(os.path.join(inbox, "README_NOTE_FORMAT.txt")):
                with open(os.path.join(inbox, "README_NOTE_FORMAT.txt"), "w", encoding="utf-8") as f:
                    f.write(NOTE_FORMAT)
            if not list_notes(inbox):
                print("The inbox is empty (%s)." % inbox)
                return 0
            parts, wb_xml, results, lines, expected = plan_inbox(snap, inbox, datetime.date.today().isoformat())
            for res in results:
                print("note %s: %s%s%s" % (res["file"], res["status"].upper(), (" " + res["id"]) if res["id"] else "", (" - " + res["reason"]) if res["reason"] else ""))
            rejected = any(r["status"] == "rejected" for r in results)
            if not lines:                                             # nothing to write: only file the notes away
                if writes and results:
                    file_notes(inbox, results)
                return 8 if rejected else 0
            label = "inbox"
            after = (lambda: file_notes(inbox, results)) if writes else None
            code = commit(a, snap, parts, wb_xml, lines, expected, label, after)
            return code if code else (8 if rejected else 0)
        if a["rules_sheet"]:
            spec = load_rules(a["rules_file"])
            sheet_xml, last = build_rules_sheet(snap, spec)
            old_sig = sheet_sig(snap.files[snap.paths[RULES]].decode("utf-8"), snap.sst)
            new_sig = sheet_sig(sheet_xml, snap.sst)
            lines = [] if old_sig == new_sig else ["%s sheet rewritten from %s (%d rows)" % (RULES, os.path.basename(a["rules_file"]), last)]
            return commit(a, snap, {snap.paths[RULES]: sheet_xml}, None, lines, {(RULES, "sig", "#sig"): new_sig}, "rules-sheet")
        if a["decision"]:
            part, xml, lines, expected = plan_decision(snap, a["decision"], a["by"] or "", a["text"] or "", a["needed"])
            return commit(a, snap, {part: xml}, None, ["%s: %s -> %s" % (l[0], (l[1][:50] or "(blank)"), l[2][:70]) for l in lines], expected, "decision")
        if a["batch"]:
            with open(a["batch"], encoding="utf-8") as f:
                updates = json.load(f)
            if not isinstance(updates, list) or not all(isinstance(u, dict) and u.get("id") and isinstance(u.get("set"), dict) for u in updates):
                raise Refused("a batch file is a list of {\"id\": \"KW-nn\", \"set\": {column: value}}")
        else:
            updates = [{"id": a["id"], "set": parse_pairs(a["pairs"])}]
        ids = [u["id"] for u in updates]
        if len(set(ids)) != len(ids):
            raise Refused("an item appears twice in the batch: combine its changes")
        xml, lines, expected = snap.xml, [], {}
        for u in updates:
            xml, changes, exp = plan_update(snap, xml, u["id"], u["set"])
            expected.update(exp)
            for h, old, new in changes:
                lines.append("%s: %s: %s -> %s" % (u["id"], h, (old[:50] + "...") if len(old) > 50 else (old or "(blank)"), (new[:70] + "...") if len(new) > 70 else new))
        label = "update-" + "-".join(ids)[:20]
    except (Refused, ValueError, OSError, json.JSONDecodeError) as e:
        print("Refused: %s\nNothing was written." % e)
        return 2
    return commit(a, snap, {snap.part: xml}, None, lines, expected, label)


def run_restore(a, path, locks):
    try:
        source = find_backup(path, a["restore"])
        Snapshot(source)                                             # the backup must be a readable workbook
    except (Refused, ValueError, OSError) as e:
        print("Refused: %s\nNothing was restored." % e)
        return 2
    if a["dry"]:
        print("(dry run) would restore %s over %s" % (source, path))
        return 0
    if locks:
        print("STOPPED: the workbook is open in Excel (lock file %s). Close it everywhere and run this again. Nothing was restored." % locks[0])
        return 3
    try:
        if os.path.exists(path):
            make_backup(path, "before-restore")
        warning = mirror_backups(path)
        if warning:
            print("WARNING: %s." % warning)
        replace_file(source, path)
    except PermissionError:
        print("STOPPED: the workbook is locked (open in Excel or being synced). Nothing was restored.")
        return 3
    except OSError as e:
        print("STOPPED: %s" % e)
        return 9
    if sha_of(path) != sha_of(source):
        print("REVERT SUSPECTED: the restored file does not match the backup.")
        return 5
    print("RESTORED %s as the master." % os.path.basename(source))
    return finish_publish(path)


def run_migrate(a, source, master):
    """One time: copy the SharePoint workbook to the master folder, back it up, and make the SharePoint file the read-only copy."""
    try:
        cp.check_writable(master)
    except ValueError as e:
        print("Refused: %s" % e)
        return 2
    if os.path.exists(master):
        print("Refused: the master already exists (%s). Nothing was changed." % master)
        return 2
    if not os.path.isfile(source):
        print("STOPPED: the workbook to migrate was not found: %s" % source)
        return 6
    if lock_files(source):
        print("STOPPED: the workbook is open in Excel (%s). Close it everywhere (desktop and browser) and run this again." % lock_files(source)[0])
        return 3
    try:
        snap = Snapshot(source)
        for need in ("ID", "Status", "Approved to Start", "Row Updated", "Updated By"):
            if need not in snap.headers:
                raise ValueError("the workbook has no '%s' column" % need)
    except (ValueError, OSError) as e:
        print("STOPPED: %s" % e)
        return 6
    if a["dry"]:
        print("(dry run) would copy %s to %s, take backup 1 and publish it back read-only" % (source, master))
        return 0
    try:
        os.makedirs(os.path.dirname(os.path.abspath(master)), exist_ok=True)
        replace_file(source, master)
        make_backup(master, "migrated")
    except OSError as e:
        print("STOPPED: %s" % e)
        return 9
    print("Master created: %s (%d bytes)" % (master, os.path.getsize(master)))
    warning = mirror_backups(master)
    if warning:
        print("WARNING: %s." % warning)
    inbox = cp.inbox_for(master)
    if inbox and os.path.isdir(os.path.dirname(inbox)):
        try:
            os.makedirs(inbox, exist_ok=True)
            with open(os.path.join(inbox, "README_NOTE_FORMAT.txt"), "w", encoding="utf-8") as f:
                f.write(NOTE_FORMAT)
        except OSError as e:
            print("WARNING: the inbox could not be set up (%s)" % e)
    code = finish_publish(master)
    print("Edit the master from now on: %s" % master)
    return code


if __name__ == "__main__":
    sys.exit(main())
