#!/usr/bin/env python3
"""The ONLY way Claude Code updates the KelpWorks change log (LOG_KelpWorks_ChangeControl.xlsx, kept in SharePoint: QAQC > KelpWorks ERP > Change_Control). Standard library only.

    python tools/changelog_write.py --id KW-46 Status=Released "PR #=75" "Released Date=2026-10-21" Notes="what changed" [--dry-run]
    python tools/changelog_write.py --batch updates.json          # [{"id": "KW-46", "set": {"Status": "Released", "PR #": "75"}}, ...]  (one wait, one check)
    python tools/changelog_write.py --show KW-46                  # read-only: the row's Status, Approved to Start, PR #, ...

The workbook is found with --file, the environment variable KELPWORKS_CHANGELOG, or the default path below. What it enforces (the 'Workflow Rules' sheet of the workbook):

* Ownership: Claude Code may write only Status, Notes, PR #, Released Date and QM-001 Ref (Row Updated and Updated By are stamped automatically). Every other column belongs to the
  intake chat or to a human, and is refused. It never adds rows or IDs.
* Status: Not started, In progress, On staging, Released or Blocked. Deferred and Won't do are a human's decision and are refused; so is moving an item OUT of them.
  In progress / On staging / Released need Approved to Start = Y or Pre-rule. Released also needs a PR # and a Released Date (given now or already in the row).
* Safe write: it stops if Excel has the workbook open (a ~$ lock file), reads the file fresh and records its modified time and checksum, finds rows by ID (never by row number),
  re-checks the time and checksum just before saving (aborts if either changed), saves atomically changing only the named cells, then waits about 60 seconds, re-opens the file and
  confirms every value stuck. If something was reverted it says so and exits with an error: it never retries on its own.

Exit codes: 0 done, 1 usage, 2 refused by a rule (nothing written), 3 workbook open / locked, 4 the file changed while this ran (nothing written), 5 a value did not stick (revert),
6 the workbook could not be read. Nothing but the named cells (and the two stamps) changes: formatting, drop-down lists and the Summary sheet are left as they are.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_backlog_xlsx as xl                                                  # the XML helpers (sheet lookup, cells, escaping)
import changelog_path                                                             # the ONE place that says where the change log is (KW-62)

DEFAULT_PATH = changelog_path.DEFAULT_PATH
SHEET = "Change Log"
OWNED = ["Status", "Notes", "PR #", "Released Date", "QM-001 Ref"]
STAMPS = {"Row Updated": lambda: datetime.date.today().isoformat(), "Updated By": lambda: "Claude Code"}
STATUSES = ["Not started", "In progress", "On staging", "Released", "Blocked"]
HUMAN_STATUSES = ["Deferred", "Won't do"]
NEEDS_APPROVAL = ("In progress", "On staging", "Released")
APPROVED = ("y", "pre-rule")
HUMAN_COLUMNS = {"Owner", "Approved to Start", "Verified By (on live)", "Verified Date"}
INTAKE_COLUMNS = {"ID", "Module", "Section", "Type", "Change Request", "Context / Rationale", "Transcript Time", "Raised By", "Priority", "Lens", "Claude Code Mode",
                  "Claude Code Prompt", "Acceptance Criteria"}
PR_RE = re.compile(r"^\d+(\s*(,|&|and|/)\s*\d+)*$")


class Refused(Exception):
    """A rule of the workflow stops this update (exit 2)."""


class Stale(Exception):
    pass


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
        paths = xl.sheet_paths(self.files)
        if SHEET not in paths:
            raise ValueError("the workbook has no sheet called '%s'" % SHEET)
        self.part = paths[SHEET]
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

    def row_of(self, kw):
        """The sheet row of an ID (by looking the ID up, never by position). Refuses an unknown or a repeated ID."""
        id_col = self.headers["ID"]
        hits = [int(ref[len(id_col):]) for ref, c in xl.cells_of(self.xml) if re.fullmatch(id_col + r"\d+", ref) and ref != id_col + "1" and xl.cell_text(c, self.sst).strip() == kw]
        if not hits:
            raise Refused("%s is not on the '%s' sheet (this tool never adds rows or IDs)" % (kw, SHEET))
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


def lock_files(path):
    """Excel (and LibreOffice) leave a lock file beside a workbook that is open."""
    folder, name = os.path.split(os.path.abspath(path))
    found = []
    for f in os.listdir(folder or "."):
        low = f.lower()
        if low.startswith("~$"):
            rest = f[2:]
            if name.lower().endswith(rest.lower()) or "kelpworks_changecontrol" in rest.lower():          # (Excel may shorten the name: ~$lpWorks_...; any KelpWorks_ChangeControl lock stops us)
                found.append(f)
        elif low.startswith(".~lock.") and name.lower() in low:
            found.append(f)
    return found


# ---- the rules -------------------------------------------------------------------------------------------------------------------------------------------------------------
def canonical(snap, column):
    h = snap.by_lower.get(column.strip().lower())
    if not h:
        raise Refused("the workbook has no '%s' column (columns: %s)" % (column, ", ".join(sorted(snap.headers))))
    return h


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
    elif re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise Refused("%s contains control characters" % column)
    elif len(value) > 30000:
        raise Refused("%s is too long (limit 30,000 characters)" % column)
    return value


def plan_update(snap, xml, kw, setting):
    """Validate one item's update against the row as it is now and return (new xml, [(header, old, new)], expected {header: text}). Raises Refused."""
    if not setting:
        raise Refused("%s: nothing to write (give column=value pairs)" % kw)
    row = snap.row_of(kw)
    current = snap.values(row, xml)
    new = {}
    for column, value in setting.items():
        h = canonical(snap, column)
        if h in STAMPS:
            raise Refused("%s is stamped automatically; do not set it" % h)
        if h not in OWNED:
            who = "a human" if h in HUMAN_COLUMNS else ("the intake chat" if h in INTAKE_COLUMNS else "someone else")
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
    return xml, changes, {(kw, h): text for h, text in new.items()}


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
    def colnum(letters):
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        return n
    body, pos = rm.group(1), None
    for cm in re.finditer(r'<c r="([A-Z]+)\d+"', body):
        if colnum(cm.group(1)) > colnum(col):
            pos = cm.start()
            break
    new_body = (body[:pos] + cell("") + body[pos:]) if pos is not None else body + cell("")
    return xml[:rm.start(1)] + new_body + xml[rm.end(1):]


# ---- saving and verifying ---------------------------------------------------------------------------------------------------------------------------------------------
def save(snap, new_xml):
    files = dict(snap.files)
    files[snap.part] = new_xml.encode("utf-8")
    wb = files["xl/workbook.xml"].decode("utf-8")
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
    except PermissionError:
        os.remove(tmp)
        raise


def verify(path, expected):
    """Re-open the saved workbook and compare every written cell. -> [(id, header, wanted, found)] of the values that did not stick."""
    snap = Snapshot(path)
    bad = []
    for (kw, h), want in expected.items():
        try:
            found = snap.values(snap.row_of(kw)).get(h, "")
        except Refused:
            found = "(row not found)"
        if found != want:
            bad.append((kw, h, want, found))
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


def parse_args(argv):
    a = {"file": changelog_path.configured_path(), "dry": False, "wait": 60.0, "id": None, "pairs": [], "batch": None, "show": None}
    i = 0
    while i < len(argv):
        t = argv[i]
        if t in ("-h", "--help"):
            return None
        if t == "--dry-run":
            a["dry"] = True
        elif t in ("--file", "--id", "--batch", "--show", "--verify-wait"):
            i += 1
            if i >= len(argv):
                return None
            v = argv[i]
            if t == "--verify-wait":
                a["wait"] = float(v)
            else:
                a[t[2:]] = v
        elif t.startswith("--"):
            return None
        else:
            a["pairs"].append(t)
        i += 1
    return a


def main(argv=None):
    a = parse_args(list(sys.argv[1:] if argv is None else argv))
    if a is None or not (a["id"] or a["batch"] or a["show"]):
        print(__doc__)
        return 1
    path = a["file"]
    if not os.path.isfile(path):
        print("STOPPED: the workbook was not found: %s" % path)
        return 6
    if not a["show"] and not a["dry"]:                                                                  # reading and dry runs may use any copy; a WRITE only ever goes to the change log itself
        try:
            changelog_path.check_writable(path)
        except ValueError as e:
            print("Refused: %s\nNothing was written." % e)
            return 2
    locks = lock_files(path)
    try:
        snap = Snapshot(path)
    except (ValueError, OSError) as e:
        print("STOPPED: %s" % e)
        return 6
    if a["show"]:
        try:
            row = snap.values(snap.row_of(a["show"]))
        except Refused as e:
            print("Refused: %s" % e)
            return 2
        for h in ("ID", "Status", "Owner", "Approved to Start", "PR #", "Released Date", "Verified By (on live)", "Verified Date", "QM-001 Ref", "Row Updated", "Updated By", "Notes"):
            if h in row:
                print("%-24s %s" % (h, (row[h][:200] + "...") if len(row[h]) > 200 else row[h]))
        return 0
    if locks:
        print("STOPPED: the workbook is open in Excel (lock file %s). Close it everywhere (desktop and browser) and run this again. Nothing was written." % locks[0])
        return 3
    try:
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
    except (Refused, ValueError, OSError, json.JSONDecodeError) as e:
        print("Refused: %s\nNothing was written." % e)
        return 2
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
        save(snap, xml)
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
        print("Stopped. Do not retry blindly: close every open copy of the workbook, then run it again.")
        return 5
    print("VERIFIED: every value is still in the file after %d s." % a["wait"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
