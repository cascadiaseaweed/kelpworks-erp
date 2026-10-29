"""A date typed into Excel is stored as a serial NUMBER (46324 = 2026-10-29) shown through a date style. The change-log tools wrote and read dates as text (YYYY-MM-DD), so the Quality Manager's
Decision Date (typed in Excel) came out of the CSV export and the register reader as "46324". The readers now turn a number shown as a date into an ISO date. Standard library only; every test
runs in a temporary folder."""
import os
import re
import sys

import pytest

from test_changelog_storage import cw, env, load, run  # noqa: F401  (the fixtures and helpers of the storage tests)
from test_qm001_tracking import GOOD_ENTRY, entry, qm, register  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import update_backlog_xlsx as xl  # noqa: E402

STYLES = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<numFmts count="4"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/><numFmt numFmtId="165" formatCode="d\\ mmm\\ yyyy"/>'
          '<numFmt numFmtId="166" formatCode="h:mm"/><numFmt numFmtId="167" formatCode="0.00&quot; d&quot;"/></numFmts>'
          '<cellXfs count="8"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="15"/><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="166"/><xf numFmtId="46"/><xf numFmtId="167"/></cellXfs></styleSheet>')


def test_serial_numbers_become_iso_dates():
    assert xl.iso_from_serial("46324") == "2026-10-29"
    assert xl.iso_from_serial("46324.75") == "2026-10-29"                       # the time of day is dropped
    assert xl.iso_from_serial("45658") == "2025-01-01"
    for not_a_date in ("", "abc", "0", "-5", "5e9", "2026-10-29"):
        assert xl.iso_from_serial(not_a_date) == not_a_date


def test_only_date_styles_are_read_as_dates():
    # 1 short date, 2 d-mmm-yy, 3 and 4 custom date formats; NOT 0 (general), 5 (h:mm), 6 (time only), 7 (a number with a unit)
    assert xl.date_style_ids(STYLES) == {1, 2, 3, 4}
    assert xl.date_style_ids("<styleSheet/>") == set()


def test_a_cell_is_read_as_a_date_only_when_it_is_a_number_with_a_date_style():
    sst = ["46324", "N. Wrana"]
    date = {2}
    assert xl.cell_value('<c r="M2" s="2"><v>46324</v></c>', sst, date) == "2026-10-29"
    assert xl.cell_value('<c r="M2" s="2" t="n"><v>46324</v></c>', sst, date) == "2026-10-29"
    assert xl.cell_value('<c r="M2" s="0"><v>46324</v></c>', sst, date) == "46324"                          # a plain number stays a number
    assert xl.cell_value('<c r="M2" s="2" t="s"><v>0</v></c>', sst, date) == "46324"                       # text that looks like a serial is text
    assert xl.cell_value('<c r="M2" s="2" t="inlineStr"><is><t>2026-10-29</t></is></c>', sst, date) == "2026-10-29"
    assert xl.cell_value('<c r="M2" s="2"><v>46324</v></c>', sst) == "46324"                                # no date styles known: unchanged
    assert xl.cell_value('<c r="M2" s="2"/>', sst, date) == ""


def type_a_date(cw, env, ref, header, serial):
    """What Excel does when the Quality Manager types a date into a register cell: a number cell with a date style (a new cell style is added to the workbook)."""
    snap = cw.Snapshot(env.master)
    styles = snap.files["xl/styles.xml"].decode("utf-8") if "xl/styles.xml" in snap.files else STYLES                          # (the test workbook has no style part of its own)
    count = int(re.search(r'<cellXfs count="(\d+)"', styles).group(1))
    styles = styles.replace('<cellXfs count="%d"' % count, '<cellXfs count="%d"' % (count + 1), 1).replace("</cellXfs>", '<xf numFmtId="15" applyNumberFormat="1"/></cellXfs>', 1)
    part = snap.paths[cw.QM_SHEET]
    xml = snap.files[part].decode("utf-8")
    headers, rows = cw.table_rows(snap, cw.QM_SHEET)
    n = [n for n, r in rows if r.get("Ref") == ref][0]
    cell = "%s%d" % (headers[header], n)
    new, hits = re.subn(r'<c r="%s"[^>]*?(?:/>|>.*?</c>)' % cell, '<c r="%s" s="%d"><v>%s</v></c>' % (cell, count, serial), xml, count=1, flags=re.S)
    assert hits == 1
    cw.save(snap, {"xl/styles.xml": styles, part: new})


def test_a_decision_date_typed_in_excel_is_read_as_an_iso_date(cw, qm):
    assert entry(cw, qm) == 0
    type_a_date(cw, qm, "C1", "Decision Date", 46324)
    row = register(cw, qm.master)[0]
    assert row["Decision Date"] == "2026-10-29"


def test_the_export_writes_a_typed_date_as_an_iso_date(cw, qm, monkeypatch):
    export = load("export_backlog")
    monkeypatch.setattr(export.os.path, "dirname", lambda p, _d=os.path.dirname: str(qm.tmp / "tools") if p.endswith("export_backlog.py") else _d(p))
    assert entry(cw, qm) == 0
    type_a_date(cw, qm, "C1", "Decision Date", 46324)
    assert export.main(["export_backlog.py"]) == 0
    lines = (qm.tmp / "docs" / "backlog" / "qm001-changes.csv").read_text(encoding="utf-8-sig").splitlines()
    assert "2026-10-29" in lines[1] and "46324" not in lines[1]
