"""KW-95: the feedstock CSV import dates the pH / ORP / % TDS readings, and a row that names an existing lot (lotNumber) UPDATES that tote.
The newest reading date is the current value (the rule of Update feedstock conditions); an older reading is kept as history. The file is checked first and is all or nothing,
and the check shows exactly what the import will do."""
import datetime
import itertools
import os

import pytest

from factories import sql
from harness import Server

TODAY = datetime.date.today()
COLS = ["lotNumber", "site", "species", "harvestDate", "toteNumber", "avgWeightKg", "receivedDate", "grind", "stabilizationMethod", "storageUnit", "storageSource", "location",
        "ph", "phDate", "orp", "orpDate", "tdsPct", "tdsDate", "status", "notes", "reason"]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_days = itertools.count()


def day(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def csv(*rows):
    cols = [c for c in COLS if any(c in r for r in rows)]
    return "\r\n".join([",".join(cols)] + [",".join(str(r.get(c, "")) for c in cols) for r in rows]) + "\r\n"


def check(admin, *rows):
    return admin.post("/api/harvest/bulk", {"csvText": csv(*rows), "dryRun": True})


def load(admin, *rows):
    return admin.post("/api/harvest/bulk", {"csvText": csv(*rows)})


def tote_of(admin, lot):
    return [t for t in admin.get("/api/totes").json["totes"] if t["lot"] == lot][0]


def state(admin, tid):
    return admin.get("/api/totes/%d/ph" % tid).json


def log(admin, tid, field):
    return [e for e in state(admin, tid)["stabilityLog"] if e["field"] == field]


def new_lot(admin, **cells):
    """Create one tote through the import (harvest date is unique per call) and return (lot number, id)."""
    date = (datetime.date(2025, 1, 1) + datetime.timedelta(days=next(_days))).isoformat()
    r = load(admin, dict({"site": "JAM", "species": "SL", "harvestDate": date}, **cells))
    assert r.status == 200, r.text
    lot = r.json["created"][0]
    return lot, tote_of(admin, lot)["id"]


def problems(r):
    assert r.status == 400 and r.json["code"] == "import_errors", r.text
    return {(p["row"], p["column"]): p["message"] for p in r.json["problems"]}


def counts(srv):
    return sql(srv, "SELECT (SELECT COUNT(*) FROM tote_lots), (SELECT COUNT(*) FROM tote_stability_log), (SELECT COUNT(*) FROM location_moves)")[0]


# ---- reading dates on a NEW tote ----

def test_a_new_tote_takes_its_reading_dates_from_the_file(srv, admin):
    lot, tid = new_lot(admin, ph=3.9, phDate=day(10), orp=-120, orpDate=day(9), tdsPct=11.5, tdsDate=day(8))
    s = state(admin, tid)
    assert (s["ph"], s["phUpdated"], s["orp"], s["orpUpdated"], s["tdsPct"], s["tdsUpdated"]) == (3.9, day(10), -120, day(9), 11.5, day(8))
    dated = {e["field"]: e["readingDate"] for e in s["stabilityLog"] if e["readingDate"]}
    assert dated == {"pH": day(10), "ORP (mV)": day(9), "Feedstock TDS (%)": day(8)}
    assert len(log(admin, tid, "Imported")) == 1


def test_without_a_date_the_import_day_is_used_and_the_check_says_so(srv, admin):
    date = (datetime.date(2025, 1, 1) + datetime.timedelta(days=next(_days))).isoformat()
    row = {"site": "JAM", "species": "SL", "harvestDate": date, "ph": 3.9, "orp": 100}
    c = check(admin, row)
    assert c.status == 200 and any("2 readings have no reading date" in w and TODAY.isoformat() in w for w in c.json["warnings"]), c.text
    lot = load(admin, row).json["created"][0]
    tid = tote_of(admin, lot)["id"]
    s = state(admin, tid)
    assert (s["phUpdated"], s["orpUpdated"]) == (TODAY.isoformat(), TODAY.isoformat())
    assert log(admin, tid, "pH") == []                                                    # exactly the old behaviour: only the Imported line


def test_a_tote_without_readings_gets_no_warning(srv, admin):
    c = check(admin, {"site": "JAM", "species": "SL", "harvestDate": "2025-12-01"})
    assert c.status == 200 and not c.json["warnings"]


# ---- the newest reading date wins ----

def test_a_second_older_reading_is_kept_as_history_and_the_current_value_stays(srv, admin):
    lot, tid = new_lot(admin, ph=4.1, phDate=day(5))
    r = load(admin, {"lotNumber": lot, "ph": 4.4, "phDate": day(9)})
    assert r.status == 200 and (r.json["updated"], r.json["unchanged"], r.json["created"]) == (1, 0, [])
    f = r.json["changes"][0]["fields"][0]
    assert (f["field"], f["new"], f["historical"], f["date"]) == ("pH", "4.4", True, day(9))
    s = state(admin, tid)
    assert (s["ph"], s["phUpdated"]) == (4.1, day(5))
    old = log(admin, tid, "pH")[0]                                                        # newest entry first
    assert old["readingDate"] == day(9) and old["newValue"] == "4.4" and not old["oldValue"] and "Historical reading" in old["note"] and "4.1" in old["note"]
    assert "Row 2 of the bulk import" in old["note"] and old["by"]            # row 1 is the header


def test_a_newer_reading_becomes_the_current_value(srv, admin):
    lot, tid = new_lot(admin, ph=4.1, phDate=day(5))
    r = load(admin, {"lotNumber": lot, "ph": 3.9, "phDate": day(2)})
    assert r.status == 200 and r.json["changes"][0]["fields"][0]["historical"] is False
    s = state(admin, tid)
    assert (s["ph"], s["phUpdated"]) == (3.9, day(2))
    new = log(admin, tid, "pH")[0]
    assert (new["oldValue"], new["newValue"], new["readingDate"]) == ("4.1", "3.9", day(2))


def test_orp_and_tds_follow_the_same_rule_each_on_its_own(srv, admin):
    lot, tid = new_lot(admin, orp=100, orpDate=day(4), tdsPct=12, tdsDate=day(4))
    r = load(admin, {"lotNumber": lot, "orp": -50, "orpDate": day(8), "tdsPct": 11, "tdsDate": day(1)})
    assert r.status == 200
    assert {f["field"]: f["historical"] for f in r.json["changes"][0]["fields"]} == {"ORP (mV)": True, "Feedstock TDS (%)": False}
    s = state(admin, tid)
    assert (s["orp"], s["orpUpdated"], s["tdsPct"], s["tdsUpdated"]) == (100, day(4), 11, day(1))


def test_the_same_reading_again_changes_nothing(srv, admin):
    lot, tid = new_lot(admin, ph=4.1, phDate=day(5))
    row = {"lotNumber": lot, "ph": 4.1, "phDate": day(5)}
    before = len(state(admin, tid)["stabilityLog"])
    r = load(admin, row)
    assert r.status == 200 and (r.json["updated"], r.json["unchanged"], r.json["changes"]) == (0, 1, [])
    assert len(state(admin, tid)["stabilityLog"]) == before


def test_one_lot_on_several_rows_is_applied_in_file_order(srv, admin):
    lot, tid = new_lot(admin)
    r = load(admin, {"lotNumber": lot, "ph": 4.0, "phDate": day(10)}, {"lotNumber": lot, "ph": 3.8, "phDate": day(20)}, {"lotNumber": lot, "ph": 4.4, "phDate": day(15)})
    assert r.status == 200 and r.json["updated"] == 3
    s = state(admin, tid)
    assert (s["ph"], s["phUpdated"]) == (4.0, day(10))                                    # day(10) is the newest of the three, whatever the order of the rows
    assert sorted(e["readingDate"] for e in log(admin, tid, "pH")) == sorted([day(20), day(15), day(10)])
    assert [e["readingDate"] for e in log(admin, tid, "pH")][::-1] == [day(10), day(20), day(15)]       # logged in file order


def test_a_grade_follows_the_newest_reading(srv, admin):
    put = admin.put("/api/grade-rules", {"defaultGrade": "Good", "rules": [{"grade": "Poor", "groups": [[{"field": "ph", "op": "lt", "value": 4}]]}]})
    assert put.status == 200, put.text
    try:
        lot, tid = new_lot(admin, ph=4.5, phDate=day(5))
        assert tote_of(admin, lot)["grade"] == "Good"
        load(admin, {"lotNumber": lot, "ph": 3.5, "phDate": day(9)}).raise_for_status()   # older and low: history only
        assert tote_of(admin, lot)["grade"] == "Good"
        load(admin, {"lotNumber": lot, "ph": 3.6, "phDate": day(1)}).raise_for_status()
        assert tote_of(admin, lot)["grade"] == "Poor"
    finally:
        admin.put("/api/grade-rules", {"defaultGrade": "Ungraded", "rules": []}).raise_for_status()


# ---- what else an update row may change ----

def test_blank_cells_leave_values_alone_and_new_tote_defaults_do_not_apply(srv, admin):
    lot, tid = new_lot(admin, avgWeightKg=150, grind="Fine", stabilizationMethod="Fresh", storageUnit="Bag", location="Dock A", notes="first")
    r = load(admin, {"lotNumber": lot, "ph": 3.8, "phDate": day(1)})
    assert r.status == 200
    t = tote_of(admin, lot)
    assert (t["avgWeightKg"], t["grind"], t["stabilizationMethod"], t["storageUnit"], t["location"], t["notes"]) == (150, "Fine", "Fresh", "Bag", "Dock A", "first")


def test_every_other_column_can_be_updated_and_each_change_is_logged(srv, admin):
    lot, tid = new_lot(admin, avgWeightKg=150, grind="Coarse", stabilizationMethod="Citric acid", storageUnit="Tote", storageSource="IBC", location="Dock A", notes="first")
    r = load(admin, {"lotNumber": lot, "avgWeightKg": 175.5, "grind": "fine", "stabilizationMethod": "fresh", "storageUnit": "bag", "storageSource": "Burlap", "location": "Cold Room 3",
                     "notes": "second", "receivedDate": day(3)})
    assert r.status == 200, r.text
    t = tote_of(admin, lot)
    assert (t["avgWeightKg"], t["grind"], t["stabilizationMethod"], t["storageUnit"], t["storageSource"], t["location"], t["notes"], t["receivedDate"]) == \
        (175.5, "Fine", "Fresh", "Bag", "Burlap", "Cold Room 3", "second", day(3))
    fields = {f["field"]: (f["old"], f["new"]) for f in r.json["changes"][0]["fields"]}
    assert fields["Weight (kg)"] == ("150", "175.5") and fields["Grind"] == ("Coarse", "Fine") and fields["Location"] == ("Dock A", "Cold Room 3") and fields["Received date"] == ("", day(3))
    for field in ("Weight (kg)", "Grind", "Stabilization method", "Storage unit", "Storage source", "Location", "Notes", "Received date"):
        e = log(admin, tid, field)
        assert len(e) == 1 and e[0]["by"], field
        assert e[0]["note"] == ("Received date filled in" if field == "Received date" else "Row 2 of the bulk import"), field
    moves = admin.get("/api/totes/%d/move" % tid).json["moveLog"]
    assert moves and (moves[0]["from"], moves[0]["to"], moves[0]["date"]) == ("Dock A", "Cold Room 3", TODAY.isoformat())


def test_hold_and_release_by_update(srv, admin):
    lot, tid = new_lot(admin, location="Dock A")
    assert load(admin, {"lotNumber": lot, "status": "hold"}).status == 200
    t = tote_of(admin, lot)
    assert (t["status"], t["location"]) == ("hold", "QAQC Hold")
    p = problems(check(admin, {"lotNumber": lot, "status": "in_stock"}))                  # released without saying where it goes
    assert list(p) == [(2, "status")]
    assert load(admin, {"lotNumber": lot, "status": "in_stock", "location": "Dock B"}).status == 200
    t = tote_of(admin, lot)
    assert (t["status"], t["location"]) == ("in_stock", "Dock B")


def test_a_filled_in_received_date_needs_a_reason_to_change_and_a_blank_one_does_not(srv, admin):
    lot, tid = new_lot(admin)
    assert load(admin, {"lotNumber": lot, "receivedDate": day(6)}).status == 200           # the first fill-in: no reason needed
    p = problems(check(admin, {"lotNumber": lot, "receivedDate": day(4)}))
    assert list(p) == [(2, "reason")]
    ok = load(admin, {"lotNumber": lot, "receivedDate": day(4), "reason": "supplier delivery note"})
    assert ok.status == 200
    e = log(admin, tid, "Received date")[0]
    assert (e["oldValue"], e["newValue"], e["note"]) == (day(6), day(4), "supplier delivery note")
    before = tote_of(admin, lot)["receivedDate"]
    assert load(admin, {"lotNumber": lot, "receivedDate": before}).json["unchanged"] == 1


# ---- problems stop the whole file ----

def test_row_problems_are_reported_with_their_row_and_column(srv, admin):
    lot, tid = new_lot(admin)
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    p = problems(check(admin,
                       {"lotNumber": "JAM-SL-20200101-001", "ph": 4},                                      # row 2: no such lot
                       {"lotNumber": lot, "site": "KOD"},                                                  # row 3: the site is part of the lot number
                       {"lotNumber": lot, "harvestDate": "2024-01-01"},                                    # row 4
                       {"lotNumber": lot, "ph": 4, "phDate": tomorrow},                                    # row 5: future
                       {"lotNumber": lot, "ph": 4, "phDate": "01/02/2026"},                                # row 6: not ISO
                       {"lotNumber": lot, "phDate": day(1)},                                               # row 7: a date without its reading
                       {"lotNumber": lot, "ph": 15},                                                       # row 8: out of range
                       {"lotNumber": lot, "avgWeightKg": 0},                                               # row 9
                       {"lotNumber": lot, "receivedDate": tomorrow}))                                      # row 10
    assert set(p) == {(2, "lotNumber"), (3, "site"), (4, "harvestDate"), (5, "phDate"), (6, "phDate"), (7, "phDate"), (8, "ph"), (9, "avgWeightKg"), (10, "receivedDate")}
    assert "not in Feedstock Inventory" in p[(2, "lotNumber")] and "cannot change" in p[(3, "site")]


def test_a_tote_in_a_run_keeps_its_physical_values_but_readings_and_notes_can_still_be_added(srv, admin):
    lot, tid = new_lot(admin, avgWeightKg=150, grind="Coarse", ph=4.0, phDate=day(3))
    sql(srv, "UPDATE tote_lots SET status='consumed' WHERE id=?", (tid,))
    p = problems(check(admin, {"lotNumber": lot, "avgWeightKg": 99}, {"lotNumber": lot, "grind": "Fine"}, {"lotNumber": lot, "location": "Elsewhere"}))
    assert set(p) == {(2, "avgWeightKg"), (3, "grind"), (4, "location")} and "consumed" in p[(2, "avgWeightKg")]
    r = load(admin, {"lotNumber": lot, "ph": 3.7, "phDate": day(7), "notes": "checked later"})                # an older reading and a note: allowed
    assert r.status == 200 and tote_of(admin, lot)["notes"] == "checked later" and state(admin, tid)["ph"] == 4.0
    same = load(admin, {"lotNumber": lot, "avgWeightKg": 150, "grind": "Coarse"})                           # the same values are not a change
    assert same.status == 200 and same.json["unchanged"] == 1


def test_one_problem_anywhere_means_nothing_changes(srv, admin):
    lot, tid = new_lot(admin, ph=4.0, phDate=day(3))
    before = counts(srv)
    r = admin.post("/api/harvest/bulk", {"csvText": csv({"lotNumber": lot, "ph": 3.5, "phDate": day(1), "notes": "x"}, {"lotNumber": "JAM-SL-20200101-001", "ph": 4})})
    assert list(problems(r)) == [(3, "lotNumber")]
    assert counts(srv) == before and state(admin, tid)["ph"] == 4.0 and tote_of(admin, lot)["notes"] in (None, "")


# ---- the check is exact and writes nothing ----

def test_the_check_lists_exactly_what_the_import_does_and_writes_nothing(srv, admin):
    lot, tid = new_lot(admin, ph=4.1, phDate=day(5), location="Dock A")
    date = (datetime.date(2025, 1, 1) + datetime.timedelta(days=next(_days))).isoformat()
    rows = [{"lotNumber": lot, "ph": 3.9, "phDate": day(2), "location": "Brand New Dock", "notes": "n"},
            {"lotNumber": lot, "orp": 90, "orpDate": day(6)},
            {"site": "JAM", "species": "SL", "harvestDate": date, "ph": 4.2, "phDate": day(1)}]
    before = counts(srv)
    c = check(admin, *rows)
    assert c.status == 200 and c.json["dryRun"] is True
    assert (c.json["count"], c.json["updated"], c.json["unchanged"]) == (1, 2, 0)
    assert counts(srv) == before                                                           # nothing written, not even the new location
    assert sql(srv, "SELECT COUNT(*) FROM locations WHERE name='Brand New Dock'")[0][0] == 0
    real = load(admin, *rows)
    assert real.status == 200
    assert real.json["changes"] == c.json["changes"] and real.json["created"] == c.json["lots"] and real.json["updated"] == c.json["updated"]
    assert state(admin, tid)["ph"] == 3.9


def test_a_file_can_mix_new_totes_and_updates(srv, admin):
    lot, tid = new_lot(admin, ph=4.0, phDate=day(8))
    date = (datetime.date(2025, 1, 1) + datetime.timedelta(days=next(_days))).isoformat()
    r = load(admin, {"site": "JAM", "species": "SL", "harvestDate": date, "avgWeightKg": 120}, {"lotNumber": lot, "ph": 3.9, "phDate": day(2)})
    assert r.status == 200 and len(r.json["created"]) == 1 and r.json["updated"] == 1
    assert state(admin, tid)["ph"] == 3.9


def test_site_species_and_harvest_date_are_still_needed_by_a_row_without_a_lot_number(srv, admin):
    lot, tid = new_lot(admin)
    p = problems(check(admin, {"lotNumber": lot, "ph": 4, "phDate": day(1)}, {"lotNumber": "", "ph": 4}))
    assert set(p) == {(3, "site"), (3, "species"), (3, "harvestDate")} and "new tote" in p[(3, "site")]


def test_a_file_without_a_lot_number_column_behaves_as_before(srv, admin):
    lot, tid = new_lot(admin)
    number = int(lot[-3:])
    t = tote_of(admin, lot)
    p = problems(admin.post("/api/harvest/bulk", {"csvText": "site,species,harvestDate,toteNumber\r\nJAM,SL,%s,%d\r\n" % (t["harvestDate"], number), "dryRun": True}))
    assert list(p) == [(2, "toteNumber")] and "already exists" in p[(2, "toteNumber")]
    missing = problems(admin.post("/api/harvest/bulk", {"csvText": "site,species\r\nJAM,SL\r\n", "dryRun": True}))
    assert (1, "harvestDate") in missing


def test_headers_are_case_insensitive(srv, admin):
    lot, tid = new_lot(admin)
    r = admin.post("/api/harvest/bulk", {"csvText": "LOTNUMBER,PH,PHDATE\r\n%s,3.9,%s\r\n" % (lot, day(2))})
    assert r.status == 200 and state(admin, tid)["ph"] == 3.9


# ---- the template and the screen ----

def test_the_template_and_the_import_window_know_the_new_columns():
    template = open(os.path.join(ROOT, "public", "templates", "feedstock_bulk_import_template.csv"), encoding="utf-8").read().splitlines()[0]
    for c in ("lotNumber", "phDate", "orpDate", "tdsDate", "reason"):
        assert c in template.split(",")
    app = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    start = app.index("async function openFeedstockImport")
    window = app[start:app.index("/* ---------------- Production", start)]
    for text in ("lotNumber", "phDate", "updated", "historical"):
        assert text in window, text
