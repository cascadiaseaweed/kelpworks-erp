"""KW-64 / KW-98: the historical-runs import tool (tools/import_historical_runs.py). SYNTHETIC data only: the real workbook, photos and mapping hold production data and are never in the repository.

The tool is run in-process against a real server (the harness). The server is a STAGING one (KELP_ERP_ENV=staging), because the tool refuses to run when the kind of site it finds
is not the kind it was told to expect."""
import ast
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import import_historical_runs as T  # noqa: E402
import kelp_erp_server as K  # noqa: E402
from factories import harvest_totes, sql  # noqa: E402
from harness import ADMIN_EMAIL, ADMIN_PASSWORD, Server  # noqa: E402

pytestmark = pytest.mark.slow    # KW-70: skipped by the quick tier (-m "not slow"); CI runs it

IBC = "New 1,000 L IBC Tote"
JPG = b"\xff\xd8\xff\xe0JFIF-test-photo"


# --------------------------------------------------------------------------- #
# A synthetic entry workbook in the same layout as the real one
# --------------------------------------------------------------------------- #
def cells(*row):
    return [("t", str(c), 0) if c not in (None, "") else None for c in row]


def extraction_rows(lot, d, v):
    """KW-98: the Extraction rows of a lot sheet: normal, bypassed (explicit rows, the paper values listed as '(no field)') or not done at all."""
    if lot.get("bypass"):
        return [("Extraction", "Bypassed", "Yes", "Extraction Notes: Bypassed extraction"), ("Extraction", "Reason bypassed", "Bypassed extraction"),
                ("(no field)", "Paper extraction parameters", "100% / 15LPM", "", "Not entered: the extraction was bypassed.")]
    if lot.get("no_extraction"):
        return [("Extraction", "Started at", "(not recorded)"), ("Extraction", "Amplitude (%)", "(not recorded)"), ("QC Check", "pH", "(not recorded)")]
    return [("Extraction", "Started at", d + " 12:00"), ("Extraction", "Amplitude (%)", 100), ("Extraction", "Flow rate (L/min)", 15),
            ("Extraction", "Pressure (psi)", v("pressure", 35)), ("Extraction", "Starting power (W)", 4775), ("QC Check", "pH", 5.15), ("QC Check", "TDS (%)", lot["ext_tds"])]


def lot_rows(lot, photos):
    """One 'Lot N' sheet. lot: no, date, totes [(tote lot, kg)], passes [tank, ...], ibcs, final (pH, TDS, Brix, Mannitol), homog_tds, ext_tds, missing [labels left '(not recorded)']."""
    n, d = lot["no"], lot["date"]
    miss = set(lot.get("missing", []))

    def v(label, value):
        return "(not recorded)" if label in miss else value
    R = [("Lot %d - %s - entry sheet for KelpWorks" % (n, d),), ("Source: synthetic",), (), ("KelpWorks screen", "Field", "Value", "Paper log", "Note / check"),
         ("1. Create the run: Production > + Historical run",),
         ("+ Historical run", "Original processing lot", "Lot-%d" % n), ("+ Historical run", "Run date", d),
         ("+ Historical run", "Inventory", "(decide)"), ("+ Historical run", "Release", "(decide)"),
         ("2. Initiation",), ("Initiation", "Product (SKU)", "(not in the paper log)"), ("Initiation", "Operators", "DP, AL"), ("Initiation", "Location", "Port Edward"),
         ("3. Feedstock (%d totes)" % len(lot["totes"]),)]
    for i, (tote_lot, kg) in enumerate(lot["totes"], 1):
        s, st = "L%d_Tote%d_Surface.jpg" % (n, i), "L%d_Tote%d_Settling.jpg" % (n, i)
        photos.extend([(n, s), (n, st)])
        R += [("", "Tote %d - paper ID %s - KelpWorks lot %s" % (i, tote_lot, tote_lot)), ("Feedstock", "Tote (pick list)", tote_lot), ("Feedstock", "Loaded at", d + " 10:15"),
              ("Feedstock", "Weight (kg)", kg), ("Feedstock", "Volume (L)", 900), ("Feedstock", "Density (calculated by KelpWorks)", 1.1), ("Feedstock", "pH", 5.4), ("Feedstock", "ORP (mV)", -50),
              ("Feedstock", "Odour", "marine, butyric"), ("Feedstock", "Odour intensity", "2 - Medium"), ("Feedstock", "Decision", "Accepted"), ("Feedstock", "Surface photo", s),
              ("Feedstock", "Settling / striation photo", st)]
    R += [("4. Homogenization",), ("Homogenization", "Started at", v("hstart", d + " 11:00")), ("Homogenization", "Rinse water (L)", 300), ("Homogenization", "Pre-Dilution Tank Level (L)", 4630),
          ("Homogenization", "Wet-solids-wt (g)", ""), ("Homogenization", "Dilution water added (L)", 0), ("Homogenization", "Post-Dilution Tank Level (L)", 4630),
          ("", "Homogenization QC check"), ("QC Check", "pH", 5.2), ("QC Check", "TDS (%)", lot["homog_tds"]), ("QC Check", "Brix (%)", 4.1),
          ("(no field)", "Initial pH / Citric added", "5.2 / 0kg", "", "KelpWorks has no Homogenization pH-adjustment fields"),
          ("Homogenization", "Notes", "Did not pH adjust.", "", "Photos: L%d_Homog_note.jpg" % n)]
    photos.append((n, "L%d_Homog_note.jpg" % n))
    if lot.get("slurry"):                    # KW-98: the handwritten sampling record also has the slurry readings (homogenization QC fields)
        R += [("QC Check", "TSslurry (%)", 95.6), ("QC Check", "\u03c1slurry (g/mL)", 1.018)]
    R += [("5. Extraction",)] + extraction_rows(lot, d, v) + [
          ("6. Separation",), ("Separation", "Started at", d + " 15:41"), ("Separation", "Flow rate (L/min)", 40), ("Separation", "Mesh size (micron)", 70),
          ("Separation", "Total wet-solids weight (kg)", "(not recorded)"), ("Separation", "%Moisture centrifuge solids", "(not recorded)"), ("(no field)", "Water addition", "0L"),
          ("Documents", "Solids photo 1", "L%d_Solids1.jpg" % n, "", "Upload as a run document."),
          ("7. Pasteurization",), ("Pasteurization", "Started at", d + " 18:00"), ("Pasteurization", "Product set-point (C)", 80), ("Pasteurization", "Boiler set-point (C)", 90),
          ("Pasteurization", "Receiving tanks", "/".join(t for t in lot["passes"] if t) or "(not recorded)"), ("Pasteurization", "Tank 5A / 5B level (L)", "(not recorded)"),
          ("8. Dilution & Preservation",)]
    photos.append((n, "L%d_Solids1.jpg" % n))
    if lot.get("no_passes"):
        R.append(("Dilution", "Passes", "(no passes) nothing recorded on the paper log"))
    for k, tank in enumerate(lot["passes"], 1):
        if tank is None:                    # KW-98: pH adjustment and a final check, but no tank, no volumes and no preservative on the paper log
            R += [("", "Pass %d - Tank (not recorded)" % k), ("Dilution", "Measured pH (before citric)", 4.1), ("Dilution", "Citric acid added (kg)", 25),
                  ("Packaging QC", "Final check: pH / TDS / Brix / Mannitol", "%s / %s%% / %s%% / %s%%" % lot["final"] + (" / %s%%" % lot["final_mc"] if lot.get("final_mc") else "")), ("Samples", "Samples taken", "No")]
            continue
        R += [("", "Pass %d - Tank %s" % (k, tank)), ("Dilution", "Product transferred (L)", 2483), ("Dilution", "Dilution water added (L)", 2483), ("Dilution", "Final level, Tank %s (L)" % tank, 5243),
              ("Dilution", "Ksorbate added (L)", 52), ("Dilution", "Sodium benzoate added (L)", 0), ("Dilution", "Preservatives added at", d + " 15:35"), ("Dilution", "Measured pH (before citric)", 5.8),
              ("Dilution", "Citric acid added (kg)", 32), ("Packaging QC", "Final check: pH / TDS / Brix / Mannitol", "%s / %s%% / %s%% / %s%%" % lot["final"] + (" / %s%%" % lot["final_mc"] if lot.get("final_mc") else "")), ("Samples", "Samples taken", "Yes")]
    R += [("9. Packaging",), ("Packaging", "Packaged at", d + " 16:00"), ("Packaging", "Container and quantity", "(no output) lost down the drain due to operator error" if lot.get("no_output") else "%s x %d" % (IBC, lot["ibcs"])),
          ("10. Cross-check against the paper log",), ("Run card", "Extraction efficiency", "(none - extraction not done)" if lot.get("bypass") or lot.get("no_extraction") else "-5.3 %"), ("(not in KelpWorks)", "Required-fields override", "Yes"),
          ("Things to check before entering",), ("Paper log", "Required-fields override was used. Reason on the log: \"test\".")]
    return R


def make_workbook(path, lots):
    photos = []
    sheets = []
    rm = K.XlsxSheet("Read me")
    for row in [("Historical production runs (synthetic)",), (), ("Assumptions made in this workbook (please confirm)",), ("- Farm J = JAM.",), ("- Tote number = batch x 10 + tote.",)]:
        rm.row(cells(*row))
    sheets.append(rm)
    ov = K.XlsxSheet("Overview")
    ov.row(cells("Lot (paper)", "Run date", "Operators", "Feedstock site", "Totes", "Feedstock kg", "Pasteurized?", "Dilution passes", "IBCs packaged", "Output L (paper)", "Final pH (pass 1)",
                 "Final TDS % (pass 1)", "Extraction eff. (paper)"))
    for lot in lots:
        eff = round((lot["ext_tds"] - lot["homog_tds"]) / lot["homog_tds"] * 100, 1) if not lot.get("bypass") and not lot.get("no_extraction") else None
        ov.row(cells(lot["no"], lot["date"], "DP, AL", "JAM", len(lot["totes"]), sum(k for _t, k in lot["totes"]), "Yes", len(lot["passes"]), lot["ibcs"], lot["ibcs"] * 1000,
                     lot["final"][0], lot["final"][1], "%s%%" % eff if eff is not None else "(none)"))
    sheets.append(ov)
    for lot in lots:
        s = K.XlsxSheet("Lot %d" % lot["no"])
        for row in lot_rows(lot, photos):
            s.row(cells(*row))
        sheets.append(s)
    with open(path, "wb") as f:
        f.write(K.xlsx_build(sheets))
    return photos


@pytest.fixture()
def world(tmp_path):
    """A staging-like server with historical entry on, six totes in stock and a workbook of three lots that uses them."""
    with Server(env={"KELP_ERP_ENV": "staging"}) as srv:
        admin = srv.admin_client()
        assert admin.put("/api/settings/historical_entry_enabled", {"value": 1}).status == 200
        ids = harvest_totes(admin, n=6, site="JAM", species="SL", date="2026-05-04")
        by_id = {t["id"]: t["lot"] for t in admin.get("/api/totes").json["totes"]}
        lots_of = [by_id[i] for i in ids]
        lots = [dict(no=1, date="2026-07-27", totes=[(lots_of[0], 996), (lots_of[1], 969)], passes=["6A"], ibcs=3, final=(4.09, 1.8, 2.7, 2.5), homog_tds=3.8, ext_tds=3.6, missing=["hstart", "pressure"]),
                dict(no=2, date="2026-07-27", totes=[(lots_of[2], 950), (lots_of[3], 940)], passes=["6A", "6B"], ibcs=4, final=(3.88, 1.9, 2.7, 2.5), homog_tds=3.5, ext_tds=3.7),
                dict(no=3, date="2026-07-29", totes=[(lots_of[4], 900), (lots_of[5], 910)], passes=["6B"], ibcs=2, final=(4.02, 1.5, 1.7, 1.9), homog_tds=3.0, ext_tds=3.3)]
        wb = str(tmp_path / "entry.xlsx")
        photos = make_workbook(wb, lots)
        for lot_no, fn in photos:
            d = tmp_path / ("Lot_%d" % lot_no) / "photos"
            d.mkdir(parents=True, exist_ok=True)
            (d / fn).write_bytes(JPG)
        mapping = {"assumptionsAcceptedBy": "N. Wrana 2026-10-26", "ksorbateStockPct": 25,
                   "lots": {"1": {"sku": "KELPIVEX", "release": "grandfather"}, "2": {"sku": "KELPIVEX", "release": "review"}, "3": {"sku": "KELPIVEX", "release": "review"}}}
        mp = str(tmp_path / "mapping.json")
        with open(mp, "w") as f:
            json.dump(mapping, f)
        yield dict(srv=srv, admin=admin, wb=wb, mp=mp, mapping=mapping, lots=lots, tmp=tmp_path, totes=lots_of)


def run_tool(world, *extra, expect="staging", server=None, monkeypatch=None, report=None):
    srv = server or world["srv"]
    argv = ["--workbook", world["wb"], "--mapping", world["mp"], "--photos", str(world["tmp"]), "--target", srv.url, "--expect", expect, "--email", ADMIN_EMAIL, "--yes"] + list(extra)
    if report:
        argv += ["--report", report]
    out = io.StringIO()
    old = sys.stdout
    os.environ["KELP_ERP_IMPORT_PASSWORD"] = ADMIN_PASSWORD
    sys.stdout = out
    try:
        code = T.main(argv)
    finally:
        sys.stdout = old
        os.environ.pop("KELP_ERP_IMPORT_PASSWORD", None)
    return code, out.getvalue()


def runs_by_lot(admin):
    return {r["processingLot"]: r for r in admin.get("/api/production").json["runs"]}


def on_hand(srv, name):
    return sql(srv, "SELECT on_hand FROM consumables WHERE name=?", (name,))[0][0]


# --------------------------------------------------------------------------- #
# --check: what the tool finds in the workbook, offline
# --------------------------------------------------------------------------- #
def test_the_workbook_is_read_and_the_gaps_are_listed_without_anything_being_sent(world, capsys):
    code = T.main(["--check", "--workbook", world["wb"], "--mapping", world["mp"], "--photos", str(world["tmp"])])
    out = capsys.readouterr().out
    assert code == 0 and "No problems" in out
    assert "PR-20260727-001" in out and "PR-20260727-002" in out and "PR-20260729-003" in out                   # PR-<run date>-<sequence>
    assert "Homogenization: Started at" in out and "Extraction: Pressure (psi)" in out                           # '(not recorded)' is listed, not filled
    assert world["srv"].admin_client().get("/api/production").json["runs"] == []                              # --check never touches a server


def test_a_cell_still_marked_decide_or_an_unaccepted_choice_stops_everything(world, capsys):
    mapping = dict(world["mapping"], lots={"1": {"sku": "KELPIVEX"}, "2": {"release": "review"}, "3": {"sku": "KELPIVEX", "release": "maybe"}})
    del mapping["assumptionsAcceptedBy"]
    json.dump(mapping, open(world["mp"], "w"))
    code = T.main(["--check", "--workbook", world["wb"], "--mapping", world["mp"], "--photos", str(world["tmp"])])
    out = capsys.readouterr().out
    assert code == 2
    assert "Lot 1: release must be" in out and "Lot 2: the product (sku)" in out and "Lot 3: release must be" in out and "assumptions are not accepted yet" in out


def test_a_label_the_tool_does_not_know_is_a_problem_never_dropped_silently(world, capsys):
    rows = T.read_workbook(world["wb"])
    lot = T.parse_lot_sheet(rows["Lot 1"], 1)
    lot["stages"]["extraction"][("Extraction", "Frobnicator (rpm)")] = T.Cell("42")
    plan = T.build_plan(lot, world["mapping"]["lots"]["1"], 1, T.DEFAULT_LOT_FORMAT, world["mapping"])
    assert any("Frobnicator" in x and "no mapping" in x for x in plan["problems"])


def test_a_missing_photo_or_a_tote_that_needs_a_choice_is_reported(world, capsys):
    (world["tmp"] / "Lot_1" / "photos" / "L1_Tote1_Surface.jpg").unlink()
    code = T.main(["--check", "--workbook", world["wb"], "--mapping", world["mp"], "--photos", str(world["tmp"])])
    assert code == 2 and "L1_Tote1_Surface.jpg" in capsys.readouterr().out
    plans, _sha, _p = T.load_plans(world["wb"], world["mapping"])
    assert plans[0]["totes"][0]["toteLot"] == world["totes"][0]
    lot = T.parse_lot_sheet(T.read_workbook(world["wb"])["Lot 1"], 1)
    lot["totes"][0]["rows"]["Tote (pick list)"] = T.Cell("JAM-SL-20250507-???")
    plan = T.build_plan(lot, {"sku": "KELPIVEX", "release": "review"}, 1, T.DEFAULT_LOT_FORMAT, {})
    assert any("toteLots" in x for x in plan["problems"])
    plan = T.build_plan(lot, {"sku": "KELPIVEX", "release": "review", "toteLots": {"1": world["totes"][0]}}, 1, T.DEFAULT_LOT_FORMAT, {})
    assert plan["totes"][0]["toteLot"] == world["totes"][0] and not any("toteLots" in x for x in plan["problems"])


# --------------------------------------------------------------------------- #
# --preflight and the guard against the wrong site
# --------------------------------------------------------------------------- #
def test_the_tool_refuses_a_site_that_is_not_the_kind_it_was_told_to_expect(world):
    with Server() as live_like:                                          # a server that says it is production
        code, out = run_tool(world, "--preflight", server=live_like, expect="staging")
        assert code == 2 and "stopping so a rehearsal is never run against the wrong site" in out
        code, out = run_tool(world, "--run", server=live_like, expect="staging")
        assert code == 2 and live_like.admin_client().get("/api/production").json["runs"] == []
    code, out = run_tool(world, "--preflight", expect="live")           # and the other way round
    assert code == 2 and "never run against the wrong site" in out


def test_preflight_needs_the_setting_on_stock_totes_and_a_real_container(world):
    assert world["admin"].put("/api/settings/historical_entry_enabled", {"value": 0}).status == 200
    code, out = run_tool(world, "--preflight")
    assert code == 2 and "'Historical run entry' is off" in out
    world["admin"].put("/api/settings/historical_entry_enabled", {"value": 1})
    sql(world["srv"], "UPDATE tote_lots SET status='hold' WHERE lot_number=?", (world["totes"][2],))
    code, out = run_tool(world, "--preflight")
    assert code == 2 and world["totes"][2] in out and "not in stock" in out
    sql(world["srv"], "UPDATE tote_lots SET status='in_stock' WHERE lot_number=?", (world["totes"][2],))
    sql(world["srv"], "DELETE FROM tote_lots WHERE lot_number=?", (world["totes"][5],))
    code, out = run_tool(world, "--preflight")
    assert code == 2 and world["totes"][5] in out and "not in Feedstock Inventory" in out


def test_preflight_is_read_only(world):
    before = sql(world["srv"], "SELECT (SELECT COUNT(*) FROM production_runs), (SELECT COUNT(*) FROM tote_lots WHERE status!='in_stock'), (SELECT COUNT(*) FROM consumable_txns)")
    code, out = run_tool(world, "--preflight")
    assert code == 0 and "0 problem(s)" in out
    assert sql(world["srv"], "SELECT (SELECT COUNT(*) FROM production_runs), (SELECT COUNT(*) FROM tote_lots WHERE status!='in_stock'), (SELECT COUNT(*) FROM consumable_txns)") == before


# --------------------------------------------------------------------------- #
# --run: the load
# --------------------------------------------------------------------------- #
def test_the_three_lots_are_loaded_in_date_order_with_everything_the_paper_log_shows(world, tmp_path):
    admin, srv = world["admin"], world["srv"]
    ibc_before, citric_before, sorbate_before = on_hand(srv, IBC), on_hand(srv, "Citric Acid"), on_hand(srv, "Potassium Sorbate")
    report = str(tmp_path / "report.csv")
    code, out = run_tool(world, "--run", report=report)
    assert code == 0, out
    assert on_hand(srv, "Citric Acid") == citric_before - 128                                                      # 4 passes x 32 kg
    assert on_hand(srv, "Potassium Sorbate") == sorbate_before - 52                                                 # 4 passes x 52 L of 25 % w/v stock = 52 kg (it was NOT deducted before the concentration was set)
    runs = runs_by_lot(admin)
    assert sorted(runs) == ["PR-20260727-001", "PR-20260727-002", "PR-20260729-003"]
    ids = [runs[l]["id"] for l in ("PR-20260727-001", "PR-20260727-002", "PR-20260729-003")]
    assert ids == sorted(ids)                                                                           # created oldest first
    r1, r2, r3 = (runs[l] for l in ("PR-20260727-001", "PR-20260727-002", "PR-20260729-003"))
    assert (r1["runDate"], r1["status"], r1["sku"], r1["operators"], r1["location"]) == ("2026-07-27", "completed", "KELPIVEX", "DP, AL", "Port Edward")
    assert r1["finalizedAt"].startswith("2026-07-27T12:00") and r3["finalizedAt"].startswith("2026-07-29T12:00")          # stamped as of the run date
    assert r1["historical"]["release"] == "grandfather" and r2["historical"]["release"] == "review" and r1["historical"]["deductInventory"] is True
    assert r1["release"]["state"] == "legacy" and r2["release"]["state"] == "pending_review"
    assert (r1["outputLitres"], r1["ibcUsed"], r2["outputLitres"], r2["ibcUsed"], r3["ibcUsed"]) == (3000, 3, 4000, 4, 2)
    assert [i["weightKg"] for i in r1["inputs"]] == [996, 969] and len(r2["dilutionPasses"]) == 1 and len(r1["dilutionPasses"]) == 0
    assert [p["receivingTanks"] for p in r2["dilutionPasses"]] == ["6B"] and r2["stages"]["pasteurization"]["receivingTanks"] == "6A"
    st1 = r1["stages"]
    assert (st1["extraction"]["amplitudePct"], st1["extraction"]["startingPowerW"], st1["packaging"]["qcPh"], st1["packaging"]["tdsPct"]) == (100, 4775, 4.09, 1.8)
    assert st1["dilution"]["tank6aFinalL"] == 5243 and st1["dilution"]["ksorbateAddedL6a"] == 52 and st1["dilution"]["citricKg"] == 32
    assert st1["extraction"]["pressurePsi"] is None and st1["homogenization"]["startedAt"] is None             # not recorded: NOT filled with a default
    assert st1["homogenization"]["wetSolidsWtG"] is None and st1["separation"]["wetSolidsWtKg"] is None
    notes = " ".join(n["body"] for n in r1["logNotes"])
    assert "NOT RECORDED on the paper log" in notes and "Homogenization: Started at" in notes and "Extraction: Pressure (psi)" in notes
    assert "Potassium sorbate stock solution taken as 25 % w/v" in notes and "Initial pH / Citric added" in notes and "Assumptions made when transcribing (N. Wrana 2026-10-26)" in notes and "Farm J = JAM" in notes and "Lot-1" in notes
    assert "Did not pH adjust." in notes and any(n["section"] == "homogenization" for n in r1["logNotes"])
    names = sorted(a["filename"] for a in r1["attachments"])
    assert len(r1["logNoteImages"]) == 1 and "L1_Homog_note.jpg" in names and "L1_Solids1.jpg" in names
    assert all(i["surfacePhoto"] and i["striationPhoto"] for i in r1["inputs"])                                      # the feedstock photos are on each feedstock card
    for i in r1["inputs"]:                                                                                           # ... and they OPEN: each is a stored document of this run (not an id from another table)
        for key in ("surfacePhoto", "striationPhoto"):
            got = admin.get("/api/production/%d/attachments/%s/download" % (r1["id"], i[key]))
            assert got.status == 200 and got.body == JPG, (i["toteLot"], key, got.status)
    assert sorted(a["filename"] for a in r1["attachments"] if "Tote" in a["filename"]) == ["L1_Tote1_Settling.jpg", "L1_Tote1_Surface.jpg", "L1_Tote2_Settling.jpg", "L1_Tote2_Surface.jpg"]
    assert all(t["status"] == "consumed" for t in admin.get("/api/totes").json["totes"] if t["lot"] in world["totes"])
    assert on_hand(srv, IBC) == ibc_before - 9                                                          # the nine IBCs were deducted (stock is counted for a historical run)
    assert sql(srv, "SELECT COUNT(*) FROM production_runs WHERE status='draft'")[0][0] == 0
    rows = [l.split(",") for l in open(report, encoding="utf-8").read().splitlines()[1:]]
    assert rows and all(r[5] == "PASS" for r in rows), rows                                              # the reconciliation against the workbook's Overview sheet
    assert any(r[2] == "extraction efficiency %" for r in rows) and any(r[2] == "release state" for r in rows)
    assert {r[2] for r in rows} >= {"feedstock photos that open", "documents attached", "note pictures"}               # the photos are CHECKED to open, not just counted
    assert ADMIN_PASSWORD not in open(report, encoding="utf-8").read() and ADMIN_PASSWORD not in out       # the password is never written anywhere
    assert admin.get("/api/integrity").json["summary"]["error"] == 0


def test_a_second_run_skips_what_is_already_there_and_changes_nothing(world):
    code, _out = run_tool(world, "--run")
    assert code == 0
    before = sql(world["srv"], "SELECT (SELECT COUNT(*) FROM production_runs), (SELECT COUNT(*) FROM consumable_txns), (SELECT COUNT(*) FROM run_attachments)")
    code, out = run_tool(world, "--run")
    assert code == 0 and out.count("already on this server: skipped") == 3
    assert sql(world["srv"], "SELECT (SELECT COUNT(*) FROM production_runs), (SELECT COUNT(*) FROM consumable_txns), (SELECT COUNT(*) FROM run_attachments)") == before


def test_a_failure_stops_the_load_discards_that_lot_and_leaves_the_earlier_ones(world, monkeypatch):
    real = T.Api.call
    state = {"finalized": 0}

    def flaky(self, method, path, body=None):
        if path.endswith("/finalize"):
            state["finalized"] += 1
            if state["finalized"] == 2:
                raise T.ApiFailure(500, "boom", method, path)
        return real(self, method, path, body)
    monkeypatch.setattr(T.Api, "call", flaky)
    ibc_before = on_hand(world["srv"], IBC)
    code, out = run_tool(world, "--run")
    assert code == 1 and "FAILED" in out and "the draft was discarded" in out and "STOPPED" in out
    runs = runs_by_lot(world["admin"])
    assert sorted(runs) == ["PR-20260727-001"]                                                          # lot 1 stays, lot 2 left no trace, lot 3 was never started
    assert sql(world["srv"], "SELECT COUNT(*) FROM production_runs WHERE status='draft'")[0][0] == 0
    assert on_hand(world["srv"], IBC) == ibc_before - 3                                                  # only lot 1's three IBCs
    assert {t["lot"]: t["status"] for t in world["admin"].get("/api/totes").json["totes"] if t["lot"] in world["totes"]} == dict(
        {world["totes"][0]: "consumed", world["totes"][1]: "consumed"}, **{l: "in_stock" for l in world["totes"][2:]})
    monkeypatch.setattr(T.Api, "call", real)
    code, out = run_tool(world, "--run")                                                                 # fixed: the run continues where it stopped
    assert code == 0 and "already on this server: skipped" in out and len(runs_by_lot(world["admin"])) == 3


def test_a_lot_that_differs_from_the_workbook_overview_is_reported_as_a_difference(world, tmp_path):
    plans, _sha, _p = T.load_plans(world["wb"], world["mapping"])
    code, _out = run_tool(world, "--run", "--only", "1")
    assert code == 0
    api = T.Api(world["srv"].url)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert all(s == "PASS" for _m, _e, _g, s in T.reconcile(api, plans[0]))
    plans[0]["expect"]["outputL"] = 12345
    res = {m: s for m, _e, _g, s in T.reconcile(api, plans[0])}
    assert res["output L"] == "DIFFERENCE" and res["feedstock kg (measured)"] == "PASS"


def test_the_historical_entry_setting_is_never_switched_by_the_tool(world):
    run_tool(world, "--run")
    assert float(T.setting_value(world["admin"].get("/api/refdata").json, "historical_entry_enabled")) == 1


def test_live_needs_the_typed_confirmation_and_cannot_be_skipped_with_yes(world, monkeypatch):
    with Server() as live_like:
        admin = live_like.admin_client()
        admin.put("/api/settings/historical_entry_enabled", {"value": 1})
        ids = harvest_totes(admin, n=6, site="JAM", species="SL", date="2026-05-04")
        by_id = {t["id"]: t["lot"] for t in admin.get("/api/totes").json["totes"]}
        lots = [dict(world["lots"][i // 2], totes=[(by_id[ids[i]], 996), (by_id[ids[i + 1]], 969)]) for i in (0, 2, 4)]
        make_workbook(world["wb"], lots)
        monkeypatch.setattr("builtins.input", lambda prompt="": "no thanks")
        code, out = run_tool(world, "--run", server=live_like, expect="live")
        assert code == 2 and "Cancelled; nothing was created." in out and admin.get("/api/production").json["runs"] == []
        monkeypatch.setattr("builtins.input", lambda prompt="": "LOAD LIVE")
        code, out = run_tool(world, "--run", server=live_like, expect="live")
        assert code == 0 and len(runs_by_lot(admin)) == 3


def test_the_tool_uses_only_the_standard_library():
    tree = ast.parse(open(os.path.join(ROOT, "tools", "import_historical_runs.py"), encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_several_files_in_one_cell_and_a_caption_after_the_names_are_read_correctly():
    assert T.file_names("L2_Solids1.jpg, L2_Solids1_2.jpg") == ["L2_Solids1.jpg", "L2_Solids1_2.jpg"]
    note = "Photos: L1_Notes-photo.jpg, L1_Notes-photo_2.jpg (Left is before extraction. Right is after.)"
    assert T.photos_from_note(note) == ["L1_Notes-photo.jpg", "L1_Notes-photo_2.jpg"]
    assert T.photo_caption(note) == "Left is before extraction. Right is after."
    assert T.photo_caption("Photos: a.jpg, b.jpg") is None and T.photos_from_note("No pictures here") == []
    lot = T.parse_lot_sheet([["Lot 1 - 2026-07-27 - entry sheet"], [], [], [], ["5. Extraction"], ["Extraction", "Notes", "Left to run overnight.", None, note],
                             ["6. Separation"], ["Documents", "Solids photo 1", "S1.jpg, S1_2.jpg"]], 1)
    plan = T.build_plan(lot, {"sku": "SACC-LKE", "release": "review"}, 1, T.DEFAULT_LOT_FORMAT, {})
    assert plan["documents"] == ["S1.jpg", "S1_2.jpg"]
    n = [x for x in plan["notes"] if x["section"] == "extraction"][0]
    assert n["photos"] == ["L1_Notes-photo.jpg", "L1_Notes-photo_2.jpg"] and "Left is before extraction. Right is after." in n["body"] and n["body"].startswith("Left to run overnight.")


def test_a_photo_that_does_not_open_from_the_run_is_reported_as_a_difference(world):
    plans, _sha, _p = T.load_plans(world["wb"], world["mapping"])
    code, _out = run_tool(world, "--run", "--only", "1")
    assert code == 0
    api = T.Api(world["srv"].url)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert {m: s for m, _e, _g, s in T.reconcile(api, plans[0], str(world["tmp"]))}["feedstock photos that open"] == "PASS"
    sql(world["srv"], "UPDATE run_inputs SET surface_photo='999999' WHERE run_id=(SELECT id FROM production_runs WHERE processing_lot='PR-20260727-001')")     # an id that is not a stored document of the run
    res = {m: (e, g, s) for m, e, g, s in T.reconcile(api, plans[0], str(world["tmp"]))}
    assert res["feedstock photos that open"] == (4, 2, "DIFFERENCE")


def test_a_load_that_would_take_nothing_out_of_inventory_for_a_reagent_added_is_refused(world, capsys):
    mapping = dict(world["mapping"])
    del mapping["ksorbateStockPct"]
    json.dump(mapping, open(world["mp"], "w"))
    code = T.main(["--check", "--workbook", world["wb"], "--mapping", world["mp"], "--photos", str(world["tmp"])])
    out = capsys.readouterr().out
    assert code == 2 and "Potassium sorbate was added" in out and "ksorbateStockPct" in out and "nothing would be taken out of inventory" in out


# --------------------------------------------------------------------------- #
# KW-98: bypassed extraction, a pass without a tank, no dilution at all and a run with no output (Lots 10 to 13 of the real load)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def special(tmp_path):
    """Like `world`, with the cases Lots 1 to 9 did not have: lot 10 bypassed extraction, lot 12 bypassed extraction and ONE pass with no tank and no preservative, lot 13 nothing done after the
    inputs (no extraction, no dilution, no output)."""
    with Server(env={"KELP_ERP_ENV": "staging"}) as srv:
        admin = srv.admin_client()
        assert admin.put("/api/settings/historical_entry_enabled", {"value": 1}).status == 200
        ids = harvest_totes(admin, n=6, site="JAM", species="SL", date="2026-05-06")
        by_id = {t["id"]: t["lot"] for t in admin.get("/api/totes").json["totes"]}
        lots_of = [by_id[i] for i in ids]
        lots = [dict(no=10, date="2026-10-20", totes=[(lots_of[0], 1009), (lots_of[1], 961)], passes=["6A"], ibcs=3, final=(3.9, 1.8, 2.2, 2.1), final_mc=98.3, slurry=True, homog_tds=2.9, ext_tds=2.9, bypass=True),
                dict(no=12, date="2026-10-26", totes=[(lots_of[2], 976), (lots_of[3], 1093)], passes=[None], ibcs=4, final=(3.8, 1.8, 2.7, 2.5), homog_tds=3.2, ext_tds=3.2, bypass=True),
                dict(no=13, date="2026-10-27", totes=[(lots_of[4], 948), (lots_of[5], 971)], passes=[], no_passes=True, no_output=True, no_extraction=True, ibcs=0, final=(None, None, None, None),
                     homog_tds=3.0, ext_tds=3.0)]
        wb = str(tmp_path / "entry.xlsx")
        photos = make_workbook(wb, lots)
        for lot_no, fn in photos:
            d = tmp_path / ("Lot_%d" % lot_no) / "photos"
            d.mkdir(parents=True, exist_ok=True)
            (d / fn).write_bytes(JPG)
        mapping = {"assumptionsAcceptedBy": "N. Wrana 2026-11-12", "ksorbateStockPct": 25, "lotNumberFormat": "PR-{date}-{no:03d}",
                   "lots": {"10": {"sku": "KELPIVEX", "release": "review"}, "12": {"sku": "KELPIVEX", "release": "review"}, "13": {"sku": "KELPIVEX", "release": "grandfather"}}}
        mp = str(tmp_path / "mapping.json")
        with open(mp, "w") as f:
            json.dump(mapping, f)
        yield dict(srv=srv, admin=admin, wb=wb, mp=mp, mapping=mapping, lots=lots, tmp=tmp_path, totes=lots_of)


def special_plans(special):
    plans, _sha, problems = T.load_plans(special["wb"], special["mapping"])
    return {p["lot"]: p for p in plans}, problems


def test_check_shows_a_bypassed_extraction_no_output_and_no_dilution(special, capsys):
    code = T.main(["--check", "--workbook", special["wb"], "--mapping", special["mp"], "--photos", str(special["tmp"])])
    out = capsys.readouterr().out
    assert code == 0 and "No problems" in out, out
    assert "PR-20261020-010" in out and "PR-20261026-012" in out and "PR-20261027-013" in out               # {no}: the paper lot number, not the order in this workbook
    assert out.count("extraction BYPASSED (reason: Bypassed extraction)") == 2
    assert "NO OUTPUT (no finished goods): lost down the drain due to operator error" in out and "NO dilution passes" in out


def test_a_bypassed_extraction_sends_only_the_bypass_and_its_reason_and_lists_no_extraction_gaps(special):
    plans, problems = special_plans(special)
    assert not problems and not plans[10]["problems"], plans[10]["problems"]
    assert plans[10]["stages"]["extraction"] == {"bypassed": True, "bypassComment": "Bypassed extraction"}
    assert not [g for g in plans[10]["gaps"] if g.startswith("Extraction")]
    assert any("Paper extraction parameters" in x for x in plans[10]["extra"])                              # the paper values are kept in the notes, not dropped
    notes = " ".join(n["body"] for n in plans[10]["notes"])
    assert "Extraction was bypassed on the paper log (reason entered: Bypassed extraction)" in notes
    # a value typed on an Extraction row of a bypassed lot is not sent (the server would clear it) and is said so
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 10"], 10)
    lot["stages"]["extraction"][("Extraction", "Amplitude (%)")] = T.Cell("100")
    plan = T.build_plan(lot, special["mapping"]["lots"]["10"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert plan["stages"]["extraction"] == {"bypassed": True, "bypassComment": "Bypassed extraction"} and any("amplitudePct=100.0" in x for x in plan["extra"])


def test_a_bypass_without_its_reason_stops_and_a_reason_without_a_bypass_too(special):
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 10"], 10)
    del lot["stages"]["extraction"][("Extraction", "Reason bypassed")]
    plan = T.build_plan(lot, special["mapping"]["lots"]["10"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert any("Extraction is bypassed" in x and "reason" in x for x in plan["problems"])
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 10"], 10)
    lot["stages"]["extraction"][("Extraction", "Bypassed")] = T.Cell("No")
    plan = T.build_plan(lot, special["mapping"]["lots"]["10"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert any("reason for bypassing is given but the row 'Bypassed' is not Yes" in x for x in plan["problems"])
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 10"], 10)
    lot["stages"]["extraction"][("Extraction", "Bypassed")] = T.Cell("maybe")
    plan = T.build_plan(lot, special["mapping"]["lots"]["10"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert any("cannot read \"maybe\" as yes or no" in x for x in plan["problems"])


def test_no_packaging_and_no_dilution_stop_the_load_unless_the_workbook_says_so_explicitly(special):
    plans, problems = special_plans(special)
    assert not plans[13]["problems"] and plans[13]["noOutput"] == "lost down the drain due to operator error" and plans[13]["noPasses"] is True and plans[13]["packaging"] == []
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 13"], 13)
    choice, fmt = special["mapping"]["lots"]["13"], special["mapping"]["lotNumberFormat"]
    del lot["stages"]["packaging"][("Packaging", "Container and quantity")]
    del lot["stages"]["dilution"][("Dilution", "Passes")]
    plan = T.build_plan(lot, choice, 1, fmt, special["mapping"])
    assert any("No packaging" in x for x in plan["problems"]) and any("No dilution pass found" in x for x in plan["problems"])
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 13"], 13)
    lot["stages"]["packaging"][("Packaging", "Container and quantity")] = T.Cell("(no output)")                       # the marker alone is not enough: the paper log's reason is required
    assert any("needs the reason" in x for x in T.build_plan(lot, choice, 1, fmt, special["mapping"])["problems"])
    lot["stages"]["packaging"][("Packaging", "Container and quantity")] = T.Cell(IBC + " x 2")
    plan = T.build_plan(lot, choice, 1, fmt, special["mapping"])
    assert not any("No packaging" in x for x in plan["problems"]) and plan.get("noOutput") is None                  # a real container and quantity is entered as always
    lot12 = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 12"], 12)                                          # '(no passes)' next to a listed pass is a contradiction
    lot12["stages"]["dilution"] = {("Dilution", "Passes"): T.Cell("(no passes) none")}
    plan = T.build_plan(lot12, special["mapping"]["lots"]["12"], 1, fmt, special["mapping"])
    assert any("also lists a dilution pass" in x for x in plan["problems"])


def test_a_pass_with_no_tank_is_accepted_only_when_the_sheet_says_not_recorded(special):
    plans, _ = special_plans(special)
    p = plans[12]
    assert not p["problems"], p["problems"]
    assert "Dilution pass 1: receiving tank" in p["gaps"] and p["stages"]["pasteurization"]["receivingTanks"] is None
    assert p["stages"]["dilution"] == {"measuredPh": 4.1, "citricKg": 25.0} and p["passes"] == []
    assert p["stages"]["packaging"]["qcPh"] == 3.8                                                                    # the final check still goes to the packaging QC check
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 12"], 12)
    lot["passes"][0]["tankNotRecorded"] = False
    plan = T.build_plan(lot, special["mapping"]["lots"]["12"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert any("receiving tank is not shown on the pass title" in x for x in plan["problems"])
    lot = T.parse_lot_sheet(T.read_workbook(special["wb"])["Lot 12"], 12)
    lot["passes"][0]["rows"][("Dilution", "Ksorbate added (L)")] = T.Cell("48")                                         # a preservative needs a tank to be charged to
    plan = T.build_plan(lot, special["mapping"]["lots"]["12"], 1, special["mapping"]["lotNumberFormat"], special["mapping"])
    assert any("Ksorbate added (L) needs the tank" in x for x in plan["problems"])


def test_the_special_runs_are_loaded_and_reconcile_with_the_workbook(special, tmp_path):
    admin, srv = special["admin"], special["srv"]
    citric_before, ibc_before = on_hand(srv, "Citric Acid"), on_hand(srv, IBC)
    report = str(tmp_path / "report.csv")
    code, out = run_tool(special, "--run", report=report)
    assert code == 0, out
    runs = runs_by_lot(admin)
    assert sorted(runs) == ["PR-20261020-010", "PR-20261026-012", "PR-20261027-013"]
    r10, r12, r13 = (runs[l] for l in ("PR-20261020-010", "PR-20261026-012", "PR-20261027-013"))
    assert r10["stages"]["extraction"]["bypassed"] is True and r12["stages"]["extraction"]["bypassed"] is True and not r13["stages"]["extraction"].get("bypassed")
    assert "Bypassed extraction" in (r10["stages"]["extraction"].get("bypassComment") or "") and r10["stages"]["extraction"].get("amplitudePct") is None
    assert (r10["ibcUsed"], r12["ibcUsed"], r13["ibcUsed"], r13["outputLitres"]) == (3, 4, 0, 0)
    assert r12["stages"]["dilution"]["citricKg"] == 25 and r12["dilutionPasses"] == [] and r12["stages"]["pasteurization"].get("receivingTanks") in (None, "")
    assert r12["stages"]["packaging"]["qcPh"] == 3.8 and r10["stages"]["dilution"]["ksorbateAddedL6a"] == 52
    assert (r10["stages"]["homogenization"]["tsSlurryPct"], r10["stages"]["homogenization"]["rhoSlurryGMl"], r10["stages"]["packaging"]["tsLiquidPct"]) == (95.6, 1.018, 98.3)       # the sampling record's slurry and final-product moisture
    assert on_hand(srv, "Citric Acid") == citric_before - 32 - 25                                                    # lot 10 pass (32) + lot 12 (25); lot 13 added none
    assert on_hand(srv, IBC) == ibc_before - 3 - 4                                                                    # lot 13 produced nothing
    assert r13["release"]["state"] == "legacy" and r10["release"]["state"] == "pending_review"
    assert sql(srv, "SELECT COUNT(*) FROM fg_lots WHERE run_id=?", (r13["id"],))[0][0] == 0
    assert sql(srv, "SELECT COALESCE(SUM(qty), 0) FROM fg_lots WHERE run_id=?", (r10["id"],))[0][0] == 3                      # one finished-goods lot of 3 IBCs (a lot per container unit)
    assert all(t["status"] == "consumed" for t in admin.get("/api/totes").json["totes"] if t["lot"] in special["totes"])      # lot 13's two totes are consumed too, none is left in WIP
    notes13 = " ".join(n["body"] for n in r13["logNotes"])
    assert "NO OUTPUT: this run produced no finished goods. Paper log: lost down the drain due to operator error." in notes13 and "No dilution or preservation was recorded" in notes13
    assert "Extraction was bypassed on the paper log" in " ".join(n["body"] for n in r12["logNotes"])
    rows = [l.split(",") for l in open(report, encoding="utf-8").read().splitlines()[1:]]
    assert rows and all(r[5] == "PASS" for r in rows), rows
    metrics = {(r[0], r[2]) for r in rows}
    assert ("10", "extraction bypassed") in metrics and ("13", "no finished goods") in metrics and ("13", "dilution passes") in metrics
    assert admin.get("/api/integrity").json["summary"]["error"] == 0
