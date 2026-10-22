#!/usr/bin/env python3
"""XML helpers for reading and editing cells inside the KelpWorks change-log workbook (an .xlsx is a zip of XML), used by tools/changelog_write.py. Standard library only.

**This is a library now (KW-66).** It no longer writes the workbook: the change log has ONE writer, tools/changelog_write.py (item updates, Open Decisions with `--decision`, the inbox,
backups and the read-only SharePoint copy). Running this file only prints that.
"""
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape




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


def main(argv):
    print("tools/update_backlog_xlsx.py does not write the workbook any more (KW-66).\n"
          "Use tools/changelog_write.py: --id KW-nn Status=... for an item, --decision KW-nn --by \"...\" --text \"...\" for an Open Decisions answer.")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
