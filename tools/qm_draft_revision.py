#!/usr/bin/env python3
"""Generate a DRAFT revision of the Quality Manual (QM-001) from the register entries the Quality Manager has approved (KW-71). Standard library only.

    python tools/qm_draft_revision.py --rev 5.2 [--manual <a draft copy of the manual>] [--file <workbook>] [--no-mark] [--dry-run]

Reads the 'QM-001 Changes' sheet of the change-log workbook (read-only) and takes every entry whose QM Decision is Approved and that is not yet entered in the manual. It writes, into the
DRAFTS folder only (tools/changelog_path.py, KELPWORKS_QM_DRAFTS) and always as new files that are never overwritten:

  QM-001_Rev<rev>_change_list_DRAFT_<date>.docx   the change list (section / current text / proposed text / why / items / decision) and a proposed Revision History line
  QM-001_Rev<rev>_DRAFT_<date>.docx               with --manual: a copy of the manual in which each approved change is a Word TRACKED CHANGE (author "KelpWorks draft"), so the Quality
                                                  Manager accepts or rejects each one in Word

A change is applied to the manual copy only when its 'before' wording is found exactly once, inside one paragraph made of ordinary text runs (formatting is kept; quotes, dashes, spacing and
non-breaking spaces are matched loosely). An addition goes in as a new tracked paragraph after the paragraph that starts with the entry's 'insert after' text. Anything else (not found, found
more than once, a complex paragraph, a second change in the same paragraph) is NOT applied and is listed in the change list under "Apply by hand" with the reason. The cover (revision, status,
effective date) and every other document-control field are never touched: the Quality Manager owns them.

Safety: the manual must be inside the drafts folder and have DRAFT in its file name (the controlled copy is never used by accident: --force-source overrides the name check only); the source file is
never modified (its checksum is compared before and after); the result is re-read: every XML part is well-formed, "reject all" reproduces the source text exactly and "accept all" contains every
proposed wording. Afterwards the register is told which draft each entry went into (changelog_write.py --qm-drafted; skipped with --no-mark or when the workbook is not the master).
"""
import datetime
import hashlib
import os
import re
import sys
import tempfile
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_path as cp
import changelog_write as cw

AUTHOR = "KelpWorks draft"
W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
PARA = re.compile(r"<w:p(?:\s[^>]*)?>((?:(?!<w:p[\s>]).)*?)</w:p>", re.S)                                  # a paragraph that holds no other paragraph
TOKEN = re.compile(r"<w:pPr>.*?</w:pPr>|<w:pPr/>|<w:r(?:\s[^>]*)?>.*?</w:r>|<w:(?:bookmarkStart|bookmarkEnd|proofErr)\b[^>]*/>", re.S)
RUN = re.compile(r"^<w:r(?:\s[^>]*)?>(.*)</w:r>$", re.S)
RUN_BODY = re.compile(r"^(<w:rPr>.*?</w:rPr>)?((?:<w:t(?:\s[^>]*)?>[^<]*</w:t>|<w:t/>)*)$", re.S)
TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
QUOTES = {"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "−": "-", " ": " ", " ": " ", " ": " "}


class Stop(Exception):
    """The generator cannot go on (the message says why)."""


def ascii_text(s):
    return str(s).encode("ascii", "replace").decode("ascii")


# ---- matching text -------------------------------------------------------------------------------------------------------------------------------------------------------
def normalize(text):
    """-> (normalized string, [(start, end) of the original characters each normalized character stands for]). Quotes and dashes are made plain, white space becomes one space."""
    chars, spans, i = [], [], 0
    while i < len(text):
        ch = QUOTES.get(text[i], text[i])
        if ch.isspace():
            j = i
            while j < len(text) and QUOTES.get(text[j], text[j]).isspace():
                j += 1
            chars.append(" ")
            spans.append((i, j))
            i = j
        else:
            chars.append(ch)
            spans.append((i, i + 1))
            i += 1
    return "".join(chars), spans


def norm_key(text):
    return normalize(str(text or "").strip())[0].strip()


def para_text(inner):
    return "".join(cw.xl.unescape(t) for t in TEXT.findall(inner))


def parse_paragraph(inner):
    """The paragraph as ordinary runs, or None when it holds anything this tool will not edit (fields, drawings, hyperlinks, existing tracked changes ...).
    -> {tokens: [(kind, xml)], runs: [{index, rpr, text}]}"""
    tokens = TOKEN.findall(inner)
    if "".join(tokens) != inner:
        return None
    out, runs = [], []
    for t in tokens:
        if t.startswith(("<w:pPr", "<w:book", "<w:proofErr")):
            out.append(("other", t))
            continue
        m = RUN.match(t)
        body = RUN_BODY.match(m.group(1)) if m else None
        if not body:
            return None
        runs.append({"index": len(out), "rpr": body.group(1) or "", "text": "".join(cw.xl.unescape(x) for x in TEXT.findall(body.group(2)))})
        out.append(("run", t))
    return {"tokens": out, "runs": runs}


def paragraphs(xml):
    return [(m.start(), m.end(), m.group(1), m.group(0)) for m in PARA.finditer(xml)]


# ---- finding every change in the manual ------------------------------------------------------------------------------------------------------------------------------------
def locate(xml, entry):
    """Where an entry goes in the ORIGINAL manual. -> ('replace'|'delete', paragraph index, original span) | ('add', paragraph index) | ('skip', reason)."""
    paras = paragraphs(xml)
    texts = [para_text(inner) for _s, _e, inner, _w in paras]
    if entry["Change"] == "Add":
        anchor = norm_key(entry.get("Insert after", ""))
        hits = [i for i, t in enumerate(texts) if anchor and norm_key(t).startswith(anchor)]
        if len(hits) != 1:
            return ("skip", "the anchor paragraph (starting '%s') was %s" % (ascii_text(anchor[:50]), "not found" if not hits else "found %d times" % len(hits)))
        return ("add", hits[0])
    before = norm_key(entry.get("Before", ""))
    if not before:
        return ("skip", "no current wording to look for")
    if "\n" in str(entry.get("Before", "")).strip():
        return ("skip", "the current wording spans several paragraphs")
    found = []
    for i, t in enumerate(texts):
        norm, spans = normalize(t)
        start = 0
        while True:
            at = norm.find(before, start)
            if at < 0:
                break
            found.append((i, spans[at][0], spans[at + len(before) - 1][1]))
            start = at + max(1, len(before))
    if not found:
        return ("skip", "the current wording was not found in the manual (it may have changed since the entry was drafted)")
    if len(found) > 1:
        return ("skip", "the current wording was found %d times: it cannot be placed" % len(found))
    i, s, e = found[0]
    return ("replace" if entry["Change"] == "Replace" else "delete", i, (s, e))


# ---- writing tracked changes ----------------------------------------------------------------------------------------------------------------------------------------------
class Ids:
    def __init__(self, xml):
        self.n = max([int(x) for x in re.findall(r'\bw:id="(\d+)"', xml)] + [0]) + 1000

    def next(self):
        self.n += 1
        return self.n


def run_xml(rpr, text, deleted=False):
    tag = "w:delText" if deleted else "w:t"
    parts = text.split("\n")
    body = "<w:br/>".join('<%s xml:space="preserve">%s</%s>' % (tag, escape(p), tag) for p in parts)
    return "<w:r>%s%s</w:r>" % (rpr, body)


def tracked(kind, ids, date, inner):
    return '<w:%s w:id="%d" w:author="%s" w:date="%s">%s</w:%s>' % (kind, ids.next(), AUTHOR, date, inner, kind)


def edit_paragraph(paragraph_xml, inner, parsed, span, after, ids, date):
    """The paragraph with [span) of its text deleted (as a tracked deletion) and `after` inserted in its place (as a tracked insertion). None when the span cannot be cut cleanly."""
    s, e = span
    pos, overlapping = 0, []
    for r in parsed["runs"]:
        r["start"], r["end"] = pos, pos + len(r["text"])
        pos = r["end"]
        if r["start"] < e and r["end"] > s:
            overlapping.append(r)
    if not overlapping:
        return None
    first, last = overlapping[0]["index"], overlapping[-1]["index"]
    if any(kind != "run" for kind, _x in parsed["tokens"][first:last + 1]):
        return None                                                                       # something sits between the runs to be cut: do not guess
    out = []
    for i, (kind, xml) in enumerate(parsed["tokens"]):
        if i < first or i > last:
            out.append(xml)
            continue
        if i == first:
            run = overlapping[0]
            if s > run["start"]:
                out.append(run_xml(run["rpr"], run["text"][:s - run["start"]]))
            dels = []
            for r in overlapping:
                dels.append(run_xml(r["rpr"], r["text"][max(s, r["start"]) - r["start"]:min(e, r["end"]) - r["start"]], deleted=True))
            out.append(tracked("del", ids, date, "".join(dels)))
            if after:
                out.append(tracked("ins", ids, date, run_xml(run["rpr"], after)))
        if i == last:
            run = overlapping[-1]
            if e < run["end"]:
                out.append(run_xml(run["rpr"], run["text"][e - run["start"]:]))
    head = re.match(r"<w:p(?:\s[^>]*)?>", paragraph_xml).group(0)
    return head + "".join(out) + "</w:p>"


def new_paragraph(anchor_xml, anchor_parsed, text, ids, date):
    """A new paragraph (tracked as inserted, including its paragraph mark) shaped like the anchor paragraph."""
    ppr = next((x for k, x in anchor_parsed["tokens"] if x.startswith("<w:pPr")), "")
    ppr = re.sub(r"<w:rPr>.*?</w:rPr>", "", ppr, flags=re.S)
    mark = '<w:rPr><w:ins w:id="%d" w:author="%s" w:date="%s"/></w:rPr>' % (ids.next(), AUTHOR, date)
    if ppr.endswith("</w:pPr>"):
        at = len(ppr) - len("</w:pPr>")
        for later in ("<w:sectPr", "<w:pPrChange"):                                       # the paragraph-mark properties come before these in the schema's order
            if ppr.find(later) != -1:
                at = min(at, ppr.find(later))
        ppr = ppr[:at] + mark + ppr[at:]
    else:
        ppr = "<w:pPr>%s</w:pPr>" % mark
    rpr = anchor_parsed["runs"][0]["rpr"] if anchor_parsed["runs"] else ""
    return "<w:p>%s%s</w:p>" % (ppr, tracked("ins", ids, date, run_xml(rpr, text)))


def build_tracked(xml, entries, date):
    """Apply every entry that can be placed. -> (new document.xml, [{ref, applied, reason}])."""
    paras = paragraphs(xml)
    ids = Ids(xml)
    plan, results, used = [], [], {}
    for order, entry in enumerate(entries):
        ref = entry.get("Ref", "")
        where = locate(xml, entry)
        if where[0] == "skip":
            results.append({"ref": ref, "applied": False, "reason": where[1]})
            continue
        p = where[1]
        parsed = parse_paragraph(paras[p][2])
        if parsed is None:
            results.append({"ref": ref, "applied": False, "reason": "the paragraph holds fields, drawings, links or earlier tracked changes: edit it by hand"})
            continue
        if where[0] != "add":
            if p in used:
                results.append({"ref": ref, "applied": False, "reason": "another change (%s) already edits the same paragraph: apply this one by hand after accepting that one" % used[p]})
                continue
            used[p] = ref
        plan.append((paras[p][1] if where[0] == "add" else paras[p][0], order, where, entry, parsed, p))
    edits = []
    for _pos, order, where, entry, parsed, p in sorted(plan, key=lambda t: (t[0], t[1]), reverse=True):
        s_, e_, inner, whole = paras[p]
        if where[0] == "add":
            lines = [x for x in str(entry.get("After", "")).split("\n") if x.strip()]
            added = "".join(new_paragraph(whole, parsed, line.strip(), ids, date) for line in lines)
            edits.append((e_, e_, added, entry, "added after the paragraph starting '%s'" % ascii_text(norm_key(entry.get("Insert after", ""))[:40])))
        else:
            new = edit_paragraph(whole, inner, parsed, where[2], "" if where[0] == "delete" else str(entry.get("After", "")).strip(), ids, date)
            if new is None:
                results.append({"ref": entry.get("Ref", ""), "applied": False, "reason": "the wording cannot be cut cleanly out of its formatting: edit it by hand"})
                continue
            edits.append((s_, e_, new, entry, "%s in place" % ("deleted" if where[0] == "delete" else "replaced")))
    out = xml
    for s_, e_, text, entry, how in edits:                                                # from the end of the document backwards, so earlier positions stay valid
        out = out[:s_] + text + out[e_:]
        results.append({"ref": entry.get("Ref", ""), "applied": True, "reason": how})
    order_of = {e.get("Ref", ""): i for i, e in enumerate(entries)}
    results.sort(key=lambda r: order_of.get(r["ref"], 0))
    return out, results


# ---- checking the result ---------------------------------------------------------------------------------------------------------------------------------------------------
def texts_of(xml, mode):
    """The text of every paragraph as it reads with every tracked change rejected ('reject'), accepted ('accept') or as it is ('plain')."""
    out = []
    for _s, _e, inner, whole in paragraphs(xml):
        inserted_mark = bool(re.search(r"<w:pPr>(?:(?!</w:pPr>).)*?<w:rPr><w:ins\b[^>]*/>", inner, re.S))
        if mode == "reject":
            if inserted_mark:
                continue                                                                  # a whole paragraph the tool added
            inner = re.sub(r"<w:ins\b[^>]*[^/]>.*?</w:ins>", "", inner, flags=re.S)
            inner = inner.replace("<w:delText", "<w:t").replace("</w:delText>", "</w:t>")
        elif mode == "accept":
            inner = re.sub(r"<w:del\b[^>]*[^/]>.*?</w:del>", "", inner, flags=re.S)
        out.append(para_text(inner))
    return out


def check_result(old_xml, new_xml, results, entries):
    """Raise Stop unless the new document is well-formed, rejecting every change gives the old text back and accepting them all contains every proposed wording."""
    try:
        ElementTree.fromstring(new_xml.encode("utf-8"))
    except ElementTree.ParseError as e:
        raise Stop("the edited document is not well-formed XML (%s); nothing was written" % e)
    if texts_of(new_xml, "reject") != texts_of(old_xml, "plain"):
        raise Stop("rejecting every tracked change does not give the original text back; nothing was written")
    accepted = "\n".join(norm_key(t) for t in texts_of(new_xml, "accept"))
    by_ref = {e.get("Ref", ""): e for e in entries}
    for r in results:
        if r["applied"] and by_ref[r["ref"]]["Change"] in ("Replace", "Add"):
            for line in [x for x in str(by_ref[r["ref"]].get("After", "")).split("\n") if x.strip()]:
                if norm_key(line) not in accepted:
                    raise Stop("%s: accepting the tracked changes does not contain its proposed wording; nothing was written" % r["ref"])


# ---- the change list (a small .docx written by hand) ---------------------------------------------------------------------------------------------------------------------
def cell(text, width, bold=False, shade=None):
    paras = "".join('<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:rPr>%s<w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                    % ("<w:b/>" if bold else "", escape(line)) for line in (str(text).split("\n") or [""]))
    fill = '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade if shade else ""
    return '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>' % (width, fill, paras)


def table(headers, widths, rows):
    borders = "".join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="808080"/>' % side for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join('<w:gridCol w:w="%d"/>' % w for w in widths)
    head = '<w:tr><w:trPr><w:tblHeader/></w:trPr>%s</w:tr>' % "".join(cell(h, w, bold=True, shade="D9E2F3") for h, w in zip(headers, widths))
    body = "".join("<w:tr>%s</w:tr>" % "".join(cell(v, w) for v, w in zip(row, widths)) for row in rows)
    return '<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>%s</w:tblBorders><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>%s</w:tblGrid>%s%s</w:tbl>' % (sum(widths), borders, grid, head, body)


def para(text, size=22, bold=False, after=120):
    return ('<w:p><w:pPr><w:spacing w:before="0" w:after="%d"/></w:pPr><w:r><w:rPr>%s<w:sz w:val="%d"/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (after, "<w:b/>" if bold else "", size, escape(text)))


def build_change_list(rev, entries, results, today, manual_name, workbook_name):
    applied = {r["ref"] for r in results if r["applied"]}
    by_hand = [(e, r["reason"]) for e in entries for r in results if r["ref"] == e["Ref"] and not r["applied"]]
    refs = ", ".join(e["Ref"] for e in entries)
    items = "; ".join(sorted({k.strip() for e in entries for k in e["KW items"].split(";") if k.strip()}, key=lambda k: int(k[3:]) if k[3:].isdigit() else 0))
    body = [para("QM-001 Quality Manual - Liquid Seaweed Extracts", 32, True, 60),
            para("Proposed Rev %s change list (working document, not controlled)" % rev, 24, True, 120),
            para("Generated %s by tools/qm_draft_revision.py from the QM-001 Changes register (%s): %d entr%s approved by the Quality Manager and not yet entered in the manual. "
                 "Nothing in the controlled manual has been changed." % (today, workbook_name, len(entries), "y" if len(entries) == 1 else "ies"), 20),
            para("Summary", 24, True, 60),
            para("%d change(s): %s. KelpWorks items: %s. %s" % (len(entries), refs, items,
                 ("Tracked changes were written into %s for %d of them; %d must be applied by hand (listed below)." % (manual_name, len(applied), len(by_hand))) if manual_name else
                 "No manual copy was given, so no tracked-change copy was made: every change is applied by hand from this list."), 20),
            para("Proposed Revision History line", 24, True, 60),
            para("Rev %s | %s | Changes %s (KelpWorks items %s) | approved by the Quality Manager; prepared with the QM-001 Changes register" % (rev, today, refs, items), 20),
            para("Changes", 24, True, 60)]
    rows = [[e["Ref"], e["Manual section / clause"], e["Change"], e.get("Before", ""), e.get("After", "") or "(deleted)", e["Reason"], e["KW items"],
             "%s, %s" % (e.get("Decided By", "") or "?", e.get("Decision Date", "") or "?"), "in the tracked copy" if e["Ref"] in applied else "by hand"] for e in entries]
    widths = [700, 1700, 800, 2600, 2600, 2200, 900, 1300, 1000]
    body.append(table(["Ref", "Section", "Change", "Current text", "Proposed text", "Why", "Items", "Decision", "Applied"], widths, rows))
    if by_hand:
        body.append(para("", 12, False, 120))
        body.append(para("Apply by hand", 24, True, 60))
        body.append(table(["Ref", "Section", "Why it was not applied"], [700, 2600, 10900], [[e["Ref"], e["Manual section / clause"], reason] for e, reason in by_hand]))
    sect = '<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/><w:pgMar w:top="900" w:right="720" w:bottom="900" w:left="720" w:header="500" w:footer="500" w:gutter="0"/></w:sectPr>'
    document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>%s%s</w:body></w:document>' % (W_NS, "".join(body), sect)
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "word/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles %s><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>' % W_NS,
        "word/document.xml": document}
    for text in files.values():
        ElementTree.fromstring(text.encode("utf-8"))
    return files


# ---- files -------------------------------------------------------------------------------------------------------------------------------------------------------------------
def unique_path(folder, name):
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, "%s-%d%s" % (stem, n, ext))
    return path


def write_zip(path, members):
    """members = [(name, bytes)] in order. Written beside the target and moved into place (never overwrites)."""
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=".qm_draft_", dir=os.path.dirname(path))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in members:
                z.writestr(name, data)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def inside(folder, path):
    folder, path = os.path.normcase(os.path.realpath(folder)), os.path.normcase(os.path.realpath(path))
    return os.path.commonpath([folder, path]) == folder


def approved_entries(snap):
    """The register entries to draft: approved by the Quality Manager, not yet entered in the manual, not a backfilled historical row."""
    if cw.QM_SHEET not in snap.paths:
        raise Stop("the workbook has no '%s' sheet yet (run changelog_write.py --qm-setup)" % cw.QM_SHEET)
    rows = cw.table_rows(snap, cw.QM_SHEET)[1]
    out = []
    for _n, r in rows:
        if r.get("QM Decision", "").lower() == "approved" and not r.get("Entered in Manual (date)") and r.get("Drafted by", "").lower() != cw.QM_BACKFILL and r.get("Ref"):
            out.append(r)
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {"rev": None, "manual": None, "file": None, "mark": True, "dry": False, "force_source": False}
    i = 0
    while i < len(argv):
        t = argv[i]
        if t in ("--rev", "--manual", "--file"):
            i += 1
            if i >= len(argv):
                print(__doc__)
                return 1
            opts[t[2:]] = argv[i]
        elif t == "--no-mark":
            opts["mark"] = False
        elif t == "--dry-run":
            opts["dry"] = True
        elif t == "--force-source":
            opts["force_source"] = True
        else:
            print(__doc__)
            return 1
        i += 1
    if not opts["rev"] or not re.fullmatch(r"[0-9][0-9A-Za-z.\-]{0,11}", opts["rev"]):
        print("STOPPED: give the revision the draft is for, for example --rev 5.2\n")
        print(__doc__)
        return 1
    path = opts["file"] or cp.configured_path()
    try:
        if not os.path.isfile(path):
            raise Stop("the workbook was not found: %s" % path)
        snap = cw.Snapshot(path)
        entries = approved_entries(snap)
        drafts = cp.drafts_folder()
        manual = opts["manual"]
        if manual:
            if not os.path.isfile(manual):
                raise Stop("the manual copy was not found: %s" % manual)
            if not os.path.isdir(drafts) or not inside(drafts, manual):
                raise Stop("the manual must be a draft copy inside the drafts folder (%s); the controlled copy is never used" % drafts)
            if "draft" not in os.path.basename(manual).lower() and not opts["force_source"]:
                raise Stop("'%s' does not have DRAFT in its file name: use a draft copy of the manual (or --force-source if you are sure it is one)" % os.path.basename(manual))
        if not entries:
            print("Nothing to draft: no register entry is Approved and not yet entered in the manual.")
            return 0
        if not os.path.isdir(drafts):
            raise Stop("the drafts folder was not found: %s" % drafts)
        today, now = datetime.date.today().isoformat(), datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results, source_sha = [], None
        if manual:
            source_sha = sha_of(manual)
            with zipfile.ZipFile(manual) as z:
                members = [(i.filename, z.read(i.filename)) for i in z.infolist()]
            names = [n for n, _b in members]
            if "word/document.xml" not in names:
                raise Stop("'%s' is not a Word document (no word/document.xml)" % os.path.basename(manual))
            old_xml = dict(members)["word/document.xml"].decode("utf-8")
            new_xml, results = build_tracked(old_xml, entries, now)
            check_result(old_xml, new_xml, results, entries)
        else:
            results = [{"ref": e["Ref"], "applied": False, "reason": "no manual copy was given: apply it by hand from the change list"} for e in entries]
        rev_safe = re.sub(r"[^0-9A-Za-z.\-]", "", opts["rev"])
        list_path = unique_path(drafts, "QM-001_Rev%s_change_list_DRAFT_%s.docx" % (rev_safe, today))
        manual_path = unique_path(drafts, "QM-001_Rev%s_DRAFT_%s.docx" % (rev_safe, today)) if manual else None
        print("%d approved entr%s not yet in the manual: %s" % (len(entries), "y" if len(entries) == 1 else "ies", ", ".join(e["Ref"] for e in entries)))
        for r in results:
            print("  %s: %s%s" % (r["ref"], "applied (%s)" % r["reason"] if r["applied"] else "NOT applied: ", "" if r["applied"] else ascii_text(r["reason"])))
        if opts["dry"]:
            print("(dry run: nothing was written; would write %s%s)" % (os.path.basename(list_path), (" and " + os.path.basename(manual_path)) if manual_path else ""))
            return 0
        files = build_change_list(opts["rev"], entries, results, today, os.path.basename(manual_path) if manual_path else "", os.path.basename(path))
        write_zip(list_path, [(n, files[n].encode("utf-8")) for n in ("[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/styles.xml", "word/document.xml")])
        print("written: %s" % list_path)
        if manual_path:
            members = []
            with zipfile.ZipFile(manual) as z:
                for info in z.infolist():
                    members.append((info.filename, new_xml.encode("utf-8") if info.filename == "word/document.xml" else z.read(info.filename)))
            write_zip(manual_path, members)
            if sha_of(manual) != source_sha:
                raise Stop("the source manual changed while this ran (its checksum differs): check %s" % manual)
            print("written: %s (%d of %d change(s) as tracked changes)" % (manual_path, sum(1 for r in results if r["applied"]), len(results)))
    except Stop as e:
        print("STOPPED: %s" % e)
        return 2
    except (cw.Refused, ValueError, OSError, zipfile.BadZipFile) as e:
        print("STOPPED: %s" % e)
        return 2
    if opts["mark"]:
        text = "%s (%s)" % (os.path.basename(manual_path or list_path), today)
        try:
            cp.check_writable(path)
        except ValueError:
            print("(the register was not marked: %s is not the change-log master; run changelog_write.py --qm-drafted %s --text \"%s\" on the master)" % (os.path.basename(path), ",".join(e["Ref"] for e in entries), text))
            return 0
        code = cw.main(["--file", path, "--qm-drafted", ",".join(e["Ref"] for e in entries), "--text", text])
        if code:
            print("WARNING: the drafts were written but the register was not marked (exit %d). Run: python tools/changelog_write.py --qm-drafted %s --text \"%s\"" % (code, ",".join(e["Ref"] for e in entries), text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
