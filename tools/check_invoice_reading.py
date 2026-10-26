"""KW-82: check how well Receive reads the real test invoices (KelpWorks ERP > Invoices).

Run it BY HAND, on your own computer, with your own key (the tests and CI never call the real service):

    set KELP_ERP_ANTHROPIC_KEY=<your key>
    python tools/check_invoice_reading.py "<folder with the 8 invoice PDFs>"

Each PDF is read the way the server reads it (the text layer if it has one, else the Claude service) and compared with `tests/invoice_expectations.json`: every field the document shows must
be right, and every field it does not show must stay blank. Prints one line per field that is wrong and a total; exits 1 if anything is wrong. Add `--only <part of a file name>` to read
just one invoice (each read of a scan costs a few cents). Standard library only.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import kelp_erp_server as K  # noqa: E402

EXPECTATIONS = os.path.join(ROOT, "tests", "invoice_expectations.json")
TEXT_FIELDS = ("invoiceNo", "poNumber", "orderNo", "deliveryDoc", "carrier", "currency")
DATE_FIELDS = ("invoiceDate", "shipDate")
NUMBER_FIELDS = ("subtotal", "freight", "tax", "otherCharges", "invoiceTotal")


def _alnum(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _same(field, exp, got):
    if exp is None:
        return got is None
    if got is None:
        return False
    if field in NUMBER_FIELDS or field in ("qty", "unitPrice", "lineTotal"):
        return abs(float(exp) - float(got)) < 0.005
    if field == "supplier":
        a, b = K.supplier_match_key(exp), K.supplier_match_key(got)
        return bool(a) and (a == b or a in b or b in a)
    if field == "description":
        return _alnum(exp) in _alnum(got) or _alnum(got) in _alnum(exp)
    return _alnum(exp) == _alnum(got)


def _ok(field, exp, got):
    if exp == "skip":
        return True
    if isinstance(exp, list):
        return any(_same(field, e, got) for e in exp)
    return _same(field, exp, got)


def compare_reading(expected, reading):
    """-> a list of problems (empty when the reading is what the document shows). `reading` is the validate_reading() shape."""
    bad = []
    for field, exp in expected["header"].items():
        got = reading["header"].get(field)
        if not _ok(field, exp, got):
            bad.append("header %s: expected %r, read %r" % (field, exp, got))
    if expected["lines"] != "skip":
        used = set()
        for e in expected["lines"]:
            cands = [i for i, ln in enumerate(reading["lines"]) if i not in used and ((e["itemCode"] and _alnum(ln["itemCode"]) == _alnum(e["itemCode"]))
                                                                                     or (not e["itemCode"] and _same("description", e["description"], ln["description"])))]
            if not cands:
                bad.append("line %s: not found" % (e["itemCode"] or e["description"]))
                continue
            i = cands[0]
            used.add(i)
            ln = reading["lines"][i]
            for field in ("description", "qty", "unit", "unitPrice", "lineTotal"):
                if field == "unit" and e["unit"] is None:
                    continue                                              # the unit may be printed or not; it is never made up in the expectations
                if not _ok(field, e[field], ln[field]):
                    bad.append("line %s %s: expected %r, read %r" % (e["itemCode"] or e["description"], field, e[field], ln[field]))
            if sorted(_alnum(x) for x in e["lots"]) != sorted(_alnum(x) for x in ln["lots"]):
                bad.append("line %s lots: expected %r, read %r" % (e["itemCode"] or e["description"], e["lots"], ln["lots"]))
    return bad


def read_file(path):
    data = open(path, "rb").read()
    lines = K.pdf_text_lines(data) if path.lower().endswith(".pdf") else []
    if lines:
        return K.validate_reading(K.parse_invoice_text(lines, [])), "text"
    raw, _model = K.read_invoice_ai(data, K.INVOICE_READ_EXTS[os.path.splitext(path)[1].lower()])
    return K.validate_reading(raw), "ai"


def main(argv):
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--only")]
    if len(args) != 1 or not os.path.isdir(args[0]):
        print("Usage: python tools/check_invoice_reading.py <folder with the invoice PDFs> [--only <part of a file name>]")
        return 2
    if not (os.environ.get("KELP_ERP_ANTHROPIC_KEY") or "").strip():
        print("Set KELP_ERP_ANTHROPIC_KEY first (text PDFs need no key, scans do).")
    expected = json.load(open(EXPECTATIONS, encoding="utf-8"))["invoices"]
    wrong = checked = 0
    for name, exp in expected.items():
        if only and only.lower() not in name.lower():
            continue
        path = os.path.join(args[0], name)
        if not os.path.isfile(path):
            print("MISSING  %s" % name)
            continue
        checked += 1
        try:
            reading, how = read_file(path)
        except K.InvoiceReadError as e:
            print("ERROR    %s: %s (%s)" % (name, e.message, e.detail))
            wrong += 1
            continue
        problems = compare_reading(exp, reading)
        warnings = K.reading_warnings(reading, 1)
        print("%-8s %s (read by %s)" % ("OK" if not problems else "WRONG", name, how))
        for p in problems + ["warning: " + w for w in warnings]:
            print("           " + p)
        wrong += bool(problems)
    print("\n%d invoice%s checked, %d with a wrong or missing value." % (checked, "" if checked == 1 else "s", wrong))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
