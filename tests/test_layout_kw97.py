"""KW-97: spacing and word wrapping. Layout only, so most of this is a check that the rules are in the shipped files, plus one real run:
long names of additional analyses (tests with no CoA specification) must come out in full on the Certificate of Analysis."""
import os
import re

import pytest

import kelp_erp_server as K
from factories import finished_run
from harness import Server

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSS = open(os.path.join(ROOT, "public", "styles.css"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()


def rule(selector):
    """The declaration block of the first rule that starts with this selector in styles.css."""
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", CSS, re.S)
    assert m, selector + " is not in styles.css"
    return m.group(1)


def function_source(start):
    i = APP.index(start)
    return APP[i:i + 9000]


# ---- Status cells: the hold cause wraps inside its box instead of running into the next column -------------------------------------

def test_the_hold_cause_wraps_inside_its_box():
    css = rule(".hold-why ")
    assert "white-space: normal" in css                                      # table cells are nowrap (th, td { white-space: nowrap }); without this the text ran past max-width into the next column
    assert re.search(r"max-width:\s*(min\()?340px", css) and "min-width" in css


def test_a_status_badge_stays_on_one_line():
    assert re.search(r"\.badge\s*\{\s*white-space:\s*nowrap", CSS)


def test_every_status_with_a_cause_uses_the_wrapping_class():
    """The Product Release list, the run window and Finished Goods all draw the cause through .hold-why."""
    assert "el('div', { class: 'hold-why ' + info.kind" in APP
    assert APP.count("class: 'hold-why'") + APP.count("'hold-why '") >= 2


# ---- Lab results: nothing hides the analyte --------------------------------------------------------------------------------------------

def test_the_lab_results_tables_wrap_their_text_cells():
    assert "function wrapTextTable(" in APP
    css = rule(".tablewrap.wrap-text th, .tablewrap.wrap-text td")
    assert "white-space: normal" in css and "overflow-wrap: break-word" in css      # break-word: a column never gets narrower than its longest word
    window = function_source("async function openLabResults")
    assert "wrapTextTable(table(['Test', 'Specification', 'Result', '', 'Laboratory / report', 'Status']" in window
    assert "wrapTextTable(table(['Entered', 'Test', 'Result', 'Report', 'By', '']" in window


def test_the_add_report_form_shows_a_long_test_name_in_full():
    form = function_source("async function addLabReport")
    assert "el('textarea', { rows: '2', placeholder: 'Test name', class: 'stack-input'" in form     # two lines on their own row, not a one-line box beside the select
    assert "class: 'stack-input'" in form[:form.index("function addLine")]                          # the laboratory name box too
    assert "'block' : 'none'" in form                                                               # shown as a block (own line), never inline beside the select
    assert "nameInp.value.replace(" in form                                                         # new lines typed into the name are removed when saved
    css = rule(".stack-input")
    assert "display: block" in css and "width: 100%" in css


# ---- the Certificate of Analysis (PDF) wraps long names instead of clipping them -------------------------------------------------------------

@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


def test_the_certificate_prints_long_additional_analysis_names_in_full(srv):
    admin = srv.admin_client()
    qm, _ = srv.make_user(role="user", isQualityManager=True)
    rid = finished_run(admin)
    names = ["Total Kjeldahl Nitrogen (as N, dry weight basis)", "Iron (Fe) - ICP-MS after microwave digestion",
             "Aluminum (Al) - total recoverable, acid digestion"]
    r = qm.post("/api/production/%d/lab-results" % rid, {"labName": "SGS Canada Inc. Minerals Laboratory", "reportNumber": "SGS-2026-0001234567",
                "results": [{"analyte": n, "value": "12.5", "unit": "ppm", "method": "SGS-ICP-MS-2234 (microwave)"} for n in names]})
    assert r.status == 200, r.text
    pdf = admin.get("/api/production/%d/coa.pdf" % rid)
    assert pdf.status == 200
    text = " ".join(" ".join(str(x) for x in (l if isinstance(l, (list, tuple)) else [l])) for l in K.pdf_text_lines(pdf.body))
    text = re.sub(r"\s+", " ", text)
    for n in names:
        # a long name wraps onto a second line of its cell: every word of it is on the page, none is cut off
        assert all(word in text for word in n.split()), n
