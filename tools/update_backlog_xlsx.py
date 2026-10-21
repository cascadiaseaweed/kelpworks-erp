#!/usr/bin/env python3
"""Update the KelpWorks change-log workbook (the master list the team keeps in SharePoint): a decision's Decided By / Decision on the Open Decisions sheet.

**The Change Log sheet is NOT written here any more** (KW-61): its columns have owners, items need approval before they start and the file must be written safely, all of which
tools/changelog_write.py enforces. An update with an "id" is refused and points there.

Standard library only. The workbook is edited INSIDE the .xlsx file (only the cells named below change), so its formatting, drop-down lists, formulas, filters and SharePoint
properties are left exactly as they are; the Summary counts are recalculated the next time Excel opens it. Close the workbook in Excel first (an open file is locked).

    python tools/update_backlog_xlsx.py "<workbook>" updates.json [--dry-run]

updates.json is a list of decisions:

    [{"decision": "KW-05", "by": "N. Wrana (2026-10-19)", "text": "Make optional (keep fields and history)"}]

Afterwards refresh the CSV snapshot with `python tools/export_backlog.py "<workbook>"`.
"""
import json
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_path                                                             # the ONE place that says where the change log is (KW-62)

NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DECISION_COLUMNS = {"by": "Decided By", "text": "Decision"}


def col_letters(ref):
    return re.match(r"[A-Z]+", ref).group(0)


def unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def sheet_paths(files):
    """{sheet name: path of its XML part}"""
    wb = files["xl/workbook.xml"].decode("utf-8")
    rels = dict(re.findall(r'<Relationship\b[^>]*?\bId="([^"]+)"[^>]*?\bTarget="([^"]+)"', files["xl/_rels/workbook.xml.rels"].decode("utf-8")))
    rels.update({a: b for b, a in re.findall(r'<Relationship\b[^>]*?\bTarget="([^"]+)"[^>]*?\bId="([^"]+)"', files["xl/_rels/workbook.xml.rels"].decode("utf-8"))})
    out = {}
    for tag in re.findall(r"<sheet\b[^>]*/>", wb):
        name = unescape(re.search(r'\bname="([^"]*)"', tag).group(1))
        rid = re.search(r'\br:id="([^"]+)"', tag).group(1)
        target = rels[rid].lstrip("/")
        out[name] = target if target.startswith("xl/") else "xl/" + target
    return out


def shared_strings(files):
    if "xl/sharedStrings.xml" not in files:
        return []
    xml = files["xl/sharedStrings.xml"].decode("utf-8")
    return [unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))) for si in re.findall(r"<si>.*?</si>|<si/>", xml, flags=re.S)]


def cell_text(cell_xml, sst):
    t = re.search(r'\bt="([^"]+)"', cell_xml.split(">")[0])
    if t and t.group(1) == "s":
        v = re.search(r"<v>(\d+)</v>", cell_xml)
        return sst[int(v.group(1))] if v else ""
    if t and t.group(1) == "inlineStr":
        return unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", cell_xml)))
    v = re.search(r"<v>([^<]*)</v>", cell_xml)
    return unescape(v.group(1)) if v else ""


def cells_of(sheet_xml):
    """[(ref, whole <c> element)] in document order"""
    return [(m.group(1), m.group(0)) for m in re.finditer(r'<c r="([A-Z]+\d+)"(?:\s+[^>]*?)?(?:/>|>.*?</c>)', sheet_xml, flags=re.S)]


def header_columns(sheet_xml, sst):
    return {cell_text(c, sst): col_letters(ref) for ref, c in cells_of(sheet_xml) if re.fullmatch(r"[A-Z]+1", ref)}


def id_rows(sheet_xml, sst, id_col):
    return {cell_text(c, sst): int(ref[len(id_col):]) for ref, c in cells_of(sheet_xml) if re.fullmatch(id_col + r"\d+", ref) and ref != id_col + "1"}


def set_text(sheet_xml, ref, text):
    """Replace one existing cell with a text cell (same style); the cell must already exist in the sheet (all editable cells in the workbook do)."""
    pat = re.compile(r'<c r="%s"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:/>|>.*?</c>)' % ref, flags=re.S)
    m = pat.search(sheet_xml)
    if not m:
        raise ValueError("cell %s is not in the sheet" % ref)
    attrs = re.sub(r'\s+t="[^"]*"', "", m.group(1))
    if text in (None, ""):
        new = '<c r="%s"%s/>' % (ref, attrs)
    else:
        new = '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, attrs, escape(str(text)))
    return sheet_xml[:m.start()] + new + sheet_xml[m.end():]


def apply_updates(files, updates):
    """files = {part name: bytes}. Returns (changed parts, messages). Raises ValueError on an unknown id / status / sheet."""
    paths, sst = sheet_paths(files), shared_strings(files)
    msgs, changed = [], {}
    for u in updates:
        if "id" in u:
            raise ValueError("%s: the Change Log sheet is written only with tools/changelog_write.py (it checks column ownership, approval and stale files)" % u["id"])
        elif "decision" in u:
            sheet, key, cols = "Open Decisions", u["decision"], DECISION_COLUMNS
            fields = {k: u[k] for k in cols if k in u}
        else:
            raise ValueError("an update needs a 'decision' (Open Decisions row): %r" % (u,))
        if sheet not in paths:
            raise ValueError("the workbook has no sheet called '%s'" % sheet)
        part = paths[sheet]
        xml = changed.get(part) or files[part].decode("utf-8")
        headers = header_columns(xml, sst)
        id_col = headers.get("ID")
        if not id_col:
            raise ValueError("'%s' has no ID column in row 1" % sheet)
        row = id_rows(xml, sst, id_col).get(key)
        if not row:
            raise ValueError("%s is not on the '%s' sheet" % (key, sheet))
        for field, text in fields.items():
            col = headers.get(cols[field])
            if not col:
                raise ValueError("'%s' has no '%s' column" % (sheet, cols[field]))
            xml = set_text(xml, "%s%d" % (col, row), text)
            msgs.append("%s: %s -> %s" % (key, cols[field], (str(text)[:60] + "...") if len(str(text)) > 60 else text))
        changed[part] = xml
    return changed, msgs


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in argv
    if len(args) != 2 or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 1
    path, spec = args
    if not dry:                                                                           # a dry run may look at any copy; a write only goes to the change log itself
        try:
            changelog_path.check_writable(path)
        except ValueError as e:
            print("Nothing changed: %s" % e)
            return 2
    with open(spec, encoding="utf-8") as f:
        updates = json.load(f)
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        files = {i.filename: z.read(i.filename) for i in infos}
    try:
        changed, msgs = apply_updates(files, updates)
    except ValueError as e:
        print("Nothing changed: %s" % e)
        return 2
    wb = files["xl/workbook.xml"].decode("utf-8")
    if "fullCalcOnLoad" not in wb:                       # the Summary counts recalculate when Excel opens the file
        wb = re.sub(r"<calcPr\b([^>]*?)/>", r'<calcPr\1 fullCalcOnLoad="1"/>', wb) if re.search(r"<calcPr\b", wb) else wb.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
    files["xl/workbook.xml"] = wb.encode("utf-8")
    for part, xml in changed.items():
        files[part] = xml.encode("utf-8")
    for m in msgs:
        print(m)
    if dry:
        print("(dry run: the workbook was not written)")
        return 0
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for i in infos:
                z.writestr(i.filename, files[i.filename])
        os.replace(tmp, path)
    except PermissionError:
        os.remove(tmp)
        print("The workbook is locked (open in Excel?). Close it and run this again.")
        return 3
    print("updated %d cell(s) in %s" % (len(msgs), os.path.basename(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
