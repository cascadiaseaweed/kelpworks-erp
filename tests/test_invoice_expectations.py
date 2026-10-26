"""KW-82: the expected values of the 8 real test invoices (tests/invoice_expectations.json) are transcribed by eye, so they are checked here: the amounts must add up, and the comparison used by
tools/check_invoice_reading.py must accept a perfect reading and reject a wrong one. The real PDFs are business records and are not in the repository."""
import copy
import importlib.util
import json
import os
import re

import pytest

import kelp_erp_server as K
from harness import ROOT

SPEC = json.load(open(os.path.join(ROOT, "tests", "invoice_expectations.json"), encoding="utf-8"))["invoices"]
_spec = importlib.util.spec_from_file_location("check_invoice_reading", os.path.join(ROOT, "tools", "check_invoice_reading.py"))
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def perfect(exp):
    """The reading that matches the expectations exactly (the first acceptable value of a list, blank for 'skip')."""
    def pick(v):
        return None if v == "skip" else (v[0] if isinstance(v, list) else v)
    head = {k: pick(v) for k, v in exp["header"].items()}
    lines = [] if exp["lines"] == "skip" else [dict(ln) for ln in exp["lines"]]
    return {"header": head, "lines": lines}


def test_there_are_eight_invoices_named_date_supplier_number():
    assert len(SPEC) == 8
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}_[A-Za-z]+_[A-Za-z0-9\-]+\.pdf", n) for n in SPEC)


@pytest.mark.parametrize("name", sorted(SPEC))
def test_the_transcribed_amounts_add_up(name):
    exp = SPEC[name]
    r = perfect(exp)
    h = r["header"]
    if exp["lines"] == "skip":                                                  # a freight invoice: only the subtotal + tax = total where both are shown
        if h["subtotal"] is not None and h["tax"] is not None:
            assert abs(h["subtotal"] + h["tax"] - h["invoiceTotal"]) < 0.005
        return
    assert K.reading_warnings(r, 0) == [], name


@pytest.mark.parametrize("name", sorted(SPEC))
def test_a_perfect_reading_passes_and_a_wrong_one_does_not(name):
    exp = SPEC[name]
    assert tool.compare_reading(exp, perfect(exp)) == []
    wrong = perfect(exp)
    wrong["header"]["invoiceNo"] = "WRONG-1"
    assert any("invoiceNo" in p for p in tool.compare_reading(exp, wrong))
    if exp["lines"] != "skip":
        bad = perfect(exp)
        bad["lines"][0]["qty"] = (bad["lines"][0]["qty"] or 0) + 1
        assert any("qty" in p for p in tool.compare_reading(exp, bad))
        none = perfect(exp)
        none["lines"] = []
        assert any("not found" in p for p in tool.compare_reading(exp, none))


def test_a_value_that_the_document_does_not_show_must_stay_blank():
    exp = SPEC["2026-06-16_Univar_53959279.pdf"]
    r = perfect(exp)
    r["header"]["carrier"] = "Invented Freight Co"
    assert any("carrier" in p for p in tool.compare_reading(exp, r))
    r2 = perfect(SPEC["2026-05-19_IslandBarrelsTotes_304.pdf"])
    r2["header"]["currency"] = "CAD"                                              # the Island Barrels invoice prints "$" only: a currency code would be a guess
    assert any("currency" in p for p in tool.compare_reading(SPEC["2026-05-19_IslandBarrelsTotes_304.pdf"], r2))


def test_the_text_reader_agrees_with_the_expectations_on_the_two_invoices_whose_text_it_can_be_given():
    """The scans have no text, so the text reader is checked on text PDFs laid out with the same labels and values (see tests/test_invoice_reading.py for the PDFs themselves)."""
    from test_invoice_reading import DUBOIS, UNIVAR
    for name, text, sup in (("2026-08-31_DuBois_IN-60133189.pdf", DUBOIS, "DuBois Chemicals Canada"), ("2026-06-16_Univar_53959279.pdf", UNIVAR, "Univar Solutions Canada")):
        reading = K.validate_reading(K.parse_invoice_text(text, [sup]))
        exp = copy.deepcopy(SPEC[name])
        for k in ("poNumber", "orderNo", "carrier", "shipDate", "deliveryDoc", "subtotal", "freight", "otherCharges"):      # not in the synthetic text (or deliberately not guessed)
            exp["header"][k] = "skip"
        exp["lines"] = [{k: v for k, v in ln.items()} for ln in exp["lines"]]
        for ln in exp["lines"]:
            ln["unit"] = None
            ln["lots"] = ln["lots"] if name.startswith("2026-08") else []          # the text reader takes lot numbers only from a line that is plainly a list of them (an unlabelled batch column is left to the scan reader)
        exp["header"]["supplier"] = sup
        problems = tool.compare_reading(exp, reading)
        assert problems == [], (name, problems)
