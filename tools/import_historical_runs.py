#!/usr/bin/env python3
"""KW-64: load historical production runs into KelpWorks from the prepared entry workbook.

Standard library only. The tool reads the workbook (read-only), builds one plan per lot, checks everything it can WITHOUT touching the server (--check), checks
the target server's state (--preflight, read-only), and creates the runs through the same API the "+ Historical run" button uses (--run), oldest first.
It never fills a gap with a default: a value the paper log does not show is left out and written into the run's notes, a cell still marked "(decide)" stops the load.

    py tools/import_historical_runs.py --check     --workbook W.xlsx --mapping mapping.json
    py tools/import_historical_runs.py --preflight --workbook W.xlsx --mapping mapping.json --target https://... --expect staging --email you@...
    py tools/import_historical_runs.py --run       --workbook W.xlsx --mapping mapping.json --target https://... --expect staging --email you@... --report out.csv

The workbook, the photos, the mapping file and the report hold production data: keep them OUTSIDE the code repository. The password is typed at the prompt and is
never written anywhere. See docs/historical-entry.md ("Loading the nine runs with the import tool") for the rehearsal order (staging first, then live).
"""
import argparse
import base64
import csv
import datetime
import getpass
import hashlib
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
DECIDE, NOT_RECORDED = "decide", "missing"


# --------------------------------------------------------------------------- #
# Reading the workbook (an .xlsx is a zip of XML parts)
# --------------------------------------------------------------------------- #
def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_workbook(path):
    """{sheet name: [row, ...]} where a row is a list of cell texts (None for an empty cell). Numbers come back as text exactly as stored."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels}
        sheets = {}
        for s in wb.find("m:sheets", NS):
            rid = s.get("{%s}id" % NS["r"])
            part = target[rid].lstrip("/")
            if not part.startswith("xl/"):
                part = "xl/" + part
            root = ET.fromstring(z.read(part))
            rows = []
            for row in root.iter("{%s}row" % NS["m"]):
                cells = {}
                for c in row.findall("m:c", NS):
                    kind = c.get("t")
                    if kind == "s":
                        v = c.find("m:v", NS)
                        text = shared[int(v.text)] if v is not None and v.text is not None else None
                    elif kind == "inlineStr":
                        text = "".join(t.text or "" for t in c.iter("{%s}t" % NS["m"]))
                    else:
                        v = c.find("m:v", NS)
                        text = v.text if v is not None else None
                    if text is not None and text != "":
                        cells[_col_index(c.get("r"))] = text
                width = (max(cells) + 1) if cells else 0
                rowno = int(row.get("r") or len(rows) + 1)
                while len(rows) < rowno - 1:
                    rows.append([])
                rows.append([cells.get(i) for i in range(width)])
            sheets[s.get("name")] = rows
        return sheets


# --------------------------------------------------------------------------- #
# Cell values
# --------------------------------------------------------------------------- #
def classify(v):
    """(kind, text): kind is 'value', 'blank', DECIDE (a choice still to make), NOT_RECORDED (the paper log does not show it) or 'absent'."""
    s = None if v is None else str(v).strip()
    if not s:
        return "blank", None
    low = s.lower()
    if low.startswith("(decide"):
        return DECIDE, s
    if low.startswith("(not recorded") or low.startswith("(not on"):
        return NOT_RECORDED, s
    if low.startswith("(not in the paper log") or low.startswith("(not in kelpworks"):
        return "absent", s
    return "value", s


_NUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:%|[A-Za-z]{0,4}|[A-Za-z]{1,4}/[A-Za-z]{1,4})?\s*$")


def to_number(text):
    m = _NUM.match(text or "")
    return float(m.group(1)) if m else None


def to_datetime(text):
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", text or "")
    return "%sT%s" % (m.group(1), m.group(2)) if m else None


def to_date(text):
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})", text or "")
    return m.group(1) if m else None


class Cell:
    def __init__(self, value, paper=None, note=None):
        self.kind, self.text = classify(value)
        self.paper, self.note = paper, note

    def ok(self):
        return self.kind == "value"


# --------------------------------------------------------------------------- #
# One lot sheet -> a structured lot
# --------------------------------------------------------------------------- #
SECTION_NAMES = {"create": "create", "initiation": "initiation", "feedstock": "feedstock", "homogenization": "homogenization", "extraction": "extraction", "separation": "separation",
                 "pasteurization": "pasteurization", "dilution": "dilution", "packaging": "packaging", "cross-check": "crosscheck", "things to check": "check"}


def _section_of(text):
    t = re.sub(r"^\d+\.\s*", "", text.strip()).lower()
    for key, name in SECTION_NAMES.items():
        if t.startswith(key):
            return name
    return None


def parse_lot_sheet(rows, lot_no):
    lot = {"no": lot_no, "title": None, "header": {}, "initiation": {}, "totes": [], "stages": {}, "passes": [], "notes": [], "documents": [], "packaging": {}, "crosscheck": {}, "check": []}
    section, tote, ps, sub = None, None, None, None
    for idx, raw in enumerate(rows, 1):
        a, b, c, d, e = (list(raw) + [None] * 5)[:5]
        a, b = (a or "").strip(), (b or "").strip()
        if idx == 1:
            lot["title"] = a
            continue
        if not a and not b:
            continue
        if idx <= 4:
            continue
        if a and not b and not c:
            sec = _section_of(a)
            if sec:
                section, tote, ps, sub = sec, None, None, None
            continue
        if section == "check":
            lot["check"].append({"area": a, "text": b})
            continue
        if not a and re.match(r"^Tote \d+ - ", b):
            tote = {"label": b, "n": int(re.match(r"^Tote (\d+)", b).group(1)), "rows": {}}
            lot["totes"].append(tote)
            continue
        if not a and re.match(r"^Pass \d+ - ", b):
            ps = {"label": b, "n": int(re.match(r"^Pass (\d+)", b).group(1)), "tank": (re.search(r"Tank (6[AB])", b) or [None, None])[1], "rows": {}}
            lot["passes"].append(ps)
            continue
        if not a and b.lower().startswith("homogenization qc"):
            sub = "homogenization"
            continue
        cell = Cell(c, d, e)
        screen = a
        if section == "create":
            lot["header"][b] = cell
        elif section == "initiation":
            lot["initiation"][b] = cell
        elif section == "feedstock" and tote is not None and screen == "Feedstock" and not (b == "Notes" or b.startswith("Note")):
            tote["rows"][b] = cell
        elif section == "feedstock":
            lot["stages"].setdefault("feedstock", {})[(screen, b)] = cell
        elif section == "dilution" and ps is not None:
            ps["rows"][(screen, b)] = cell
        elif section in ("homogenization", "extraction", "separation", "pasteurization", "dilution", "packaging"):
            lot["stages"].setdefault(section, {})[(screen, b)] = cell
        elif section == "crosscheck":
            lot["crosscheck"][b] = cell
        else:
            lot["stages"].setdefault(section or "other", {})[(screen, b)] = cell
    return lot


_IMAGE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ .\-]*?\.(?:jpe?g|png|gif|webp)", re.I)


def file_names(text):
    """The file names in a cell: one or several, separated by commas (a name never contains a comma)."""
    return [m.group(0).strip() for m in _IMAGE_NAME.finditer(text or "")]


def photos_from_note(note):
    m = re.search(r"Photos?:\s*(.+)$", note or "")
    return file_names(m.group(1)) if m else []


def photo_caption(note):
    """Text the person wrote in brackets after the file names ('(Left is before extraction. Right is after.)'): it belongs to the note."""
    m = re.search(r"Photos?:\s*.+?\.(?:jpe?g|png|gif|webp)\s*\((.+)\)\s*$", note or "", re.I)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------- #
# Mapping: workbook labels -> KelpWorks fields. Anything a lot sheet shows that is not listed here (or in IGNORED) is a problem, so nothing is dropped silently.
# --------------------------------------------------------------------------- #
RHO = "ρliquid (g/mL)"
HOMOG = {"Started at": ("startedAt", "dt"), "Rinse water (L)": ("rinsingWaterL", "num"), "Pre-Dilution Tank Level (L)": ("slurryL", "num"),
         "Wet-solids-wt (g)": ("wetSolidsWtG", "num"), "Liquid-wt (g)": ("liquidWtG", "num"), "Target %Solids Loading, (w/w)": ("targetPctWetSolids", "num"),
         "Dilution water added (L)": ("dilutionWaterL", "num"), "Post-Dilution Tank Level (L)": ("postDilutionTankL", "num")}
HOMOG_QC = {"pH": ("qcPh", "num"), "TDS (%)": ("tdsPct", "num"), "Brix (%)": ("brixPct", "num"), "Mannitol (%)": ("mannitolPct", "num"), "TSliquid (%)": ("tsLiquidPct", "num"),
            RHO: ("rhoLiquidGMl", "num"), "%Moisture solids": ("tsSolidsPct", "num"), "Solids Loading (%)": ("solidsLoadingPct", "num")}
EXTRACTION = {"Started at": ("startedAt", "dt"), "Amplitude (%)": ("amplitudePct", "num"), "Flow rate (L/min)": ("flowrateLpm", "num"), "Pressure (psi)": ("pressurePsi", "num"),
              "Starting power (W)": ("startingPowerW", "num")}
EXTRACTION_QC = {"pH": ("qcPh", "num"), "TDS (%)": ("tdsPct", "num"), "Brix (%)": ("brixPct", "num"), "Mannitol (%)": ("mannitolPct", "num"), "TSliquid (%)": ("tsLiquidPct", "num"),
                 RHO: ("rhoLiquidGMl", "num")}
SEPARATION = {"Started at": ("startedAt", "dt"), "Flow rate (L/min)": ("flowrateLpm", "num"), "Mesh size (micron)": ("meshMicron", "num"),
              "Total wet-solids weight (kg)": ("wetSolidsWtKg", "num"), "%Moisture centrifuge solids": ("pctMoisture", "num"), "Water addition": ("waterAdditionL", "num")}
PASTEURIZATION = {"Started at": ("startedAt", "dt"), "Product set-point (C)": ("productSetpointC", "num"), "Boiler set-point (C)": ("boilerSetpointC", "num")}
PACKAGING = {"Packaged at": ("packagedAt", "dt")}
PASS_ROWS = {"Product transferred (L)": "productTransferredL", "Dilution water added (L)": "waterAddedL", "Measured pH (before citric)": "measuredPh", "Citric acid added (kg)": "citricKg"}
NOTE_SECTIONS = {"homogenization": "homogenization", "extraction": "extraction", "separation": "separation", "pasteurization": "pasteurization", "dilution": "dilution",
                 "feedstock": "feedstock", "packaging": "packaging"}
# shown on the lot sheet only for the person entering it: never sent
IGNORED_SCREENS = ("(not in KelpWorks)", "Run card")
IGNORED_LABELS = {"Homogenization QC check", "Filtrate QC check"}
FILTRATE_QC = {"pH": "liquidQcPh", "TDS (%)": "liquidTdsPct", "Brix (%)": "liquidBrixPct", "Mannitol (%)": "liquidMannitolPct"}


def _ascii(text):
    return (text or "").encode("ascii", "replace").decode("ascii")


class Plan(dict):
    pass


def _new_plan(lot, choice, seq, fmt):
    h = lot["header"]
    run_date = to_date(h["Run date"].text) if "Run date" in h and h["Run date"].ok() else None
    paper = h["Original processing lot"].text if "Original processing lot" in h and h["Original processing lot"].ok() else "Lot-%d" % lot["no"]
    lot_name = choice.get("processingLot") or fmt.format(date=(run_date or "").replace("-", ""), seq=seq, no=lot["no"])
    return Plan(lot=lot["no"], paperLot=paper, processingLot=lot_name, runDate=run_date, operators=None, location=None, sku=choice.get("sku"), release=choice.get("release"),
                deduct=(choice.get("inventory", "count") != "leave"), totes=[], stages={}, passes=[], packaging=[], notes=[], documents=[], problems=[], gaps=[], extra=[], toCheck=[], expect={})


def _put(plan, stage, key, cell, kind, what):
    if cell is None:
        return
    if cell.kind == DECIDE:
        plan["problems"].append('%s: still marked "(decide)"' % what)
    elif cell.kind == "value":
        v = {"dt": to_datetime, "num": to_number, "date": to_date, "text": lambda t: t.strip()}[kind](cell.text)
        if v is None:
            plan["problems"].append('%s: cannot read "%s" as %s' % (what, cell.text, {"dt": "a date and time", "num": "a number", "date": "a date", "text": "text"}[kind]))
        else:
            plan["stages"].setdefault(stage, {})[key] = v
    elif cell.kind in (NOT_RECORDED, "blank"):
        plan["gaps"].append(what)


def _stage_rows(plan, lot, section, table, qc_table=None, qc_prefix="", label_prefix=""):
    for (screen, label), cell in lot["stages"].get(section, {}).items():
        what = "%s: %s" % (section.capitalize(), label)
        if screen in IGNORED_SCREENS or label in IGNORED_LABELS or (section == "pasteurization" and label in ("Receiving tanks", "Tank 5A / 5B level (L)")):
            continue                                              # (the last two are read together with the dilution passes, below)
        if screen == "QC Check":
            if qc_table and label in qc_table:
                key, kind = qc_table[label]
                _put(plan, section, key, cell, kind, "%s QC check: %s" % (section.capitalize(), label))
            else:
                plan["problems"].append("%s: no KelpWorks field for the QC check %s" % (section.capitalize(), label))
        elif label == "Notes":
            if cell.ok():
                plan["notes"].append({"section": NOTE_SECTIONS.get(section, "general"), "body": _with_caption(cell), "photos": photos_from_note(cell.note)})
        elif label in table:
            key, kind = table[label]
            _put(plan, section, key, cell, kind, what)
        elif screen == "(no field)":
            if cell.ok():
                plan["extra"].append("%s - %s: %s (no KelpWorks field)" % (section.capitalize(), label, cell.text))
        elif screen == "Documents":
            if cell.ok():
                names = file_names(cell.text)
                if not names:
                    plan["problems"].append('%s: cannot find a picture file name in "%s"' % (what, cell.text))
                plan["documents"].extend(names)
        else:
            plan["problems"].append("%s: the workbook shows \"%s\" but the tool has no mapping for it" % (section.capitalize(), label))


def _with_caption(cell):
    cap = photo_caption(cell.note)
    return cell.text + ("\n(Photo caption on the paper log: %s)" % cap if cap else "")


def _tote_detail(plan, tote, choice):
    rows, n = tote["rows"], tote["n"]
    what = "Tote %d" % n
    out = {}
    lot_cell = rows.get("Tote (pick list)")
    override = (choice.get("toteLots") or {}).get(str(n))
    if override:
        tote_lot = override.strip()
    elif lot_cell is not None and lot_cell.ok() and "?" not in lot_cell.text:
        tote_lot = lot_cell.text.strip()
    else:
        tote_lot = None
        plan["problems"].append('%s: the KelpWorks tote lot number is "%s": say which tote it is in the mapping file (toteLots)' % (what, lot_cell.text if lot_cell is not None else "missing"))
    for label, key, kind in (("Loaded at", "loadedAt", "dt"), ("Weight (kg)", "weightKg", "num"), ("Volume (L)", "volumeL", "num"), ("pH", "ph", "num"), ("ORP (mV)", "orp", "num"),
                             ("Odour", "odour", "text"), ("Odour intensity", "odourIntensity", "text"), ("Decision", "decision", "text")):
        cell = rows.get(label)
        if cell is None:
            plan["problems"].append("%s: the workbook has no %s row" % (what, label))
        elif cell.kind == DECIDE:
            plan["problems"].append('%s: %s is still marked "(decide)"' % (what, label))
        elif cell.ok():
            v = {"dt": to_datetime, "num": to_number, "text": lambda t: t.strip()}[kind](cell.text)
            if v is None:
                plan["problems"].append('%s: cannot read %s "%s"' % (what, label, cell.text))
                continue
            if key == "odourIntensity":
                v = v.split(" - ")[-1].strip()
            if key == "decision":
                v = v.lower()
            out[key] = v
        else:
            plan["gaps"].append("%s: %s" % (what, label))
    photos = {}
    for label, slot in (("Surface photo", "surface"), ("Settling / striation photo", "striation")):
        cell = rows.get(label)
        if cell is not None and cell.ok():
            photos[slot] = cell.text.strip()
        elif cell is not None:
            plan["gaps"].append("%s: %s" % (what, label))
    return {"n": n, "toteLot": tote_lot, "details": out, "photos": photos, "weightKg": out.get("weightKg")}


def build_plan(lot, choice, seq, fmt, global_choice):
    plan = _new_plan(lot, choice, seq, fmt)
    h, ini = lot["header"], lot["initiation"]
    if not plan["runDate"]:
        plan["problems"].append("The run date is missing")
    if not plan["sku"]:
        plan["problems"].append("Lot %d: the product (sku) is not named on the paper log: set it in the mapping file" % lot["no"])
    if plan["release"] not in ("review", "grandfather"):
        plan["problems"].append("Lot %d: release must be 'review' or 'grandfather' in the mapping file (the workbook cell is still \"(decide)\")" % lot["no"])
    plan["deduct"] = choice.get("inventory", "count") != "leave"
    for key, label in (("operators", "Operators"), ("location", "Location")):
        cell = ini.get(label)
        if cell is not None and cell.ok():
            plan[key] = choice.get(key) or cell.text.strip()
        else:
            plan["gaps"].append("Initiation: " + label)
    for tote in lot["totes"]:
        plan["totes"].append(_tote_detail(plan, tote, choice))
    if not lot["totes"]:
        plan["problems"].append("No feedstock totes found on the lot sheet")
    for note_label, cell in (lot["stages"].get("feedstock") or {}).items():
        if note_label[1] == "Notes" and cell.ok():
            plan["notes"].append({"section": "feedstock", "body": _with_caption(cell), "photos": photos_from_note(cell.note)})
        elif note_label[1].startswith("Note") and cell.ok():
            plan["notes"].append({"section": "feedstock", "body": cell.text, "photos": []})
    _stage_rows(plan, lot, "homogenization", HOMOG, HOMOG_QC)
    _stage_rows(plan, lot, "extraction", EXTRACTION, EXTRACTION_QC)
    _stage_rows(plan, lot, "separation", SEPARATION)
    _stage_rows(plan, lot, "pasteurization", PASTEURIZATION)
    # receiving tanks, the 5A / 5B levels and the passes
    past = lot["stages"].get("pasteurization", {})
    recv = past.get(("Pasteurization", "Receiving tanks"))
    t5 = past.get(("Pasteurization", "Tank 5A / 5B level (L)"))
    if t5 is not None and t5.kind == DECIDE:
        plan["problems"].append('Pasteurization: Tank 5A / 5B level is still "(decide)"')
    elif t5 is not None and not t5.ok():
        plan["gaps"].append("Pasteurization: Tank 5A / 5B level")
    elif t5 is not None and t5.ok():
        nums = re.findall(r"-?\d+(?:\.\d+)?", t5.text)
        if len(nums) == 2:
            plan["stages"].setdefault("pasteurization", {}).update(tank5aL=float(nums[0]), tank5bL=float(nums[1]))
        else:
            plan["problems"].append('Pasteurization: cannot read the 5A / 5B levels "%s" (expected two numbers)' % t5.text)
    passes = lot["passes"]
    if not passes:
        plan["problems"].append("No dilution pass found on the lot sheet")
    for i, ps in enumerate(passes):
        tank = (ps["tank"] or "").lower()
        what = "Dilution pass %d" % ps["n"]
        body = {}
        sub = {"stages": {}}
        local = Plan(problems=plan["problems"], gaps=plan["gaps"], stages=sub["stages"], notes=plan["notes"], extra=plan["extra"], documents=plan["documents"])
        for (screen, label), cell in ps["rows"].items():
            if label in PASS_ROWS:
                _put(local, "p", PASS_ROWS[label], cell, "num", "%s: %s" % (what, label))
            elif label == "Final level, Tank 6A (L)" or label == "Final level, Tank 6B (L)":
                _put(local, "p", "tank%sFinalL" % label[-6:-4].lower(), cell, "num", "%s: %s" % (what, label))
            elif label == "Ksorbate added (L)":
                _put(local, "p", "ksorbateAddedL%s" % tank, cell, "num", "%s: %s" % (what, label))
            elif label == "Sodium benzoate added (L)":
                _put(local, "p", "nabenzoateAddedL%s" % tank, cell, "num", "%s: %s" % (what, label))
            elif label == "Preservatives added at":
                if cell.ok():
                    plan["extra"].append("%s - preservatives added at %s (no KelpWorks field)" % (what, cell.text))
                else:
                    plan["extra"].append("%s - the paper log shows no preservative time" % what)
            elif label == "Final check: pH / TDS / Brix / Mannitol":
                parts = [to_number(x) for x in (cell.text or "").split("/")] if cell.ok() else []
                if i == 0 and len(parts) == 4 and None not in parts:
                    plan["stages"].setdefault("packaging", {}).update(qcPh=parts[0], tdsPct=parts[1], brixPct=parts[2], mannitolPct=parts[3])
                elif cell.ok():
                    plan["extra"].append("%s - final check pH / TDS / Brix / Mannitol: %s (KelpWorks keeps one final check, from pass 1)" % (what, cell.text))
                elif cell.kind == DECIDE:
                    plan["problems"].append('%s: the final check is still "(decide)"' % what)
                else:
                    plan["gaps"].append("%s: final check" % what)
            elif label == "Samples taken":
                if cell.ok() and cell.text.strip().lower().startswith("no"):
                    plan["extra"].append("%s - paper log: samples taken: No" % what)
            elif label == "Notes":
                if cell.ok():
                    plan["notes"].append({"section": "dilution", "body": _with_caption(cell), "photos": photos_from_note(cell.note)})
            else:
                plan["problems"].append('%s: the workbook shows "%s" but the tool has no mapping for it' % (what, label))
        got = sub["stages"].get("p", {})
        if i == 0:
            plan["stages"].setdefault("dilution", {}).update(got)
            plan["stages"].setdefault("pasteurization", {})["receivingTanks"] = ps["tank"] if ps["tank"] else None
            if not ps["tank"]:
                plan["problems"].append("%s: the receiving tank is not shown on the pass title" % what)
        else:
            row = dict(got)
            row["receivingTanks"] = ps["tank"]
            plan["passes"].append(row)
    # Potassium sorbate / sodium benzoate are entered as LITRES of stock solution; KelpWorks takes kg out of inventory as litres x the stock concentration. The paper log gives no
    # concentration, so it comes from the mapping file (the workbook's accepted assumption), and a load that would take NOTHING out of stock is refused rather than done silently.
    for name, label, key in (("Potassium sorbate", "ksorbate", "ksorbateStockPct"), ("Sodium benzoate", "nabenzoate", "nabenzoateStockPct")):
        litres = sum(v for k, v in plan["stages"].get("dilution", {}).items() if k.startswith(label + "AddedL")) + sum(v for row in plan["passes"] for k, v in row.items() if k.startswith(label + "AddedL"))
        pct = choice.get(key, global_choice.get(key))
        if litres > 0 and pct is None:
            plan["problems"].append("%s was added (%g L of stock solution) but its stock concentration is not set: nothing would be taken out of inventory. Set %s in the mapping file (the workbook assumes 25 %% w/v for sorbate)." % (name, litres, key))
        elif litres > 0:
            plan["stages"].setdefault("dilution", {})[key] = float(pct)
            plan["extra"].append("%s stock solution taken as %g %% w/v to convert the litres added into kg taken out of inventory (workbook assumption; the paper log gives litres)" % (name, float(pct)))
    if recv is not None and recv.ok() and passes and (passes[0]["tank"] or "") not in recv.text.replace("/", "").replace("+", ""):
        plan["extra"].append("Pasteurization - receiving tanks on the paper log: %s (pass 1 went to Tank %s)" % (recv.text, passes[0]["tank"]))
    elif recv is not None and recv.kind == DECIDE:
        plan["problems"].append('Pasteurization: receiving tanks is still "(decide)"')
    # packaging
    pk = lot["stages"].get("packaging", {})
    for (screen, label), cell in pk.items():
        if label in PACKAGING:
            key, kind = PACKAGING[label]
            _put(plan, "packaging", key, cell, kind, "Packaging: " + label)
        elif label == "Container and quantity":
            m = re.match(r"^(.*?)\s+x\s+(\d+(?:\.\d+)?)\s*$", cell.text or "")
            if cell.kind == DECIDE:
                plan["problems"].append('Packaging: container is still "(decide)"')
            elif not m:
                plan["problems"].append('Packaging: cannot read the container and quantity "%s" (expected "<container> x <number>")' % cell.text)
            else:
                plan["packaging"].append({"containerUnit": m.group(1).strip(), "qty": float(m.group(2))})
        elif label == "Notes":
            if cell.ok():
                plan["notes"].append({"section": "packaging", "body": _with_caption(cell), "photos": photos_from_note(cell.note)})
        elif screen not in IGNORED_SCREENS:
            plan["problems"].append('Packaging: the workbook shows "%s" but the tool has no mapping for it' % label)
    if not plan["packaging"]:
        plan["problems"].append("No packaging (container and quantity) found")
    # cross-check and the rest
    for label, cell in lot["crosscheck"].items():
        if label == "Performance notes" and cell.ok():
            plan["notes"].append({"section": "general", "body": "Performance notes: " + _with_caption(cell), "photos": photos_from_note(cell.note)})
        elif label == "Required-fields override" and cell.ok():
            plan["extra"].append("Paper log: the paper form's required-fields override was used")
        elif label == "Extraction efficiency":
            plan["expect"]["extractionEfficiencyPct"] = to_number(cell.text)
        elif label.startswith("Conversion"):
            plan["expect"]["conversion"] = to_number(cell.text)
    plan["toCheck"] = ["%s: %s" % (x["area"], x["text"]) for x in lot["check"]]
    # photos named on the Photos list but not attached to a field are not guessed at
    return plan


# --------------------------------------------------------------------------- #
# Loading the whole workbook into plans
# --------------------------------------------------------------------------- #
DEFAULT_LOT_FORMAT = "PR-{date}-{seq:03d}"
NOTE_MAX = 3800


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def readme_assumptions(sheets):
    out, on = [], False
    for row in sheets.get("Read me", []):
        text = (row[0] if row else None) or ""
        if text.startswith("Assumptions made"):
            on = True
            continue
        if on and text.startswith("- "):
            out.append(text[2:].strip())
    return out


def overview_expect(sheets):
    """Per paper lot number: the figures the workbook's Overview sheet gives, for the reconciliation."""
    rows = sheets.get("Overview") or []
    if not rows:
        return {}
    head = [str(x or "").strip() for x in rows[0]]
    out = {}
    for r in rows[1:]:
        rec = {head[i]: r[i] for i in range(min(len(head), len(r))) if r[i] is not None}
        no = str(rec.get("Lot (paper)", "")).strip()
        if no.isdigit():
            out[int(no)] = {"totes": to_number(str(rec.get("Totes", ""))), "feedstockKg": to_number(str(rec.get("Feedstock kg", ""))),
                            "passes": to_number(str(rec.get("Dilution passes", ""))), "ibcs": to_number(str(rec.get("IBCs packaged", ""))),
                            "outputL": to_number(str(rec.get("Output L (paper)", ""))), "finalPh": to_number(str(rec.get("Final pH (pass 1)", ""))),
                            "finalTds": to_number(str(rec.get("Final TDS % (pass 1)", ""))), "extractionEfficiencyPct": to_number(str(rec.get("Extraction eff. (paper)", ""))),
                            "pasteurized": str(rec.get("Pasteurized?", "")).strip(), "runDate": str(rec.get("Run date", "")).strip()}
    return out


def split_note(body):
    """A note can be at most NOTE_MAX characters: split on line breaks, never inside a line (a longer line is cut at a space)."""
    parts, cur = [], ""
    for line in body.split("\n"):
        while len(line) > NOTE_MAX:
            cut = line.rfind(" ", 0, NOTE_MAX) or NOTE_MAX
            cut = cut if cut > 0 else NOTE_MAX
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        if len(cur) + len(line) + 1 > NOTE_MAX and cur:
            parts.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line
    if cur:
        parts.append(cur)
    return parts


def compose_run_notes(plan, assumptions, accepted_by, sha):
    lines = ["Historical run entered retrospectively from the paper production log (original paper lot %s) with the KW-64 import tool. KelpWorks processing lot %s." % (plan["paperLot"], plan["processingLot"]),
             "Source workbook SHA-256 %s." % sha[:16]]
    if plan["gaps"]:
        lines.append("NOT RECORDED on the paper log (left blank, nothing was filled in): " + "; ".join(plan["gaps"]) + ".")
    if plan["extra"]:
        lines.append("Paper-log values that have no KelpWorks field: " + "; ".join(plan["extra"]) + ".")
    if plan["toCheck"]:
        lines.append("Items the entry workbook flagged to check: " + " | ".join(plan["toCheck"]))
    if assumptions:
        lines.append("Assumptions made when transcribing (%s): " % (accepted_by or "not yet accepted") + " ".join("(%d) %s" % (i, a) for i, a in enumerate(assumptions, 1)))
    return [{"section": "general", "body": part, "photos": []} for part in split_note("\n".join(lines))]


def load_plans(workbook, mapping, photos_dir=None):
    """(plans, workbook sha, problems about the whole load). Plans are in date order (the order they are created in)."""
    sheets = read_workbook(workbook)
    sha = sha256_file(workbook)
    expect = overview_expect(sheets)
    lots = []
    for name in sheets:
        m = re.match(r"^Lot (\d+)$", name)
        if m:
            lots.append(parse_lot_sheet(sheets[name], int(m.group(1))))
    lots.sort(key=lambda L: (to_date(L["header"]["Run date"].text) if "Run date" in L["header"] and L["header"]["Run date"].ok() else "9999", L["no"]))
    choices = mapping.get("lots") or {}
    fmt = mapping.get("lotNumberFormat") or DEFAULT_LOT_FORMAT
    assumptions = readme_assumptions(sheets)
    accepted_by = mapping.get("assumptionsAcceptedBy")
    plans, problems = [], []
    if not lots:
        problems.append("The workbook has no 'Lot N' sheets")
    for seq, lot in enumerate(lots, 1):
        plan = build_plan(lot, choices.get(str(lot["no"])) or {}, seq, fmt, mapping)
        plan["expect"].update({k: v for k, v in (expect.get(lot["no"]) or {}).items() if v not in (None, "")})
        if not accepted_by:
            plan["problems"].append("The workbook's assumptions are not accepted yet: set assumptionsAcceptedBy (your name and date) in the mapping file once you have confirmed them")
        plan["notes"] = compose_run_notes(plan, assumptions, accepted_by, sha) + plan["notes"]
        plans.append(plan)
    seen = {}
    for p in plans:
        if p["processingLot"] in seen:
            problems.append("Two lots would get the processing lot %s (lots %d and %d)" % (p["processingLot"], seen[p["processingLot"]], p["lot"]))
        seen[p["processingLot"]] = p["lot"]
    return plans, sha, problems


def photo_path(photos_root, lot_no, filename):
    return os.path.join(photos_root, "Lot_%d" % lot_no, "photos", filename)


def plan_files(plan):
    """Every photo / document the plan names: (kind, filename)."""
    names = []
    for t in plan["totes"]:
        for slot, fn in (t["photos"] or {}).items():
            names.append(("tote-" + slot, fn))
    for n in plan["notes"]:
        for fn in n["photos"]:
            names.append(("note", fn))
    for fn in plan["documents"]:
        names.append(("document", fn))
    return names


def check_files(plans, photos_root):
    problems = []
    for p in plans:
        for kind, fn in plan_files(p):
            if not photos_root or not os.path.isfile(photo_path(photos_root, p["lot"], fn)):
                problems.append("Lot %d: the file %s (%s) is not in %s" % (p["lot"], fn, kind, photo_path(photos_root or "<photos folder>", p["lot"], "")))
    return problems


def print_check(plans, load_problems, out=None):
    out = out or sys.stdout
    total = 0
    for p in plans:
        out.write("Lot %d  %s  %s  (paper %s)  release=%s  inventory=%s  sku=%s\n" % (p["lot"], p["processingLot"], p["runDate"], p["paperLot"], p["release"] or "?", "count" if p["deduct"] else "leave", p["sku"] or "?"))
        out.write("   totes %d, dilution passes %d, packaging %s, notes %d, documents %d, files %d\n" % (len(p["totes"]), 1 + len(p["passes"]), ", ".join("%s x %g" % (x["containerUnit"], x["qty"]) for x in p["packaging"]) or "-",
                                                                                                         len(p["notes"]), len(p["documents"]), len(plan_files(p))))
        if p["gaps"]:
            out.write("   not recorded on the paper log (%d): %s\n" % (len(p["gaps"]), "; ".join(_ascii(g) for g in p["gaps"][:6]) + (" ..." if len(p["gaps"]) > 6 else "")))
        for x in p["problems"]:
            out.write("   PROBLEM: %s\n" % _ascii(x))
            total += 1
    for x in load_problems:
        out.write("PROBLEM: %s\n" % _ascii(x))
        total += 1
    out.write("%d problem(s) must be settled before anything can be loaded.\n" % total if total else "No problems: the plan is complete.\n")
    return total


# --------------------------------------------------------------------------- #
# Talking to a KelpWorks server
# --------------------------------------------------------------------------- #
ENV_FOR_EXPECT = {"staging": "staging", "live": "production", "development": "development"}
STAGE_ORDER = ("homogenization", "extraction", "separation", "pasteurization", "dilution", "packaging")


class ApiFailure(Exception):
    def __init__(self, status, message, method="", path=""):
        super().__init__("%s %s -> %s: %s" % (method, path, status, message))
        self.status, self.message, self.method, self.path = status, message, method, path


class Api:
    def __init__(self, base_url, timeout=180):
        self.base, self.token, self.timeout = base_url.rstrip("/"), None, timeout

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                msg = json.loads(raw.decode("utf-8")).get("error") or raw.decode("utf-8", "replace")
            except ValueError:
                msg = raw.decode("utf-8", "replace")[:300]
            raise ApiFailure(e.code, msg, method, path)
        except urllib.error.URLError as e:
            raise ApiFailure(0, "cannot reach the server: %s" % e.reason, method, path)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def get(self, path):
        return self.call("GET", path)

    def raw(self, path):
        """The bytes of a file (a download); None when the server does not have it."""
        req = urllib.request.Request(self.base + path)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError):
            return None

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path, body):
        return self.call("PUT", path, body)

    def delete(self, path):
        return self.call("DELETE", path)

    def login(self, email, password):
        r = self.post("/api/auth/login", {"email": email, "password": password})
        self.token = r.get("token")
        return r


def file_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def content_type_of(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def setting_value(ref, key):
    """A setting from GET /api/refdata (each setting is {value, label, ...} or a bare value)."""
    v = (ref.get("settings") or {}).get(key)
    return v.get("value") if isinstance(v, dict) else v


def preflight(api, plans, expect, photos_root, out=None):
    """Read-only checks of the target. Returns (problems, warnings)."""
    out = out or sys.stdout
    problems, warnings = [], []
    env = api.get("/api/env").get("env")
    if env != ENV_FOR_EXPECT[expect]:
        problems.append("This server says it is '%s' but --expect %s was given: stopping so a rehearsal is never run against the wrong site." % (env, expect))
        out.write("PROBLEM: %s" % problems[0] + chr(10))
        return problems, warnings
    me = api.get("/api/me")
    user = me.get("user") or me
    if user.get("role") != "admin":
        problems.append("You are not signed in as an administrator (historical entry is administrator-only).")
    ref = api.get("/api/refdata")
    if not float(setting_value(ref, "historical_entry_enabled") or 0):
        problems.append("The setting 'Historical run entry' is off on this server. Switch it on (Calculations page) for the load and back off afterwards: the tool never changes it.")
    skus = {s["code"]: s for s in ref.get("skus") or []}
    containers = {c["name"]: c for c in ref.get("containers") or []}
    aliases = ref.get("containerAliases") or {}
    done = {r["processingLot"].upper() for r in api.get("/api/production").get("runs", [])}
    drafts = api.get("/api/production/drafts").get("drafts", [])
    ids = [r["id"] for r in api.get("/api/production").get("runs", [])] + [d["id"] for d in drafts]
    totes = {t["lot"]: t for t in api.get("/api/totes").get("totes", [])}
    used = {}
    for p in plans:
        what = "Lot %d" % p["lot"]
        if p["processingLot"].upper() in done:
            warnings.append("%s: %s already exists on this server: it will be skipped." % (what, p["processingLot"]))
        if p["sku"] and p["sku"] not in skus:
            problems.append("%s: the product '%s' is not a product on this server." % (what, p["sku"]))
        elif p["sku"] and not skus[p["sku"]].get("active", True):
            problems.append("%s: the product '%s' is not active." % (what, p["sku"]))
        for pk in p["packaging"]:
            name = aliases.get(pk["containerUnit"], pk["containerUnit"])
            if name not in containers or containers[name].get("litresEach") is None:
                problems.append("%s: the container '%s' is not a packaging container on this server." % (what, pk["containerUnit"]))
            else:
                pk["resolved"] = name
        if p["processingLot"].upper() not in done:
            for t in p["totes"]:
                tote = totes.get(t["toteLot"])
                if tote is None:
                    problems.append("%s tote %d: %s is not in Feedstock Inventory on this server." % (what, t["n"], t["toteLot"]))
                    continue
                if tote.get("status") != "in_stock":
                    problems.append("%s tote %d: %s is '%s' (not in stock), so it cannot go into a run." % (what, t["n"], t["toteLot"], tote.get("status")))
                if t["toteLot"] in used and used[t["toteLot"]] != p["lot"]:
                    problems.append("%s tote %d: %s is also used by lot %d." % (what, t["n"], t["toteLot"], used[t["toteLot"]]))
                used[t["toteLot"]] = p["lot"]
                t["toteId"] = tote["id"]
    pending = [p for p in plans if p["processingLot"].upper() not in done]
    if pending and not done and not drafts:
        warnings.append("No run exists yet: the first run created gets row number %d, so lot sequence numbers will match the paper-lot order only if nothing else is created in between." % (max(ids or [0]) + 1))
    elif drafts:
        warnings.append("There are %d unfinished draft run(s) on this server: they are left alone, and the numbers the historical runs get may not match the sequence." % len(drafts))
    if photos_root is not None:
        problems.extend(check_files(plans, photos_root))
    for w in warnings:
        out.write("WARNING: %s\n" % _ascii(w))
    for x in problems:
        out.write("PROBLEM: %s\n" % _ascii(x))
    out.write("Preflight: %d problem(s), %d warning(s).\n" % (len(problems), len(warnings)))
    return problems, warnings


def _stage_body(plan, stage):
    return dict(plan["stages"].get(stage) or {})


def load_one(api, plan, photos_root, out=None):
    """Create, fill and finalize one historical run. A failure before finalize discards the draft (stock and totes go back as they were)."""
    out = out or sys.stdout
    rid = None
    try:
        base = {"sku": plan["sku"], "runDate": plan["runDate"], "operators": plan["operators"], "location": plan["location"], "toteIds": [], "feedstockDetails": {}}
        r = api.post("/api/production/drafts", dict(base, historical={"processingLot": plan["processingLot"], "release": plan["release"], "deductInventory": plan["deduct"]}))
        rid = r["run"]["id"]
        out.write("   draft %s created (run id %d)\n" % (plan["processingLot"], rid))
        tote_ids, details = [], {}
        for t in plan["totes"]:
            d = dict(t["details"])
            for slot, fn in (t["photos"] or {}).items():
                p = photo_path(photos_root, plan["lot"], fn)
                # the production log's own photo route (a document of THIS run, as the Feedstock card in the draft stores it), not the tote's photo route
                resp = api.post("/api/production/%d/feedstock-photo" % rid, {"slot": slot, "filename": fn, "contentType": content_type_of(fn), "dataB64": file_b64(p)})
                d["surfacePhotoId" if slot == "surface" else "striationPhotoId"] = resp["attachmentId"]
            tote_ids.append(t["toteId"])
            details[str(t["toteId"])] = d
        body = dict(base, toteIds=tote_ids, feedstockDetails=details)
        res = api.put("/api/production/drafts/%d" % rid, body)
        if res.get("rejectedToteIds"):
            raise ApiFailure(400, "the server treated tote(s) %s as rejected" % res["rejectedToteIds"], "PUT", "drafts")
        for stage in STAGE_ORDER:
            payload = _stage_body(plan, stage)
            if payload:
                api.put("/api/production/%d/stages/%s" % (rid, stage), payload)
        for row in plan["passes"]:
            api.post("/api/production/%d/dilution-passes" % rid, row)
        for pk in plan["packaging"]:
            api.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": pk.get("resolved") or pk["containerUnit"], "qty": pk["qty"]})
        for n in plan["notes"]:
            res = api.post("/api/production/%d/notes" % rid, {"section": n["section"], "body": n["body"]})
            for fn in n["photos"]:
                api.post("/api/production/%d/notes/%d/images" % (rid, res["id"]), {"filename": fn, "contentType": content_type_of(fn), "dataB64": file_b64(photo_path(photos_root, plan["lot"], fn))})
        for fn in plan["documents"]:
            api.post("/api/production/%d/attachments" % rid, {"filename": fn, "contentType": content_type_of(fn), "dataB64": file_b64(photo_path(photos_root, plan["lot"], fn))})
        api.post("/api/production/drafts/%d/finalize" % rid, body)
        out.write("   finalized\n")
        return rid
    except Exception:
        if rid is not None:
            try:
                api.delete("/api/production/drafts/%d" % rid)
                out.write("   the draft was discarded (stock and totes are as they were)\n")
            except ApiFailure as e:
                out.write("   COULD NOT discard the draft %d: %s\n" % (rid, _ascii(str(e))))
        raise


def _close(a, b, tol):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def reconcile(api, plan, photos_root=None):
    """Read the run back from the server and compare it with the workbook's Overview figures, and (with photos_root) check that every photo and document really OPENS from the run.
    Returns rows (metric, expected, got, status)."""
    runs = [r for r in api.get("/api/production").get("runs", []) if r["processingLot"].upper() == plan["processingLot"].upper()]
    if not runs:
        return [("run exists", plan["processingLot"], None, "DIFFERENCE")]
    run = runs[0]
    st = run.get("stages") or {}
    ex = plan["expect"]
    kg = round(sum((i.get("weightKg") or 0) for i in run.get("inputs", [])), 1)
    homog_tds, ext_tds = (st.get("homogenization") or {}).get("tdsPct"), (st.get("extraction") or {}).get("tdsPct")
    eff = round((ext_tds - homog_tds) / homog_tds * 100, 1) if homog_tds and ext_tds is not None else None
    rel = (run.get("release") or {}).get("state")
    want_rel = "legacy" if plan["release"] == "grandfather" else "pending_review"
    rows = [("totes", ex.get("totes"), len(run.get("inputs", [])), _close(ex.get("totes"), len(run.get("inputs", [])), 0)),
            ("feedstock kg (measured)", ex.get("feedstockKg"), kg, _close(ex.get("feedstockKg"), kg, 1.0)),
            ("dilution passes", ex.get("passes"), 1 + len(run.get("dilutionPasses", [])), _close(ex.get("passes"), 1 + len(run.get("dilutionPasses", [])), 0)),
            ("IBCs packaged", ex.get("ibcs"), run.get("ibcUsed"), _close(ex.get("ibcs"), run.get("ibcUsed"), 0)),
            ("output L", ex.get("outputL"), run.get("outputLitres"), _close(ex.get("outputL"), run.get("outputLitres"), 1.0)),
            ("final pH", ex.get("finalPh"), (st.get("packaging") or {}).get("qcPh"), _close(ex.get("finalPh"), (st.get("packaging") or {}).get("qcPh"), 0.005)),
            ("final TDS %", ex.get("finalTds"), (st.get("packaging") or {}).get("tdsPct"), _close(ex.get("finalTds"), (st.get("packaging") or {}).get("tdsPct"), 0.05)),
            ("extraction efficiency %", ex.get("extractionEfficiencyPct"), eff, _close(ex.get("extractionEfficiencyPct"), eff, 0.15)),
            ("run date", plan["runDate"], run.get("runDate"), plan["runDate"] == run.get("runDate")),
            ("release state", want_rel, rel, rel == want_rel)]
    out = [(m, e, g, "PASS" if ok else "DIFFERENCE") for m, e, g, ok in rows if not (e is None and m not in ("run date", "release state"))]
    if photos_root:
        by_tote = {i["toteLot"]: i for i in run.get("inputs", [])}
        want = opened = 0
        for t in plan["totes"]:
            for slot, fn in (t["photos"] or {}).items():
                want += 1
                inp = by_tote.get(t["toteLot"]) or {}
                aid = inp.get("surfacePhoto" if slot == "surface" else "striationPhoto")
                data = api.raw("/api/production/%d/attachments/%s/download" % (run["id"], aid)) if aid else None
                if data is not None and len(data) == os.path.getsize(photo_path(photos_root, plan["lot"], fn)):
                    opened += 1
        names = [a.get("filename") for a in run.get("attachments", [])]
        docs_ok = sum(1 for fn in plan["documents"] if fn in names)
        note_want = sum(len(n["photos"]) for n in plan["notes"])
        note_got = sum(len(v) for v in (run.get("logNoteImages") or {}).values())
        for m, e, g in (("feedstock photos that open", want, opened), ("documents attached", len(plan["documents"]), docs_ok), ("note pictures", note_want, note_got)):
            out.append((m, e, g, "PASS" if e == g else "DIFFERENCE"))
    return out


def write_report(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lot", "processing lot", "metric", "workbook", "KelpWorks", "result"])
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KW-64: load historical production runs from the prepared entry workbook (standard library only).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="offline: parse the workbook, print the gap list; nothing is sent")
    mode.add_argument("--preflight", action="store_true", help="read-only checks against --target")
    mode.add_argument("--run", action="store_true", help="create the runs on --target, oldest first")
    ap.add_argument("--workbook", required=True)
    ap.add_argument("--mapping", required=True, help="the mapping file (per-lot choices); keep it beside the workbook, not in the repository")
    ap.add_argument("--photos", help="the folder that holds Lot_1 ... Lot_9 (default: the workbook's folder)")
    ap.add_argument("--target", help="https://... of the KelpWorks site")
    ap.add_argument("--expect", choices=sorted(ENV_FOR_EXPECT), help="the kind of site --target must be")
    ap.add_argument("--email")
    ap.add_argument("--report", help="write the reconciliation report (CSV) here (outside the repository)")
    ap.add_argument("--only", help="comma-separated paper lot numbers to load (default: all)")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation on a staging or development target (never on live)")
    args = ap.parse_args(argv)
    with open(args.mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    photos_root = args.photos or os.path.dirname(os.path.abspath(args.workbook))
    try:
        plans, sha, load_problems = load_plans(args.workbook, mapping)
    except (OSError, zipfile.BadZipFile) as e:
        print("Cannot read the workbook (%s). If it is open in Excel, close it or point --workbook at a copy." % _ascii(str(e)))
        return 2
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        plans = [p for p in plans if p["lot"] in wanted]
    problems = print_check(plans, load_problems) + len(check_files(plans, photos_root))
    for x in check_files(plans, photos_root):
        print("PROBLEM: %s" % _ascii(x))
    if args.check:
        return 0 if not problems else 2
    if problems:
        print("Nothing was sent: settle the problems above first.")
        return 2
    if not (args.target and args.expect and args.email):
        print("--target, --expect and --email are needed for --preflight and --run")
        return 2
    api = Api(args.target)
    password = os.environ.get("KELP_ERP_IMPORT_PASSWORD") or getpass.getpass("Password for %s: " % args.email)
    try:
        api.login(args.email, password)
    except ApiFailure as e:
        print("Could not sign in: %s" % _ascii(e.message))
        return 2
    finally:
        password = None
    pf_problems, _warnings = preflight(api, plans, args.expect, photos_root)
    if args.preflight:
        return 0 if not pf_problems else 2
    if pf_problems:
        print("Nothing was created: settle the problems above first.")
        return 2
    if args.expect == "live" or not args.yes:
        word = "LOAD LIVE" if args.expect == "live" else "LOAD"
        if input("Type %s to create %d run(s) on %s: " % (word, len(plans), args.target)).strip() != word:
            print("Cancelled; nothing was created.")
            return 2
    done = {r["processingLot"].upper() for r in api.get("/api/production").get("runs", [])}
    rows, failed = [], False
    for p in plans:
        print("Lot %d -> %s (%s)" % (p["lot"], p["processingLot"], p["runDate"]))
        if p["processingLot"].upper() in done:
            print("   already on this server: skipped")
        else:
            try:
                load_one(api, p, photos_root)
            except Exception as e:                                                  # stop at the first failure: nothing later depends on a lot that is missing
                print("   FAILED: %s" % _ascii(str(e)))
                failed = True
                break
        for metric, exp, got, res in reconcile(api, p, photos_root):
            rows.append((p["lot"], p["processingLot"], metric, exp, got, res))
            print("   %-26s workbook %-10s KelpWorks %-10s %s" % (metric, exp, got, res))
    if args.report:
        write_report(args.report, rows)
        print("Report written to %s" % args.report)
    differences = [r for r in rows if r[5] != "PASS"]
    print("%d lot(s) processed; %d difference(s)%s." % (len(plans), len(differences), "; STOPPED at a failure" if failed else ""))
    return 0 if not failed and not differences else 1


if __name__ == "__main__":
    sys.exit(main())
