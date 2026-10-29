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
    python tools/changelog_write.py --playbook-sheet [--playbook-file f.json]   # rewrite the Claude Code Playbook sheet from docs/backlog/claude-code-playbook.json
    python tools/changelog_write.py --classify-setup              # one time (KW-79): add Touches after Type, remove the Claude Code Prompt column, a Type drop-down
    python tools/changelog_write.py --default-owner               # one time (KW-79): Owner = N. Wrana where blank (and the spelling N.Wrana); no other name is changed
    python tools/changelog_write.py --qm-setup                    # one time (KW-71): add the 'QM-001 Impact' column and the 'QM-001 Changes' register sheet
    python tools/changelog_write.py --qm-entry entry.json         # draft a manual change on the register: {"kw_items": ["KW-99"], "section": "...", "change": "Replace|Add|Delete", "before": "...", "after": "...", "reason": "..."}
    python tools/changelog_write.py --qm-backfill backfill.json   # one time: impacts for Released items (only where blank) and register rows for the refs the manual already carries
    python tools/changelog_write.py --qm-drafted C21,C22 --text "<draft file, date>"   # note which draft revision approved entries went into (qm_draft_revision.py calls this)
    python tools/changelog_write.py --qm-report                   # read-only: released items with no confirmed impact, entries waiting for the Quality Manager, approved entries not yet in the manual
    python tools/changelog_write.py --publish                     # copy the master to SharePoint now (after a hand edit in Excel)
    python tools/changelog_write.py --backups                     # list the backups (local + SharePoint mirror)
    python tools/changelog_write.py --restore latest|<backup name># put a backup back as the master (backs the current one up first)
    python tools/changelog_write.py --migrate [--file <old workbook>]   # one time: copy the SharePoint workbook to the master folder

What it enforces (the 'Workflow Rules' sheet of the workbook):

* QM-001 (KW-71): an item cannot be Released until its QM-001 Impact is recorded and confirmed ('<class> - <one line>', not PROPOSED); a class other than None also needs a QM-001 Ref that is
  on the 'QM-001 Changes' sheet. The Quality Manager's columns on that sheet (QM Decision, Decided By, Decision Date, Manual Revision, Entered in Manual) are human-only.
* Ownership: item updates may write only Status, Notes, PR #, Released Date, QM-001 Ref, QM-001 Impact, Type and Touches (Row Updated and Updated By are stamped automatically, except when only
  Type / Touches / QM-001 Impact change: they classify a request and do not change it). Every other column belongs to the intake chat or to a human, and is refused. The intake columns change, and rows are added,
  ONLY from a valid inbox note. A new item needs a Type (one value from a fixed list) and Touches (one or more areas) once the workbook has the Touches column, and gets Owner N. Wrana.
* Status: Not started, In progress, On staging, Released or Blocked. Deferred and Won't do are a human's decision and are refused; so is moving an item OUT of them.
  In progress / On staging / Released need Approved to Start = Y or Pre-rule. Released also needs a PR # and a Released Date (given now or already in the row).
* Only the master is written (the right file name in a folder called ChangeControl, never inside OneDrive, never the SharePoint copy). Reading (--show, --dry-run) may use any copy.
* Safe write: it stops if Excel has the master open (a ~$ lock file) or another run is writing (.changelog_write.lock), reads the file fresh and records its modified time and checksum,
  finds rows by ID (never by row number), re-checks both just before saving, takes a BACKUP (newest 30 kept, second copy in SharePoint Backups), saves atomically changing only the
  named cells, waits (10 s, --verify-wait) and re-opens the file to confirm every value stuck, then refreshes the SharePoint copy. A revert is reported, never retried.

Exit codes: 0 done, 1 usage, 2 refused by a rule (nothing written), 3 the master is open in Excel / locked / another run is writing, 4 the file changed while this ran (nothing written),
5 a value did not stick (revert), 6 the workbook could not be read, 7 written but the SharePoint copy was NOT refreshed after 5 tries over about 30 s (run --publish), 8 some inbox notes were rejected (the valid
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
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_backlog_xlsx as xl                                                  # the XML helpers (sheet lookup, cells, escaping)
import changelog_path as cp                                                       # the ONE place that says where the change log is (KW-62, KW-66)

DEFAULT_PATH = cp.DEFAULT_PATH
SHEET = "Change Log"
DECISIONS = "Open Decisions"
RULES = "Workflow Rules"
RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "backlog", "workflow-rules.json")
IMPACT = "QM-001 Impact"                               # KW-71: what a change does to the Quality Manual (QM-001), recorded for EVERY item before it can be Released
QM_SHEET = "QM-001 Changes"                            # the register of manual changes (one row per change; the Quality Manager's columns are human-only)
IMPACT_CLASSES = ["None", "Requirement changed", "Requirement added", "Requirement relaxed", "Record / control changed"]
OWNED = ["Status", "Notes", "PR #", "Released Date", "QM-001 Ref", "Type", "Touches", IMPACT]
STAMPS = {"Row Updated": lambda: datetime.date.today().isoformat(), "Updated By": lambda: "Claude Code"}
STATUSES = ["Not started", "In progress", "On staging", "Released", "Blocked"]
HUMAN_STATUSES = ["Deferred", "Won't do"]
NEEDS_APPROVAL = ("In progress", "On staging", "Released")
APPROVED = ("y", "pre-rule")
HUMAN_COLUMNS = {"Owner", "Approved to Start", "Verified By (on live)", "Verified Date"}
INTAKE_COLUMNS = {"ID", "Module", "Section", "Type", "Touches", "Change Request", "Context / Rationale", "Transcript Time", "Raised By", "Priority", "Lens", "Claude Code Mode",
                  "Acceptance Criteria"}
NEW_ITEM_REQUIRED = ["Module", "Section", "Type", "Change Request", "Raised By", "Priority", "Claude Code Mode", "Acceptance Criteria"]
# KW-79: every request is classified. Type = ONE value from this list (a drop-down in Excel: a typed list cannot hold a comma, so no name has one); Touches = one or more areas, written "A; B".
TYPES = ["Security & hardening", "Data integrity & traceability", "Quality & compliance", "Production workflow", "Inventory & shipping", "Samples & lab", "Reporting & analytics",
         "User experience", "Equipment & maintenance", "Process & tooling", "Operations (non-ERP)"]
TOUCHES = ["Production log", "Feedstock inventory", "Pre-processing", "Inventory items", "CIP / SIP log", "Samples", "Product release", "Finished goods / shipping", "Documents / SOPs",
           "Admin / users", "Settings / Calculations", "Reports / QC charts", "Records archive / backups", "Database / schema", "Change-log tooling", "Labels / printing", "New module",
           "Whole app", "Outside KelpWorks"]
METADATA = {"Type", "Touches", IMPACT}                 # they describe a request, never change its scope: a change to only these is not stamped (Row Updated stays the last REAL change)
DEFAULT_OWNER = "N. Wrana"                             # every task has an owner by default; a person changes the cell in Excel
LAYOUT_INSERT, LAYOUT_AFTER, LAYOUT_DELETE, TOUCHES_WIDTH = "Touches", "Type", "Claude Code Prompt", 30
PLAYBOOK = "Claude Code Playbook"
PLAYBOOK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "backlog", "claude-code-playbook.json")
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
note_id must be unique for ever (letters, digits, dot, dash, underscore; 6-80 characters). Five kinds of note:

1. NEW ITEM
{"note_id": "chat-20261022-001", "kind": "new_item", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z",
 "fields": {"Module": "...", "Section": "...", "Type": "...", "Change Request": "...", "Context / Rationale": "...", "Transcript Time": "...", "Raised By": "...",
            "Priority": "P2", "Lens": "...", "Claude Code Mode": "Direct", "Acceptance Criteria": "..."}}
The writer gives it the next free KW number (Status Not started, Owner N. Wrana, Approved to Start blank: a human approves it). Required: Module, Section, Type, Touches, Change Request,
Raised By, Priority, Claude Code Mode, Acceptance Criteria. Priority and Claude Code Mode must be values already used in the workbook.
Type is exactly ONE of: %(types)s.
Touches is one or more of: %(touches)s (separated by semicolons, e.g. "Production log; Product release").
A new item without a valid Type and Touches is rejected with this list in the message.

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

5. QM-001 REGISTER ENTRY (a change the Quality Manual needs: Claude drafts it, only the Quality Manager decides it)
{"note_id": "chat-20261029-005", "kind": "qm_entry", "from": "Claude (chat)", "created": "...",
 "fields": {"kw_items": ["KW-99"], "section": "2.2.2 User Roles", "change": "Replace", "before": "<the current wording>", "after": "<the new wording>", "reason": "..."}}
change is Replace (needs before and after), Add (needs after and insert_after: the start of the paragraph the new wording goes after) or Delete (needs before). Every kw_items entry must be an
item on the Change Log. The next C-number is assigned and returned; with "ref" among the fields an entry that has no Quality Manager decision yet is revised instead. QM Decision, Decided By,
Decision Date, Manual Revision and Entered in Manual are the Quality Manager's: a note cannot set them.

Only the intake columns Module, Section, Type, Touches, Change Request, Context / Rationale, Transcript Time, Raised By, Priority, Lens, Claude Code Mode and
Acceptance Criteria can be set on an item. Status, Notes, Approved to Start, PR #, Owner and every other column are refused. A note with the same note_id as an applied one, or a
new item whose Change Request is already on the sheet, is rejected as a duplicate.
""" % {"types": "; ".join(TYPES), "touches": "; ".join(TOUCHES)}


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


def check_value(column, value, classified=False):
    """classified = the workbook has the Touches column: then Type must be one of TYPES (an older layout keeps free-text Types)."""
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
    elif column == "Type" and classified:
        value = check_text(column, value)
        hit = {t.lower(): t for t in TYPES}.get(value.lower())
        if not hit:
            raise Refused("Type must be exactly one of: %s (got '%s')" % ("; ".join(TYPES), value or "blank"))
        value = hit
    elif column == "Touches":
        value = check_text(column, value)
        known, picked = {t.lower(): t for t in TOUCHES}, set()
        for part in value.split(";"):
            part = part.strip()
            if not part:
                continue
            hit = known.get(part.lower())
            if not hit:
                raise Refused("Touches takes areas from this list, separated by semicolons: %s (got '%s')" % ("; ".join(TOUCHES), part))
            picked.add(hit)
        if not picked:
            raise Refused("Touches needs at least one area from: %s" % "; ".join(TOUCHES))
        value = "; ".join(t for t in TOUCHES if t in picked)
    elif column == IMPACT:
        value = check_impact(value)
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
        new[h] = check_value(h, value, "Touches" in snap.headers)
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
            if was != "Released":                                        # KW-71: the QM-001 impact is part of being Released (a repeat of the same status is not judged again)
                if IMPACT not in snap.headers:
                    raise Refused("%s cannot be Released: the workbook has no '%s' column yet. Run: python tools/changelog_write.py --qm-setup" % (kw, IMPACT))
                impact_gate(snap, kw, new.get(IMPACT) if IMPACT in new else current.get(IMPACT, ""), new.get("QM-001 Ref") if "QM-001 Ref" in new else current.get("QM-001 Ref", ""))
    if all(current.get(h, "") == text for h, text in new.items()):
        return xml, [], {}                                               # already as asked: nothing is written, not even the stamps
    if not set(h for h, text in new.items() if current.get(h, "") != text) <= METADATA:
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


# ---- the layout of the Change Log: Touches after Type, the Claude Code Prompt column removed (KW-79) -------------------------------------------------------------------------
def letters(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def split_ref(ref):
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return m.group(1), int(m.group(2))


def column_plan(headers):
    """The new column order. -> (mapping {old letter: new letter or None for the removed column}, new letter of the inserted column, [header names in the new order])."""
    named = sorted(((colnum(l), h) for h, l in headers.items() if h), key=lambda kv: kv[0])
    if [n for n, _h in named] != list(range(1, len(named) + 1)):
        raise Refused("the header row of '%s' has gaps (columns must run A, B, C ... without a blank header)" % SHEET)
    order = [h for _n, h in named]
    if LAYOUT_INSERT in order:
        raise Refused("the '%s' sheet already has a '%s' column" % (SHEET, LAYOUT_INSERT))
    for need in (LAYOUT_AFTER, LAYOUT_DELETE):
        if need not in order:
            raise Refused("the '%s' sheet has no '%s' column, so the layout change does not apply" % (SHEET, need))
    kept = [h for h in order if h != LAYOUT_DELETE]
    at = kept.index(LAYOUT_AFTER) + 1
    new_order = kept[:at] + [LAYOUT_INSERT] + kept[at:]
    new_letter = {h: letters(i + 1) for i, h in enumerate(new_order)}
    mapping = {headers[h]: (None if h == LAYOUT_DELETE else new_letter[h]) for h in order}
    return mapping, new_letter[LAYOUT_INSERT], new_order


def remap_refs(text, mapping):
    """Move the column letters of every cell or column reference in a formula / range; text inside quotes is left alone. A reference to the removed column is refused."""
    def col(letter):
        new = mapping.get(letter, letter)
        if new is None:
            raise Refused("a formula, rule or range refers to column %s, which is being removed" % letter)
        return new

    def cell(m):
        return m.group(1) + col(m.group(2)) + m.group(3) + m.group(4)

    def colrange(m):
        return m.group(1) + col(m.group(2)) + ":" + m.group(3) + col(m.group(4))

    parts = text.split('"')
    for i in range(0, len(parts), 2):
        s = re.sub(r"(\$?)([A-Z]{1,3})(\$?)(\d+)", cell, parts[i])
        parts[i] = re.sub(r"(\$?)([A-Z]{1,3}):(\$?)([A-Z]{1,3})", colrange, s)
    return '"'.join(parts)


def remap_other_formulas(xml, mapping):
    """Formulas on other sheets: only what follows 'Change Log'! is a reference into the Change Log."""
    def inner(m):
        return m.group(1) + re.sub(r"('Change Log'!)([^,)\s<]+)", lambda r: r.group(1) + remap_refs(r.group(2), mapping), m.group(2)) + m.group(3)
    return re.sub(r"(<f\b[^>]*>)(.*?)(</f>)", inner, xml, flags=re.S)


def add_list_validation(xml, column, values, last_row, title):
    """A drop-down list on column rows 2..last_row (a typed list: no comma in a value, at most 255 characters in all)."""
    joined = ",".join(values)
    if any("," in v for v in values) or len(joined) > 255:
        raise Refused("the drop-down list for %s cannot be written (a value contains a comma, or the list is longer than Excel's 255 characters)" % title)
    m = re.search(r'<dataValidations count="(\d+)"', xml)
    if not m:
        raise Refused("the '%s' sheet has no data validations block to add a drop-down to" % SHEET)
    rule = ('<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="%s" error="Choose a %s from the list." sqref="%s2:%s%d"><formula1>"%s"</formula1></dataValidation>'
            % (xl.escape(title), xl.escape(title), column, column, last_row, xl.escape(joined)))
    xml = xml.replace(m.group(0), '<dataValidations count="%d"' % (int(m.group(1)) + 1), 1)
    return xml.replace("</dataValidations>", rule + "</dataValidations>", 1)


def remap_columns_xml(snap):
    """Insert Touches after Type and remove the Claude Code Prompt column, moving every reference with it, so the old E to K move to F to L, the old M stays M and N to X do not move.
    -> (sheet xml, workbook xml, {other part: xml}, [header names in the new order]). Refuses (nothing written) if anything does not fit; checks every cell moved intact before returning."""
    headers = snap.headers
    mapping, ins, new_order = column_plan(headers)
    xml, wb = snap.xml, snap.files["xl/workbook.xml"].decode("utf-8")
    type_letter, last_row = headers[LAYOUT_AFTER], last_row_number(snap.xml)
    before = {}                                                                          # (row, header) -> text, to prove afterwards that nothing was lost
    letter_to_header = {l: h for h, l in headers.items() if h}
    for ref, c in xl.cells_of(xml):
        col_, row_ = split_ref(ref)
        if col_ in letter_to_header and letter_to_header[col_] != LAYOUT_DELETE:
            before[(row_, letter_to_header[col_])] = xl.cell_text(c, snap.sst)

    def do_row(m):
        tag, row_no, body, end = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        cells = xl.cells_of(body)
        if "".join(el for _r, el in cells) != body:
            raise Refused("row %d of '%s' holds something other than cells; the layout change does not apply" % (row_no, SHEET))
        out, style = [], None
        for ref, el in cells:
            col_, _r = split_ref(ref)
            if col_ == type_letter:
                style = (re.search(r'\bs="(\d+)"', el.split(">")[0]) or [None, None])[1]
            new = mapping.get(col_, col_)
            if new is None:
                continue
            out.append((colnum(new), el.replace('r="%s"' % ref, 'r="%s%d"' % (new, row_no), 1)))
        s_attr = (' s="%s"' % style) if style else ""
        if row_no == 1:
            out.append((colnum(ins), '<c r="%s1"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ins, s_attr, xl.escape(LAYOUT_INSERT))))
        else:
            out.append((colnum(ins), '<c r="%s%d"%s/>' % (ins, row_no, s_attr)))
        tag = re.sub(r'\bspans="(\d+):(\d+)"', lambda sm: 'spans="%s:%d"' % (sm.group(1), len(new_order)), tag)
        return tag + "".join(el for _n, el in sorted(out, key=lambda kv: kv[0])) + end

    xml = re.sub(r'(<row r="(\d+)"[^>]*?(?<!/)>)(.*?)(</row>)', do_row, xml, flags=re.S)
    block = re.search(r"<cols>(.*?)</cols>", xml, flags=re.S)
    if not block:
        raise Refused("the '%s' sheet has no <cols> block" % SHEET)
    per = {}
    for m in re.finditer(r"<col\b([^>]*?)/>", block.group(1)):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        for i in range(int(attrs["min"]), int(attrs["max"]) + 1):
            per[i] = {k: v for k, v in attrs.items() if k not in ("min", "max")}
    moved = {}
    for i, attrs in per.items():
        new = mapping.get(letters(i), letters(i))
        if new is not None:
            moved[colnum(new)] = attrs
    moved[colnum(ins)] = {"width": str(TOUCHES_WIDTH), "customWidth": "1"}
    cols = "".join('<col min="%d" max="%d" %s/>' % (i, i, " ".join('%s="%s"' % kv for kv in a.items())) for i, a in sorted(moved.items()))
    xml = xml.replace(block.group(0), "<cols>%s</cols>" % cols, 1)
    xml = re.sub(r'(<(?:conditionalFormatting|dataValidation)\b[^>]*?\bsqref=")([^"]*)"', lambda m: m.group(1) + remap_refs(m.group(2), mapping) + '"', xml)
    xml = re.sub(r"(<formula>)(.*?)(</formula>)", lambda m: m.group(1) + remap_refs(m.group(2), mapping) + m.group(3), xml, flags=re.S)
    xml = re.sub(r'(<(?:dimension|autoFilter)\b[^>]*?\bref=")([^"]*)"', lambda m: m.group(1) + remap_refs(m.group(2), mapping) + '"', xml)
    wb = re.sub(r"(<definedName\b[^>]*>)([^<]*)(</definedName>)", lambda m: m.group(1) + re.sub(r"('%s'!)([^,\s]+)" % re.escape(SHEET), lambda r: r.group(1) + remap_refs(r.group(2), mapping), m.group(2)) + m.group(3), wb)
    xml = add_list_validation(xml, headers[LAYOUT_AFTER], TYPES, last_row, "Type")
    others = {}
    for name, part in snap.paths.items():
        if name == SHEET:
            continue
        old = snap.files[part].decode("utf-8")
        new = remap_other_formulas(old, mapping)
        if new != old:
            others[part] = new
    for part in [xml, wb] + list(others.values()):                                       # never save what Excel could not read
        try:
            ElementTree.fromstring(part.encode("utf-8"))
        except ElementTree.ParseError as e:
            raise Refused("the edited XML is not well-formed (%s); nothing was written" % e)
    new_headers = xl.header_columns(xml, snap.sst)
    if list(sorted(new_headers, key=lambda h: colnum(new_headers[h]))) != new_order:
        raise Refused("the header row does not have the planned order after the change; nothing was written")
    after = {}
    for ref, c in xl.cells_of(xml):
        col_, row_ = split_ref(ref)
        after[(row_, [h for h, l in new_headers.items() if l == col_][0])] = xl.cell_text(c, snap.sst)
    bad = [k for k, v in before.items() if after.get(k) != v]
    if bad or any(after.get((r, LAYOUT_INSERT), "") for r in range(2, last_row + 1)):
        raise Refused("a cell did not move intact (%s ...); nothing was written" % (bad[:3],))
    return xml, wb, others, new_order


# ---- Owner: the default owner of every task (KW-79) -------------------------------------------------------------------------------------------------------------------------
def plan_default_owner(snap):
    """Fill every blank Owner with the default and write the default's own spelling ('N.Wrana' -> 'N. Wrana'); any other name is left as it is. Not stamped. -> (xml, lines, expected)."""
    if "Owner" not in snap.headers:
        raise Refused("the workbook has no 'Owner' column")
    xml, lines, expected = snap.xml, [], {}
    same_person = re.sub(r"[\s.]+", "", DEFAULT_OWNER).lower()
    for kw, row in snap.id_rows():
        cur = snap.values(row).get("Owner", "")
        if cur == DEFAULT_OWNER or (cur and re.sub(r"[\s.]+", "", cur).lower() != same_person):
            continue
        xml = set_cell(xml, snap.headers["Owner"], row, DEFAULT_OWNER)
        lines.append("%s: Owner: %s -> %s" % (kw, cur or "(blank)", DEFAULT_OWNER))
        expected[(SHEET, kw, "Owner")] = DEFAULT_OWNER
    return xml, lines, expected


# ---- QM-001: the Impact column, the register of manual changes, the release gate, entries, backfill and the report (KW-71) ---------------------------------------------------------
QM_COLUMNS = ["Ref", "Date", "KW items", "Manual section / clause", "Change", "Before", "After", "Insert after", "Reason", "Drafted by", "QM Decision", "Decided By", "Decision Date",
              "Drafted In", "Manual Revision", "Entered in Manual (date)"]
QM_WIDTHS = [8, 12, 14, 30, 11, 46, 46, 30, 40, 16, 15, 16, 14, 34, 14, 16]
QM_HUMAN = ["QM Decision", "Decided By", "Decision Date", "Manual Revision", "Entered in Manual (date)"]            # only the Quality Manager writes these (in Excel): the tool never does
QM_CHANGES = ["Replace", "Add", "Delete"]
QM_DECISIONS = ["Approved", "Rejected", "Needs change"]
QM_FIELDS = {"kw_items": "KW items", "section": "Manual section / clause", "change": "Change", "before": "Before", "after": "After", "insert_after": "Insert after", "reason": "Reason"}
QM_CANDIDATES = ("KW-23", "KW-51", "KW-66")                                  # released items that look like they changed the manual: listed first in the report
QM_BACKFILL = "backfill"                                                      # Drafted by: a ref the manual already carries (entered before the register existed): never chased
IMPACT_WIDTH = 46
REF_RE = re.compile(r"\b[BC]\d+\b")


def impact_parts(value):
    """(proposed, class, one line) of a well-formed impact ('Requirement changed - ...', optionally after PROPOSED), else None."""
    v = (value or "").strip()
    proposed = bool(re.match(r"(?i)proposed\s", v))
    if proposed:
        v = v.split(None, 1)[1] if len(v.split(None, 1)) > 1 else ""
    m = re.match(r"^(.+?)\s+-\s+(.+)$", v, re.S)
    if not m:
        return None
    cls = {c.lower(): c for c in IMPACT_CLASSES}.get(m.group(1).strip().lower())
    line = m.group(2).strip()
    return (proposed, cls, line) if cls and line else None


def check_impact(value):
    """Validate a QM-001 Impact and write it in its standard form: '<class> - <one line>' (an unconfirmed backfill proposal starts with PROPOSED)."""
    value = check_text(IMPACT, value)
    parts = impact_parts(value)
    if not parts:
        raise Refused("%s must be '<class> - <one line>' where class is one of: %s (for example 'None - bug fix, no requirement involved'); got '%s'" % (IMPACT, "; ".join(IMPACT_CLASSES), value or "blank"))
    proposed, cls, line = parts
    return ("PROPOSED " if proposed else "") + cls + " - " + line


def table_rows(snap, name, xml=None):
    """(headers {header: column letters}, [(row number, {header: text})]) of any sheet, row 1 being the headers."""
    xml = xml if xml is not None else snap.files[snap.paths[name]].decode("utf-8")
    headers = xl.header_columns(xml, snap.sst)
    by_letter = {l: h for h, l in headers.items() if h}
    rows = {}
    for ref, c in xl.cells_of(xml):
        col_, row_ = split_ref(ref)
        if row_ > 1 and col_ in by_letter:
            rows.setdefault(row_, {})[by_letter[col_]] = xl.cell_text(c, snap.sst).strip()
    return headers, sorted(rows.items())


def register_refs(snap, xml=None):
    """The Refs on the QM-001 Changes sheet, or None when the workbook has no such sheet."""
    if QM_SHEET not in snap.paths:
        return None
    return {r.get("Ref", "").upper() for _n, r in table_rows(snap, QM_SHEET, xml)[1] if r.get("Ref", "")}


def impact_gate(snap, kw, impact, ref):
    """Refuse Released unless the QM-001 impact is recorded, confirmed, and (for a change) backed by a register entry. Raises Refused."""
    if not impact:
        raise Refused("%s cannot be Released without a %s (give it in the same call, for example \"%s=None - bug fix, no requirement involved\")" % (kw, IMPACT, IMPACT))
    parts = impact_parts(impact)
    if not parts:
        raise Refused("%s: %s '%s' is not '<class> - <one line>' (classes: %s)" % (kw, IMPACT, impact[:60], "; ".join(IMPACT_CLASSES)))
    proposed, cls, _line = parts
    if proposed:
        raise Refused("%s cannot be Released: its %s is still PROPOSED (not confirmed). Confirm or correct it first (write it without the word PROPOSED)" % (kw, IMPACT))
    if cls == "None":
        return
    refs = [r.upper() for r in REF_RE.findall(ref or "")]
    if not refs:
        raise Refused("%s cannot be Released: its %s is '%s', so the manual change needs a register entry. Draft it with --qm-entry, then give its Ref as QM-001 Ref" % (kw, IMPACT, cls))
    have = register_refs(snap)
    if have is None:
        raise Refused("%s cannot be Released: the workbook has no '%s' sheet yet. Run: python tools/changelog_write.py --qm-setup" % (kw, QM_SHEET))
    missing = [r for r in refs if r not in have]
    if missing:
        raise Refused("%s cannot be Released: QM-001 Ref %s is not on the '%s' sheet (draft the entry with --qm-entry first)" % (kw, ", ".join(missing), QM_SHEET))


def next_ref(snap, register_xml=None):
    """The next free C-number: one more than the highest C-number on the register or in any item's QM-001 Ref."""
    used = []
    _h, rows = table_rows(snap, SHEET)
    for _n, r in rows:
        used += [int(x) for x in re.findall(r"\bC(\d+)\b", r.get("QM-001 Ref", ""), re.I)]
    if QM_SHEET in snap.paths:
        for _n, r in table_rows(snap, QM_SHEET, register_xml)[1]:
            used += [int(x) for x in re.findall(r"\bC(\d+)\b", r.get("Ref", ""), re.I)]
    return "C%d" % (max(used, default=0) + 1)


def qm_styles(snap):
    """(header, body, human) cell styles taken from the Open Decisions sheet (an Excel-written sheet); None where the workbook has no such sheet."""
    if DECISIONS not in snap.paths:
        return None, None, None
    xml = snap.files[snap.paths[DECISIONS]].decode("utf-8")
    heads = xl.header_columns(xml, snap.sst)
    last = letters(max([colnum(l) for l in heads.values()] or [1]))

    def style_of(ref):
        for r, c in xl.cells_of(xml):
            if r == ref:
                m = re.search(r'\bs="(\d+)"', c.split(">")[0])
                return m.group(1) if m else None
        return None
    return style_of("A1"), style_of("A2"), style_of(last + "2")


def build_qm_sheet(snap):
    """The XML of an empty QM-001 Changes sheet, cloned in structure from the Open Decisions sheet (frozen header, widths, drop-downs on Change and QM Decision)."""
    header_s, _body, _human = qm_styles(snap)
    n = len(QM_COLUMNS)
    s_attr = ' s="%s"' % header_s if header_s else ""
    head = '<row r="1" spans="1:%d" ht="30" customHeight="1">%s</row>' % (n, "".join('<c r="%s1"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (letters(i), s_attr, xl.escape(h)) for i, h in enumerate(QM_COLUMNS, 1)))
    cols = "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i, i, w) for i, w in enumerate(QM_WIDTHS, 1))

    def rule(header, values):
        col_ = letters(QM_COLUMNS.index(header) + 1)
        return ('<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="%s" error="Choose a %s from the list." sqref="%s2:%s500"><formula1>"%s"</formula1></dataValidation>'
                % (xl.escape(header), xl.escape(header), col_, col_, xl.escape(",".join(values))))
    validations = '<dataValidations count="2">%s%s</dataValidations>' % (rule("Change", QM_CHANGES), rule("QM Decision", QM_DECISIONS))
    od = snap.files[snap.paths[DECISIONS]].decode("utf-8") if DECISIONS in snap.paths else ""
    root = re.match(r"(<\?xml[^>]*\?>\s*<worksheet\b[^>]*>)", od)
    root = re.sub(r'\s+xr:uid="[^"]*"', "", root.group(1)) if root else ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                                                                         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">')
    def pick(pat, default):
        m = re.search(pat, od)
        return m.group(0) if m else default
    fmt = pick(r"<sheetFormatPr\b[^>]*/>", '<sheetFormatPr defaultRowHeight="15"/>')
    margins = pick(r"<pageMargins\b[^>]*/>", '<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/>')
    page = pick(r"<pageSetup\b[^>]*/>", "")
    view = ('<sheetViews><sheetView workbookViewId="0"><pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/><selection pane="topRight" activeCell="B1" sqref="B1"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/><selection pane="bottomRight" activeCell="B2" sqref="B2"/></sheetView></sheetViews>')
    return "%s<dimension ref=\"A1:%s1\"/>%s%s<cols>%s</cols><sheetData>%s</sheetData>%s%s%s</worksheet>" % (root, letters(n), view, fmt, cols, head, validations, margins, page)


def add_impact_column_xml(snap):
    """Append the QM-001 Impact column after the last one (so the Summary sheet's whole-column formulas keep their letters). -> (sheet xml, workbook xml)."""
    headers = snap.headers
    named = sorted((colnum(l), h) for h, l in headers.items() if h)
    if [n for n, _h in named] != list(range(1, len(named) + 1)):
        raise Refused("the header row of '%s' has gaps (columns must run A, B, C ... without a blank header)" % SHEET)
    if "QM-001 Ref" not in headers:
        raise Refused("the '%s' sheet has no 'QM-001 Ref' column to copy the style from" % SHEET)
    old_last, new_letter = letters(len(named)), letters(len(named) + 1)
    ref_letter = headers["QM-001 Ref"]
    before = dict(xl.cells_of(snap.xml))

    def do_row(m):
        tag, row_no, body, end = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        cells = xl.cells_of(body)
        if "".join(el for _r, el in cells) != body:
            raise Refused("row %d of '%s' holds something other than cells; the new column does not apply" % (row_no, SHEET))
        style = None
        for ref, el in cells:
            if split_ref(ref)[0] == ref_letter:
                style = (re.search(r'\bs="(\d+)"', el.split(">")[0]) or [None, None])[1]
        s_attr = (' s="%s"' % style) if style else ""
        new = ('<c r="%s1"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (new_letter, s_attr, xl.escape(IMPACT))) if row_no == 1 else '<c r="%s%d"%s/>' % (new_letter, row_no, s_attr)
        tag = re.sub(r'\bspans="(\d+):(\d+)"', lambda sm: 'spans="%s:%d"' % (sm.group(1), len(named) + 1), tag)
        return tag + body + new + end

    xml = re.sub(r'(<row r="(\d+)"[^>]*?(?<!/)>)(.*?)(</row>)', do_row, snap.xml, flags=re.S)
    if colnum(new_letter) not in column_widths(xml):
        block = re.search(r"</cols>", xml)
        if not block:
            raise Refused("the '%s' sheet has no <cols> block" % SHEET)
        xml = xml.replace("</cols>", '<col min="%d" max="%d" width="%d" customWidth="1"/></cols>' % (colnum(new_letter), colnum(new_letter), IMPACT_WIDTH), 1)
    xml = re.sub(r'(<dimension ref="[A-Z]+\d+:)%s(\d+")' % old_last, r"\g<1>%s\g<2>" % new_letter, xml)
    xml = re.sub(r'(<autoFilter\b[^>]*?\bref="[A-Z]+\d+:)%s(\d+")' % old_last, r"\g<1>%s\g<2>" % new_letter, xml)
    wb = re.sub(r"('%s'!\$[A-Z]+\$\d+:\$)%s(\$\d+)" % (re.escape(SHEET), old_last), r"\g<1>%s\g<2>" % new_letter, snap.files["xl/workbook.xml"].decode("utf-8"))
    after = dict(xl.cells_of(xml))
    if any(after.get(ref) != el for ref, el in before.items() if split_ref(ref)[0] != new_letter):
        raise Refused("a cell changed while the column was added; nothing was written")
    return xml, wb


def plan_qm_setup(snap):
    """One time: the 'QM-001 Impact' column (last) and the 'QM-001 Changes' sheet. Refuses if either exists. -> (parts {name: xml}, workbook xml, lines, expected)."""
    if IMPACT in snap.headers:
        raise Refused("the '%s' sheet already has a '%s' column" % (SHEET, IMPACT))
    if QM_SHEET in snap.paths:
        raise Refused("the workbook already has a sheet called '%s'" % QM_SHEET)
    xml, wb = add_impact_column_xml(snap)
    files = snap.files
    sheet_xml = build_qm_sheet(snap)
    nums = [int(x) for name in files for x in re.findall(r"^xl/worksheets/sheet(\d+)\.xml$", name)]
    n = max(nums or [0]) + 1
    part = "xl/worksheets/sheet%d.xml" % n
    rels = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    rid = "rId%d" % (max([int(x) for x in re.findall(r'\bId="rId(\d+)"', rels)] or [0]) + 1)
    sid = max([int(x) for x in re.findall(r'<sheet\b[^>]*?\bsheetId="(\d+)"', wb)] or [0]) + 1
    tags = re.findall(r"<sheet\b[^>]*/>", wb)
    names = [xl.unescape(re.search(r'\bname="([^"]*)"', t).group(1)) for t in tags]
    pos = names.index(DECISIONS) + 1 if DECISIONS in names else len(names)
    if any(int(i) >= pos for i in re.findall(r'\blocalSheetId="(\d+)"', wb)):                  # a sheet-index reference would move: put the new sheet last instead
        pos = len(names)
    new_tag = '<sheet name="%s" sheetId="%d" r:id="%s"/>' % (xl.escape(QM_SHEET), sid, rid)
    wb = wb.replace("</sheets>", new_tag + "</sheets>", 1) if pos == len(names) else wb.replace(tags[pos - 1], tags[pos - 1] + new_tag, 1)
    active = re.search(r'\bactiveTab="(\d+)"', wb)
    if active and int(active.group(1)) >= pos:
        wb = wb.replace(active.group(0), 'activeTab="%d"' % (int(active.group(1)) + 1), 1)
    rels = rels.replace("</Relationships>", '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/></Relationships>' % (rid, n), 1)
    parts = {snap.part: xml, part: sheet_xml, "xl/_rels/workbook.xml.rels": rels}
    if "[Content_Types].xml" in files:
        ct = files["[Content_Types].xml"].decode("utf-8")
        parts["[Content_Types].xml"] = ct.replace("</Types>", '<Override PartName="/%s" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>' % part, 1)
    if "docProps/app.xml" in files:                                                           # the sheet list Excel keeps for itself: kept in step (it is informational)
        app = files["docProps/app.xml"].decode("utf-8")
        titles = re.search(r'<vt:vector size="(\d+)" baseType="lpstr">(.*?)</vt:vector>', app, re.S)
        count = re.search(r"(<vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>)(\d+)(</vt:i4>)", app)
        if titles and count and int(titles.group(1)) == int(count.group(2)):
            items = re.findall(r"<vt:lpstr>.*?</vt:lpstr>", titles.group(2))
            if len(items) == len(names):
                items.insert(pos, "<vt:lpstr>%s</vt:lpstr>" % xl.escape(QM_SHEET))
                app = app.replace(titles.group(0), '<vt:vector size="%d" baseType="lpstr">%s</vt:vector>' % (len(items), "".join(items)), 1)
                app = app.replace(count.group(0), "%s%d%s" % (count.group(1), len(items), count.group(3)), 1)
                parts["docProps/app.xml"] = app
    for text in list(parts.values()) + [wb]:                                                   # never save what Excel could not read
        try:
            ElementTree.fromstring(text.encode("utf-8"))
        except ElementTree.ParseError as e:
            raise Refused("the edited XML is not well-formed (%s); nothing was written" % e)
    lines = ["%s sheet: column '%s' added at the end (%s)" % (SHEET, IMPACT, letters(len(snap.headers) + 1)),
             "sheet '%s' added (%d columns: %s)" % (QM_SHEET, len(QM_COLUMNS), ", ".join(QM_COLUMNS))]
    expected = {(SHEET, "%s1" % letters(len(snap.headers) + 1), ""): IMPACT}
    for i, h in enumerate(QM_COLUMNS, 1):
        expected[(QM_SHEET, "%s1" % letters(i), "")] = h
    return parts, wb, lines, expected


def qm_row_xml(headers, values, row, styles):
    """One register row. Cells of the human-only columns carry the shaded style (the Quality Manager's)."""
    _head, body, human = styles
    widths = {colnum(letters(i)): w for i, w in enumerate(QM_WIDTHS, 1)}
    cells, lines = "", 1
    for h in QM_COLUMNS:
        col_ = headers[h]
        style = human if h in QM_HUMAN else body
        s_attr = ' s="%s"' % style if style else ""
        text = values.get(h, "")
        if text:
            cells += '<c r="%s%d"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (col_, row, s_attr, xl.escape(text))
            lines = max(lines, text_lines(text, widths.get(colnum(col_), 12.0)))
        else:
            cells += '<c r="%s%d"%s/>' % (col_, row, s_attr)
    return '<row r="%d" spans="1:%d" ht="%s" customHeight="1">%s</row>' % (row, len(QM_COLUMNS), row_height(lines, 30.0), cells)


def set_dimension(xml, last_row):
    return re.sub(r'(<dimension ref="[A-Z]+\d+:[A-Z]+)(\d+)(")', lambda m: m.group(1) + str(last_row) + m.group(3), xml, count=1)


def kw_list(value, ids):
    """'KW-99; KW-98' or ['KW-99'] -> 'KW-98; KW-99' (each must be on the Change Log)."""
    found = re.findall(r"KW-\d+", " ".join(value) if isinstance(value, list) else str(value or ""))
    if not found:
        raise Refused("kw_items must name at least one change-log item (for example [\"KW-99\"])")
    missing = [k for k in found if k not in ids]
    if missing:
        raise Refused("%s is not an item on the '%s' sheet" % (", ".join(missing), SHEET))
    return "; ".join(sorted(set(found), key=lambda k: int(k[3:])))


def qm_entry_values(fields, ids, current=None):
    """Validate the fields of a register entry (merged over `current` when revising). -> {header: text}."""
    unknown = [k for k in fields if k not in QM_FIELDS and k != "ref"]
    if unknown:
        raise Refused("a register entry takes only: ref (to revise one), %s (got %s)" % (", ".join(QM_FIELDS), ", ".join(unknown)))
    values = dict(current or {})
    for key, header in QM_FIELDS.items():
        if key in fields:
            values[header] = kw_list(fields[key], ids) if key == "kw_items" else check_text(header, fields[key] if isinstance(fields[key], str) else "" if fields[key] is None else str(fields[key]))
    for need, key in (("KW items", "kw_items"), ("Manual section / clause", "section"), ("Change", "change"), ("Reason", "reason")):
        if not values.get(need):
            raise Refused("a register entry needs %s" % key)
    hit ={c.lower(): c for c in QM_CHANGES}.get(values["Change"].lower())
    if not hit:
        raise Refused("change must be one of: %s (got '%s')" % ("; ".join(QM_CHANGES), values["Change"]))
    values["Change"] = hit
    before, after, anchor = values.get("Before", ""), values.get("After", ""), values.get("Insert after", "")
    if hit == "Replace" and (not before or not after or before == after):
        raise Refused("a Replace needs 'before' (the current wording) and a different 'after' (the new wording)")
    if hit == "Delete" and (not before or after):
        raise Refused("a Delete needs 'before' (the wording to remove) and no 'after'")
    if hit == "Add" and (not after or before or not anchor):
        raise Refused("an Add needs 'after' (the new wording) and 'insert_after' (the start of the paragraph it goes after), and no 'before'")
    if hit != "Add" and anchor:
        raise Refused("insert_after is only for an Add")
    return values


def plan_qm_entry(snap, xml, fields, by, today, ids):
    """Add (or, with ref, revise) one entry on the register sheet. -> (new xml, ref, row, 'added'|'revised', line, expected {(sheet, row, header): text})."""
    headers, rows = table_rows(snap, QM_SHEET, xml)
    for h in QM_COLUMNS:
        if h not in headers:
            raise Refused("the '%s' sheet has no '%s' column" % (QM_SHEET, h))
    ref = str(fields.get("ref", "")).strip().upper()
    if ref:
        hit = [(n, r) for n, r in rows if r.get("Ref", "").upper() == ref]
        if not hit:
            raise Refused("%s is not on the '%s' sheet" % (ref, QM_SHEET))
        row, old = hit[0]
        held = [h for h in ("QM Decision", "Drafted In", "Manual Revision", "Entered in Manual (date)") if old.get(h)]
        if held:
            raise Refused("%s cannot be revised: it already has %s (a decided or drafted entry is not changed: raise a new entry)" % (ref, ", ".join(held)))
        values = qm_entry_values({k: v for k, v in fields.items() if k != "ref"}, ids, current={h: old.get(h, "") for h in QM_FIELDS.values()})
        changed = {h: v for h, v in values.items() if old.get(h, "") != v}
        if not changed:
            return xml, ref, row, "unchanged", "%s: unchanged" % ref, {}
        for h, v in changed.items():
            xml = set_cell(xml, headers[h], row, v)
        return xml, ref, row, "revised", "%s: revised (%s)" % (ref, "; ".join("%s: %s -> %s" % (h, (old.get(h, "")[:40] or "(blank)"), v[:50]) for h, v in changed.items())), {(QM_SHEET, row, h): v for h, v in changed.items()}
    values = qm_entry_values(fields, ids)
    for _n, r in rows:
        if all(r.get(h, "") == values.get(h, "") for h in ("KW items", "Manual section / clause", "Change", "Before", "After", "Insert after")):
            raise Refused("duplicate: %s already holds this change (revise it with ref, or change the wording)" % r.get("Ref", "?"))
    ref = next_ref(snap, xml)
    values.update({"Ref": ref, "Date": today, "Drafted by": by})
    row = last_row_number(xml) + 1
    xml = xml.replace("</sheetData>", qm_row_xml(headers, values, row, qm_styles(snap)) + "</sheetData>", 1)
    xml = set_dimension(xml, row)
    return xml, ref, row, "added", "%s: new register entry for %s (%s: %s)" % (ref, values["KW items"], values["Change"], values["Manual section / clause"][:50]), {(QM_SHEET, row, h): v for h, v in values.items() if v}


def read_json(path, what):
    try:
        with open(path, encoding="utf-8-sig") as f:                      # (a file written by Windows PowerShell starts with a byte-order mark)
            return json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("%s %s could not be read (%s)" % (what, path, e))


def plan_qm_entries_file(snap, path, today):
    """--qm-entry <file>: one entry (an object) or several (a list). -> (parts, lines, expected)."""
    if QM_SHEET not in snap.paths:
        raise Refused("the workbook has no '%s' sheet yet. Run: python tools/changelog_write.py --qm-setup" % QM_SHEET)
    spec = read_json(path, "the entry file")
    entries = spec if isinstance(spec, list) else [spec]
    if not entries or not all(isinstance(e, dict) for e in entries):
        raise Refused("an entry file is one JSON object (or a list of them): kw_items, section, change, before, after, insert_after, reason")
    ids = {i for i, _r in snap.id_rows()}
    xml = snap.files[snap.paths[QM_SHEET]].decode("utf-8")
    lines, expected = [], {}
    for e in entries:
        xml, _ref, _row, _what, line, exp = plan_qm_entry(snap, xml, e, "Claude Code", today, ids)
        lines.append(line)
        expected.update(exp)
    return {snap.paths[QM_SHEET]: xml}, lines, expected


def plan_qm_backfill(snap, path):
    """--qm-backfill <file>: proposed / confirmed impacts for Released items (only where blank) and register rows for the refs the manual already carries. -> (parts, lines, expected)."""
    if IMPACT not in snap.headers or QM_SHEET not in snap.paths:
        raise Refused("the workbook needs the '%s' column and the '%s' sheet first. Run: python tools/changelog_write.py --qm-setup" % (IMPACT, QM_SHEET))
    spec = read_json(path, "the backfill file")
    if not isinstance(spec, dict) or not isinstance(spec.get("impacts"), list):
        raise Refused("a backfill file is {\"impacts\": [{\"id\": \"KW-23\", \"impact\": \"PROPOSED None - ...\"}], \"register\": [{\"ref\": \"C10\", \"kw_items\": [...], \"date\": \"2026-10-20\"}]}")
    xml, lines, expected, problems, seen = snap.xml, [], {}, [], set()
    for item in spec["impacts"]:
        kw = item.get("id") if isinstance(item, dict) else None
        if not isinstance(kw, str) or kw in seen:
            raise Refused("every impact needs an id, and an id may appear once (got %r)" % (item,))
        seen.add(kw)
        row = snap.row_of(kw, xml)
        current = snap.values(row, xml)
        if current.get("Status") != "Released":
            problems.append("%s is '%s', not Released (the backfill is only for Released items)" % (kw, current.get("Status", "")))
            continue
        if current.get(IMPACT, ""):
            lines.append("%s: already has an impact (skipped)" % kw)
            continue
        value = check_impact(item.get("impact", ""))
        xml = set_cell(xml, snap.headers[IMPACT], row, value)
        expected[(SHEET, kw, IMPACT)] = value
        lines.append("%s: %s: %s" % (kw, IMPACT, value[:90]))
    if problems:
        raise Refused("; ".join(problems))
    parts = {snap.part: xml}
    qxml = snap.files[snap.paths[QM_SHEET]].decode("utf-8")
    headers, rows = table_rows(snap, QM_SHEET, qxml)
    have = {r.get("Ref", "").upper() for _n, r in rows}
    ids = {i for i, _r in snap.id_rows()}
    for item in spec.get("register", []):
        ref = str(item.get("ref", "")).strip().upper() if isinstance(item, dict) else ""
        if not re.fullmatch(r"[BC]\d+", ref):
            raise Refused("a register row needs a ref like C10 or B11 (got %r)" % (item,))
        if ref in have:
            lines.append("%s: already on the register (skipped)" % ref)
            continue
        date = str(item.get("date", "")).strip()
        values = {"Ref": ref, "Date": date, "KW items": kw_list(item.get("kw_items", []), ids), "Manual section / clause": "see the manual's change list",
                  "Reason": check_text("reason", item.get("reason", "")) or "Entered in the manual before this register existed: see the manual's change list", "Drafted by": QM_BACKFILL}
        row = last_row_number(qxml) + 1
        qxml = qxml.replace("</sheetData>", qm_row_xml(headers, values, row, qm_styles(snap)) + "</sheetData>", 1)
        qxml = set_dimension(qxml, row)
        have.add(ref)
        for h, v in values.items():
            if v:
                expected[(QM_SHEET, row, h)] = v
        lines.append("%s: register row (backfill) for %s" % (ref, values["KW items"]))
    parts[snap.paths[QM_SHEET]] = qxml
    return parts, lines, expected


def plan_qm_drafted(snap, refs, text):
    """--qm-drafted C21,C22 --text <draft file and date>: note which draft revision an APPROVED entry went into (the generator calls this). -> (parts, lines, expected)."""
    if QM_SHEET not in snap.paths:
        raise Refused("the workbook has no '%s' sheet yet" % QM_SHEET)
    text = check_text("Drafted In", text)
    if not text:
        raise Refused("--qm-drafted needs --text (the draft revision file name and date)")
    xml = snap.files[snap.paths[QM_SHEET]].decode("utf-8")
    headers, rows = table_rows(snap, QM_SHEET, xml)
    by_ref = {r.get("Ref", "").upper(): (n, r) for n, r in rows}
    lines, expected = [], {}
    for ref in [x.strip().upper() for x in str(refs).split(",") if x.strip()]:
        if ref not in by_ref:
            raise Refused("%s is not on the '%s' sheet" % (ref, QM_SHEET))
        row, r = by_ref[ref]
        if r.get("QM Decision", "").lower() != "approved":
            raise Refused("%s is not Approved by the Quality Manager (QM Decision is '%s'): only an approved entry goes into a draft revision" % (ref, r.get("QM Decision", "") or "blank"))
        if r.get("Entered in Manual (date)", ""):
            raise Refused("%s is already entered in the manual" % ref)
        if r.get("Drafted In", "") == text:
            continue
        xml = set_cell(xml, headers["Drafted In"], row, text)
        expected[(QM_SHEET, row, "Drafted In")] = text
        lines.append("%s: Drafted In: %s -> %s" % (ref, r.get("Drafted In", "") or "(blank)", text))
    return {snap.paths[QM_SHEET]: xml}, lines, expected


def qm_report(snap):
    """The QM-001 status report as text lines (read-only): released items with no confirmed impact, entries waiting for the Quality Manager, approved entries not yet in the manual."""
    out = ["QM-001 status report (KelpWorks change log, %s)" % datetime.date.today().isoformat(), ""]
    if IMPACT not in snap.headers or QM_SHEET not in snap.paths:
        return out + ["The workbook does not have the '%s' column and the '%s' sheet yet (run --qm-setup)." % (IMPACT, QM_SHEET)]
    _h, items = table_rows(snap, SHEET)
    released = [(r.get("ID", ""), r) for _n, r in items if r.get("Status") == "Released"]
    open_ = []
    for kw, r in released:
        parts = impact_parts(r.get(IMPACT, ""))
        if not parts or parts[0]:
            open_.append((kw, r, "PROPOSED, not yet confirmed" if parts else "no impact recorded"))
    open_.sort(key=lambda t: (t[0] not in QM_CANDIDATES, t[0]))
    out.append("1. Released items with no confirmed QM-001 impact: %d of %d" % (len(open_), len(released)))
    for kw, r, why in open_:
        out.append("   %s%s  %s  [%s]" % (kw, " (candidate)" if kw in QM_CANDIDATES else "", (r.get("Module", "") + " / " + r.get("Section", ""))[:60], why))
    _hh, rows = table_rows(snap, QM_SHEET)
    real = [(n, r) for n, r in rows if r.get("Drafted by", "").lower() != QM_BACKFILL]
    historic = [r for n, r in rows if r.get("Drafted by", "").lower() == QM_BACKFILL]
    waiting = [r for n, r in real if not r.get("QM Decision")]
    out += ["", "2. Register entries waiting for the Quality Manager's decision: %d" % len(waiting)]
    out += ["   %s  %s  %s: %s" % (r.get("Ref", ""), r.get("KW items", ""), r.get("Change", ""), r.get("Manual section / clause", "")[:60]) for r in waiting]
    approved = [r for n, r in real if r.get("QM Decision", "").lower() == "approved" and not r.get("Entered in Manual (date)")]
    undrafted = [r for r in approved if not r.get("Drafted In")]
    drafted = [r for r in approved if r.get("Drafted In")]
    out += ["", "3. Approved entries not yet in the manual: %d (%d not yet in a draft revision, %d drafted)" % (len(approved), len(undrafted), len(drafted))]
    out += ["   %s  %s  %s  [not yet drafted]" % (r.get("Ref", ""), r.get("KW items", ""), r.get("Manual section / clause", "")[:50]) for r in undrafted]
    out += ["   %s  %s  %s  [drafted in: %s]" % (r.get("Ref", ""), r.get("KW items", ""), r.get("Manual section / clause", "")[:50], r.get("Drafted In", "")) for r in drafted]
    other = [r for n, r in real if r.get("QM Decision", "").lower() in ("rejected", "needs change")]
    out += ["", "4. Rejected or 'needs change' entries (for information): %d" % len(other)]
    out += ["   %s  %s  %s" % (r.get("Ref", ""), r.get("KW items", ""), r.get("QM Decision", "")) for r in other]
    out += ["", "%d register row(s) were entered in the manual before the register existed (Drafted by: %s); they are not chased." % (len(historic), QM_BACKFILL)]
    return out


# ---- the Claude Code Playbook sheet (written from docs/backlog/claude-code-playbook.json, cell by cell) -----------------------------------------------------------------
def load_playbook(path):
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("the playbook file %s could not be read (%s)" % (path, e))
    ok = (isinstance(spec, dict) and isinstance(spec.get("steps"), list) and all(isinstance(r, list) and len(r) == 4 and all(isinstance(c, str) for c in r) for r in spec["steps"])
          and isinstance(spec.get("assumptions"), list) and all(isinstance(t, str) for t in spec["assumptions"]) and isinstance(spec.get("note"), str))
    if not ok:
        raise Refused("the playbook file %s needs steps (rows of 4 texts), assumptions (texts) and note (text)" % path)
    for t in [c for r in spec["steps"] for c in r] + spec["assumptions"] + [spec["note"]]:
        check_text("playbook text", t)
    return {"steps": [[c.strip() for c in r] for r in spec["steps"]], "assumptions": [t.strip() for t in spec["assumptions"]], "note": spec["note"].strip()}


def plan_playbook(snap, spec):
    """Write the steps, assumptions and note into the cells the sheet already has (the numbers of rows must match). -> (xml, lines, expected)."""
    if PLAYBOOK not in snap.paths:
        raise Refused("the workbook has no sheet called '%s'" % PLAYBOOK)
    xml = snap.files[snap.paths[PLAYBOOK]].decode("utf-8")
    text = {ref: xl.cell_text(c, snap.sst).strip() for ref, c in xl.cells_of(xml)}
    rows = sorted({split_ref(r)[1] for r in text})

    def cell(col_, row_):
        return text.get("%s%d" % (col_, row_), "")

    steps, r = [], 2
    while cell("A", r) and not cell("A", r).startswith(("Assumptions", "Note")):
        steps.append(r)
        r += 1
    head = [x for x in rows if cell("A", x) == "Assumptions"]
    note = [x for x in rows if cell("A", x) == "Note"]
    if not head or not note:
        raise Refused("the '%s' sheet has no 'Assumptions' and 'Note' rows in column A" % PLAYBOOK)
    assumptions, r = [], head[0] + 1
    while cell("B", r):
        assumptions.append(r)
        r += 1
    for what, have, want in (("steps", len(steps), len(spec["steps"])), ("assumptions", len(assumptions), len(spec["assumptions"]))):
        if have != want:
            raise Refused("the playbook file has %d %s but the sheet has %d rows for them (the numbers must match)" % (want, what, have))
    widths = column_widths(xml)
    writes = {}
    for row_, values in zip(steps, spec["steps"]):
        for col_, v in zip("ABCD", values):
            writes["%s%d" % (col_, row_)] = v
    for row_, v in zip(assumptions, spec["assumptions"]):
        writes["B%d" % row_] = v
    writes["B%d" % note[0]] = spec["note"]
    lines, expected = [], {}
    for ref, value in writes.items():
        if text.get(ref, "") != value:
            col_, row_ = split_ref(ref)
            xml = set_cell(xml, col_, row_, value)
            expected[(PLAYBOOK, ref, "")] = value
            lines.append("%s!%s: %s -> %s" % (PLAYBOOK, ref, (text.get(ref, "")[:40] or "(blank)"), value[:60]))
    for row_ in steps + assumptions + note:
        cells = {split_ref(ref)[0]: (writes.get(ref) or text.get(ref, "")) for ref in set(writes) | set(text) if split_ref(ref)[1] == row_}
        height = row_height(max([1] + [text_lines(v, widths.get(colnum(c), 12.0)) for c, v in cells.items()]))
        xml = re.sub(r'(<row r="%d"[^>]*?)>' % row_, lambda m: re.sub(r'\s+(?:ht|customHeight)="[^"]*"', "", m.group(1)) + ' ht="%s" customHeight="1">' % height, xml, count=1)
    return xml, lines, expected


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
        if h in METADATA and out[h]:                                      # a blank value is reported as missing (with the lists), not as invalid
            try:
                out[h] = check_value(h, out[h], "Touches" in snap.headers)
            except Refused as e:
                raise Reject(str(e))
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
    missing = [h for h in NEW_ITEM_REQUIRED + (["Touches"] if "Touches" in snap.headers else []) if not fields.get(h)]
    if missing:
        hint = ""
        if "Touches" in snap.headers and ("Type" in missing or "Touches" in missing):
            hint = " (Type is exactly one of: %s. Touches is one or more of: %s, separated by semicolons)" % ("; ".join(TYPES), "; ".join(TOUCHES))
        raise Reject("a new item needs %s%s" % (", ".join(missing), hint))
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
    if "Owner" in snap.headers:
        values["Owner"] = DEFAULT_OWNER
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


def do_qm_entry(c, note, sender):
    snap, work = c.snap, c.work
    if QM_SHEET not in snap.paths:
        raise Reject("the workbook has no '%s' sheet yet (run --qm-setup)" % QM_SHEET)
    fields = note.get("fields")
    if not isinstance(fields, dict) or not fields or not all(isinstance(v, (str, list)) for v in fields.values()):
        raise Reject("'fields' must be an object: kw_items, section, change, before, after, insert_after, reason (and ref to revise an entry that has no decision yet)")
    try:
        xml, ref, row, what, line, exp = plan_qm_entry(snap, work.sheet(QM_SHEET), fields, sender, c.today, c.ids)
    except Refused as e:
        raise Reject(str(e))
    if what == "unchanged":
        c.res.update(status="unchanged", id=ref, reason="the sheet already says this")
        return
    work.xml[QM_SHEET] = xml
    c.expected.update(exp)
    c.lines.append("%s (%s)" % (line, c.res["file"]))
    c.res.update(status="applied", id=ref, reason="register entry %s %s at row %d of '%s'" % (ref, what, row, QM_SHEET))


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
            handler = {"new_item": do_new_item, "revision": do_revision, "new_decision": do_new_decision, "plan_cells": do_plan_cells, "qm_entry": do_qm_entry}.get(note.get("kind"))
            if not handler:
                raise Reject("kind must be 'new_item', 'revision', 'new_decision', 'plan_cells' or 'qm_entry'")
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


PUBLISH_TRIES = 5                                                  # OneDrive briefly locks the file it has just synced: try again before giving up (KW-68)
PUBLISH_DELAY = 6.0                                                # seconds between tries: about 30 s in all


def busy(error):
    """A lock that goes away by itself (access denied / sharing violation), not a missing folder or a full disk."""
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) in (5, 32)


def publish(master, snapshot):
    """Copy the master over the read-only SharePoint copy (temp file + replace, retried while the file is busy), set it read-only and compare checksums. Raises OSError."""
    folder = os.path.dirname(os.path.abspath(snapshot))
    if not os.path.isdir(folder):
        raise OSError("the SharePoint folder was not found: %s" % folder)
    fd, tmp = tempfile.mkstemp(prefix=".publish_", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(master, tmp)
        tries = 0
        while True:
            tries += 1
            try:
                if os.path.exists(snapshot):
                    os.chmod(snapshot, stat.S_IREAD | stat.S_IWRITE)
                os.replace(tmp, snapshot)
                break
            except OSError as e:
                if not busy(e):
                    raise
                if tries >= PUBLISH_TRIES:
                    raise OSError("%s (still busy after %d tries over about %d s)" % (e, tries, PUBLISH_DELAY * (tries - 1)))
                print("(the SharePoint copy is busy, probably OneDrive syncing: try %d of %d failed, trying again in %d s)" % (tries, PUBLISH_TRIES, PUBLISH_DELAY))
                time.sleep(PUBLISH_DELAY)
        os.chmod(snapshot, stat.S_IREAD)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        if os.path.exists(snapshot):
            try:
                os.chmod(snapshot, stat.S_IREAD)                       # never leave the read-only copy writable
            except OSError:
                pass
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
            known = {i.filename for i in snap.infos}
            for name in files:                                          # a part that did not exist before (the QM-001 Changes sheet)
                if name not in known:
                    z.writestr(name, files[name])
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


VALUE_OPTIONS = ("--file", "--id", "--batch", "--show", "--verify-wait", "--decision", "--by", "--text", "--needed", "--restore", "--rules-file", "--playbook-file", "--qm-entry", "--qm-backfill",
                 "--qm-drafted")
FLAG_OPTIONS = ("--dry-run", "--apply-inbox", "--publish", "--backups", "--migrate", "--rules-sheet", "--classify-setup", "--default-owner", "--playbook-sheet", "--qm-setup", "--qm-report")


def parse_args(argv):
    a = {"file": cp.configured_path(), "file_given": False, "dry": False, "wait": VERIFY_WAIT, "id": None, "pairs": [], "batch": None, "show": None, "decision": None, "by": None, "text": None,
         "needed": None, "restore": None, "apply_inbox": False, "publish": False, "backups": False, "migrate": False, "rules_sheet": False, "rules_file": RULES_FILE,
         "classify_setup": False, "default_owner": False, "playbook_sheet": False, "playbook_file": PLAYBOOK_FILE, "qm_setup": False, "qm_report": False, "qm_entry": None, "qm_backfill": None,
         "qm_drafted": None}
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


def refresh_inbox_readme(inbox):
    """Inbox/README_NOTE_FORMAT.txt is the tool's own text: write it when it is missing or differs. -> True if it was written. Raises OSError."""
    target = os.path.join(inbox, "README_NOTE_FORMAT.txt")
    try:
        with open(target, encoding="utf-8") as f:
            if f.read() == NOTE_FORMAT:
                return False
    except FileNotFoundError:
        pass
    with open(target, "w", encoding="utf-8") as f:
        f.write(NOTE_FORMAT)
    return True


def tidy_inbox_readme(master):
    inbox = cp.inbox_for(master)
    if not inbox or not os.path.isdir(inbox):
        return
    try:
        if refresh_inbox_readme(inbox):
            print("Inbox README_NOTE_FORMAT.txt updated to the tool's current text.")
    except OSError as e:
        print("WARNING: the inbox README could not be updated (%s)." % e)


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
    tidy_inbox_readme(path)
    publish_qm_report(path, snapshot)
    return 0


QM_REPORT_NAME = "QM-001_Status_Report.txt"


def publish_qm_report(path, snapshot):
    """Write the QM-001 status report beside the SharePoint copy so it is never stale. A failure is a warning, not an error (the workbook is already published)."""
    try:
        snap = Snapshot(path)
        if IMPACT not in snap.headers or QM_SHEET not in snap.paths:
            return
        target = os.path.join(os.path.dirname(os.path.abspath(snapshot)), QM_REPORT_NAME)
        if os.path.exists(target):
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(qm_report(snap)) + "\n")
        os.chmod(target, stat.S_IREAD)
        print("QM-001 status report updated: %s" % target)
    except (ValueError, OSError) as e:
        print("WARNING: the QM-001 status report could not be written (%s)." % e)


def show(snap, kw):
    try:
        row = snap.values(snap.row_of(kw))
    except Refused as e:
        print("Refused: %s" % e)
        return 2
    for h in ("ID", "Type", "Touches", "Status", "Owner", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date", "QM-001 Ref", IMPACT, "Row Updated", "Updated By", "Notes"):
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
    modes = ([m for m in ("id", "batch", "show", "decision", "restore", "qm_entry", "qm_backfill", "qm_drafted") if a and a[m]]
             + [m for m in ("apply_inbox", "publish", "backups", "migrate", "rules_sheet", "classify_setup", "default_owner", "playbook_sheet", "qm_setup", "qm_report") if a and a[m]])
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
    if a["show"] or a["publish"] or a["qm_report"]:
        try:
            snap = Snapshot(path)
        except (ValueError, OSError) as e:
            print("STOPPED: %s" % e)
            return 6
        if a["show"]:
            return show(snap, a["show"])
        if a["qm_report"]:
            print("\n".join(qm_report(snap)))
            return 0
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
            if writes:
                tidy_inbox_readme(path)
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
        if a["classify_setup"]:
            xml, wb_xml, others, new_order = remap_columns_xml(snap)
            parts = dict(others)
            parts[snap.part] = xml
            lines = ["%s sheet: '%s' added after '%s' (column %s), '%s' removed, the old columns E to K moved to F to L, a drop-down on '%s'; every cell, rule, drop-down and formula moved with them"
                     % (SHEET, LAYOUT_INSERT, LAYOUT_AFTER, letters(new_order.index(LAYOUT_INSERT) + 1), LAYOUT_DELETE, LAYOUT_AFTER)]
            expected = {(SHEET, "%s1" % letters(i + 1), ""): h for i, h in enumerate(new_order)}
            return commit(a, snap, parts, wb_xml, lines, expected, "classify-setup")
        if a["qm_setup"]:
            parts, wb_xml, lines, expected = plan_qm_setup(snap)
            return commit(a, snap, parts, wb_xml, lines, expected, "qm-setup")
        if a["qm_entry"]:
            parts, lines, expected = plan_qm_entries_file(snap, a["qm_entry"], datetime.date.today().isoformat())
            return commit(a, snap, parts, None, lines, expected, "qm-entry")
        if a["qm_backfill"]:
            parts, lines, expected = plan_qm_backfill(snap, a["qm_backfill"])
            return commit(a, snap, parts, None, lines, expected, "qm-backfill")
        if a["qm_drafted"]:
            parts, lines, expected = plan_qm_drafted(snap, a["qm_drafted"], a["text"] or "")
            return commit(a, snap, parts, None, lines, expected, "qm-drafted")
        if a["default_owner"]:
            xml, lines, expected = plan_default_owner(snap)
            return commit(a, snap, {snap.part: xml}, None, lines, expected, "default-owner")
        if a["playbook_sheet"]:
            xml, lines, expected = plan_playbook(snap, load_playbook(a["playbook_file"]))
            return commit(a, snap, {snap.paths[PLAYBOOK]: xml}, None, lines, expected, "playbook-sheet")
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
            refresh_inbox_readme(inbox)
        except OSError as e:
            print("WARNING: the inbox could not be set up (%s)" % e)
    code = finish_publish(master)
    print("Edit the master from now on: %s" % master)
    return code


if __name__ == "__main__":
    sys.exit(main())
