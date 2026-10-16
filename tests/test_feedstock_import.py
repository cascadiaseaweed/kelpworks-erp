"""Feedstock bulk import (CSV): every column of the inventory that can be set, a manual tote number, and lot numbers that can never repeat.
The whole file is checked first: any problem is reported with its row and column, and then NOTHING is imported."""
import datetime

import pytest

from factories import harvest_totes, sql
from harness import Server

HEAD = "site,species,harvestDate,avgWeightKg,toteNumber,receivedDate,grind,stabilizationMethod,storageUnit,storageSource,location,ph,orp,tdsPct,status,notes"
TODAY = datetime.date.today().isoformat()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def csv(*rows, head=HEAD):
    return "\r\n".join([head, *rows]) + "\r\n"


def row(site="JAM", species="SL", date="2026-08-01", kg="180", tote="", rec="", grind="", stab="", unit="", source="", loc="", ph="", orp="", tds="", status="", notes=""):
    return ",".join([site, species, date, kg, tote, rec, grind, stab, unit, source, loc, ph, orp, tds, status, notes])


def check(admin, text):
    return admin.post("/api/harvest/bulk", {"csvText": text, "dryRun": True})


def load(admin, text):
    return admin.post("/api/harvest/bulk", {"csvText": text})


def problems(r):
    assert r.status == 400 and r.json["code"] == "import_errors", r.text
    return [(p["row"], p["column"]) for p in r.json["problems"]], r.json["problems"]


def count_totes(srv):
    return sql(srv, "SELECT COUNT(*) FROM tote_lots")[0][0]


# ---- every settable column ----

def test_every_column_can_be_filled_and_lands_on_the_tote(srv, admin):
    r = load(admin, csv(row(date="2026-08-02", kg="201.5", tote="12", rec="2026-08-03", grind="fine", stab="fresh", unit="bag", source="Burlap sack", loc="Cold Room 9",
                            ph="3.8", orp="-120", tds="11.5", status="in_stock", notes="from the old sheet")))
    assert r.status == 200, r.text
    assert r.json["created"] == ["JAM-SL-20260802-012"] and any("Cold Room 9" in w for w in r.json["warnings"])
    t = [x for x in admin.get("/api/totes").json["totes"] if x["lot"] == "JAM-SL-20260802-012"][0]
    assert (t["toteNumber"], t["harvestDate"], t["receivedDate"], t["avgWeightKg"], t["grind"], t["stabilizationMethod"], t["storageUnit"], t["storageSource"], t["location"]) == \
        (12, "2026-08-02", "2026-08-03", 201.5, "Fine", "Fresh", "Bag", "Burlap sack", "Cold Room 9")
    assert (t["ph"], t["orp"], t["tdsPct"], t["status"], t["notes"]) == (3.8, -120, 11.5, "in_stock", "from the old sheet")
    log = [e for e in admin.get("/api/totes/%d/ph" % t["id"]).json["stabilityLog"] if e["field"] == "Imported"]
    assert len(log) == 1 and "pH 3.8" in log[0]["note"] and "TDS 11.5" in log[0]["note"] and log[0]["by"]


def test_a_tote_on_hold_sits_at_qaqc_hold_and_the_reverse(srv, admin):
    r = load(admin, csv(row(date="2026-08-03", status="hold"), row(date="2026-08-03", loc="QAQC Hold"), row(date="2026-08-03", status="in_stock", loc="Dock")))
    assert r.status == 200, r.text
    by = {x["lot"]: x for x in admin.get("/api/totes").json["totes"]}
    assert [(by[l]["status"], by[l]["location"]) for l in r.json["created"]] == [("hold", "QAQC Hold"), ("hold", "QAQC Hold"), ("in_stock", "Dock")]


def test_blank_columns_get_the_usual_defaults_and_the_required_ones_are_enough(srv, admin):
    r = load(admin, "site,species,harvestDate,avgWeightKg\r\nJAM,SL,2026-08-04,150\r\n")
    assert r.status == 200 and r.json["count"] == 1
    t = [x for x in admin.get("/api/totes").json["totes"] if x["lot"] == r.json["created"][0]][0]
    assert (t["grind"], t["stabilizationMethod"], t["storageUnit"], t["status"], t["tdsPct"]) == ("Coarse", "Citric acid", "Tote", "in_stock", None)


# ---- tote numbers ----

def test_tote_numbers_can_be_given_or_automatic_and_never_collide_with_each_other(srv, admin):
    r = load(admin, csv(row(date="2026-08-05", tote="50"), row(date="2026-08-05"), row(date="2026-08-05", tote="3"), row(date="2026-08-05")))
    assert r.status == 200, r.text
    # 50 and 3 are fixed by the file; the automatic ones continue after the highest number in use
    assert r.json["created"] == ["JAM-SL-20260805-050", "JAM-SL-20260805-051", "JAM-SL-20260805-003", "JAM-SL-20260805-052"]


def test_a_repeated_number_in_the_file_is_reported_on_the_second_row(srv, admin):
    before = count_totes(srv)
    where, details = problems(check(admin, csv(row(date="2026-08-06", tote="7"), row(date="2026-08-06", tote="8"), row(date="2026-08-06", tote="7"))))
    assert where == [(4, "toteNumber")] and "repeats row 2" in details[0]["message"]
    assert load(admin, csv(row(date="2026-08-06", tote="7"), row(date="2026-08-06", tote="7"))).status == 400
    assert count_totes(srv) == before                                                              # nothing was imported


def test_the_same_number_on_another_date_or_site_is_a_different_lot(srv, admin):
    assert check(admin, csv(row(date="2026-08-07", tote="7"), row(date="2026-08-08", tote="7"), row(site="DIP", date="2026-08-07", tote="7"))).status == 200


def test_a_number_already_in_inventory_is_refused(srv, admin):
    harvest_totes(admin, n=2, site="JAM", species="SL", date="2026-08-09")                         # JAM-SL-20260809-001 and -002 exist
    where, details = problems(check(admin, csv(row(date="2026-08-09", tote="2"))))
    assert where == [(2, "toteNumber")] and "JAM-SL-20260809-002 already exists" in details[0]["message"]
    assert check(admin, csv(row(date="2026-08-09", tote="3"))).status == 200


def test_the_number_of_a_deleted_tote_is_never_reused_by_an_import_or_a_check_in(srv, admin):
    ids = harvest_totes(admin, n=3, site="JAM", species="SL", date="2026-08-10")
    lot = sql(srv, "SELECT lot_number FROM tote_lots WHERE id=?", (ids[-1],))[0][0]               # ...-003, the HIGHEST number
    assert admin.delete("/api/totes/%d" % ids[-1]).status == 200
    where, details = problems(check(admin, csv(row(date="2026-08-10", tote="3"))))
    assert where == [(2, "toteNumber")] and "deleted" in details[0]["message"] and lot in details[0]["message"]
    r = load(admin, csv(row(date="2026-08-10")))                                                   # automatic: not 003 again
    assert r.json["created"] == ["JAM-SL-20260810-004"]
    again = harvest_totes(admin, n=1, site="JAM", species="SL", date="2026-08-10")                  # and the normal check-in has the same protection
    assert sql(srv, "SELECT lot_number FROM tote_lots WHERE id=?", (again[0],))[0][0] == "JAM-SL-20260810-005"


def test_the_tote_number_has_to_be_a_whole_number_from_1_to_999(srv, admin):
    for bad in ("0", "1000", "7.5", "abc", "-2"):
        where, _ = problems(check(admin, csv(row(date="2026-08-11", tote=bad))))
        assert where == [(2, "toteNumber")], bad


def test_the_site_and_species_codes_are_normalized_before_the_lot_is_built(srv, admin):
    r = load(admin, csv(row(site=" jam ", species="sl", date="2026-08-12", tote="9")))
    assert r.status == 200 and r.json["created"] == ["JAM-SL-20260812-009"]
    where, _ = problems(check(admin, csv(row(site="JAM", species="SL", date="2026-08-12", tote="9"))))
    assert where == [(2, "toteNumber")]                                                            # "jam" and "JAM" are the same site


def test_the_database_itself_refuses_a_second_tote_with_the_same_number(srv, admin):
    harvest_totes(admin, n=1, site="JAM", species="SL", date="2026-08-13")
    with pytest.raises(Exception):
        sql(srv, "INSERT INTO tote_lots (lot_number, site_code, species_code, checkin_date, tote_number, status, created_at) VALUES ('MANUAL-DUP', 'JAM', 'SL', '2026-08-13', 1, 'in_stock', 'x')")


# ---- the file is checked as a whole, with feedback ----

def test_every_problem_is_listed_with_its_row_and_column_and_nothing_is_imported(srv, admin):
    before = count_totes(srv)
    text = csv(row(site="", date="2026-08-14"), row(species="ZZ", date="2026-08-14"), row(date="14/08/2026"), row(date="2099-01-01"), row(date="2026-08-14", kg="abc"),
               row(date="2026-08-14", ph="15"), row(date="2026-08-14", orp="abc"), row(date="2026-08-14", tds="101"), row(date="2026-08-14", grind="Chunky"),
               row(date="2026-08-14", status="consumed"), row(date="2026-08-14", status="hold", loc="Dock"), row(date="2026-08-14", rec="2026-08-01"), row(date="2026-08-14"))
    r = check(admin, text)
    where, details = problems(r)
    assert r.json["problemCount"] == len(details) and len(details) >= 12
    assert {w for w in where} >= {(2, "site"), (3, "species"), (4, "harvestDate"), (5, "harvestDate"), (6, "avgWeightKg"), (7, "ph"), (8, "orp"), (9, "tdsPct"), (10, "grind"),
                                  (11, "status"), (12, "location"), (13, "receivedDate")}
    assert all(p["message"] for p in details) and (14, "toteNumber") not in where                  # the one good row has no problem
    assert load(admin, text).status == 400 and count_totes(srv) == before


def test_columns_that_are_unknown_or_missing_or_twice_are_problems_on_row_1(srv, admin):
    where, details = problems(check(admin, "site,species,harvestDate,avgWeightKg,toteNumbr,site\r\nJAM,SL,2026-08-15,100,5,JAM\r\n"))
    assert {(1, "toteNumbr"), (1, "site")} <= set(where) and any("Unknown column" in p["message"] for p in details)
    where, _ = problems(check(admin, "site,species\r\nJAM,SL\r\n"))
    assert where == [(1, "harvestDate")]


def test_a_row_with_too_many_cells_is_a_problem_not_a_silent_shift(srv, admin):
    where, details = problems(check(admin, "site,species,harvestDate,avgWeightKg\r\nJAM,SL,2026-08-16,100,extra\r\n"))
    assert where == [(2, "")] and "more cells" in details[0]["message"]


def test_a_check_changes_nothing_and_reports_the_lots_and_new_locations(srv, admin):
    before = count_totes(srv)
    r = check(admin, csv(row(date="2026-08-17", tote="5", loc="Brand New Dock")))
    assert r.status == 200 and r.json["lots"] == ["JAM-SL-20260817-005"] and any("Brand New Dock" in w for w in r.json["warnings"])
    assert count_totes(srv) == before and not sql(srv, "SELECT 1 FROM locations WHERE name='Brand New Dock'")


def test_header_names_are_case_insensitive_and_a_leading_bom_is_ignored(srv, admin):
    r = load(admin, "﻿SITE,Species,HARVESTDATE,avgweightkg,TOTENUMBER\r\nJAM,SL,2026-08-18,100,21\r\n")
    assert r.status == 200 and r.json["created"] == ["JAM-SL-20260818-021"]


def test_an_empty_file_and_a_huge_file_are_refused(srv, admin):
    assert load(admin, HEAD + "\r\n").status == 400
    assert load(admin, csv(*[row(date="2026-08-19") for _ in range(501)])).status == 400


def test_imported_totes_are_graded_like_any_other(srv, admin):
    assert admin.put("/api/grade-rules", {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[{"field": "ph", "op": "le", "value": 4}]]}]}).status == 200
    r = load(admin, csv(row(date=TODAY, ph="3.5", tote="1"), row(date=TODAY, ph="5.5", tote="2")))
    assert r.status == 200, r.text
    by = {t["lot"]: t["grade"] for t in admin.get("/api/totes").json["totes"]}
    assert (by[r.json["created"][0]], by[r.json["created"][1]]) == ("A", "C")
    admin.put("/api/grade-rules", {"defaultGrade": "Ungraded", "rules": []})


def weights(srv, lots):
    return [sql(srv, "SELECT avg_weight_kg FROM tote_lots WHERE lot_number=?", (l,))[0][0] for l in lots]


def test_the_weight_is_optional_blank_or_the_column_left_out(srv, admin):
    r = load(admin, csv(row(date="2026-09-01", kg=""), row(date="2026-09-01", kg="150.5")))
    assert r.status == 200
    assert weights(srv, r.json["created"]) == [None, 150.5]
    r = load(admin, "site,species,harvestDate\r\nJAM,SL,2026-09-02\r\n")                          # no weight column at all
    assert r.status == 200 and weights(srv, r.json["created"]) == [None]
    where, _ = problems(check(admin, csv(row(date="2026-09-03", kg="0"), row(date="2026-09-03", kg="6000"))))
    assert where == [(2, "avgWeightKg"), (3, "avgWeightKg")]                                      # a weight that IS given is still range-checked


def wlog(admin, tid):
    return [e for e in admin.get("/api/totes/%d/ph" % tid).json["stabilityLog"] if e["field"] == "Weight (kg)"]


def test_one_weight_can_be_set_on_many_totes_at_once_and_each_change_is_logged(srv, admin):
    lots = load(admin, csv(*[row(date="2026-09-10", kg="") for _ in range(3)])).json["created"]
    ids = [sql(srv, "SELECT id FROM tote_lots WHERE lot_number=?", (l,))[0][0] for l in lots]
    r = admin.post("/api/totes/set-bulk", {"ids": ids, "avgWeightKg": 182.5, "note": "batch weighed"})
    assert r.status == 200 and r.json["updated"] == 3 and r.json["unchanged"] == 0
    assert weights(srv, lots) == [182.5] * 3
    for tid in ids:
        e = wlog(admin, tid)
        assert len(e) == 1 and not e[0]["oldValue"] and e[0]["newValue"] == "182.5" and e[0]["by"] and "batch weighed" in e[0]["note"]
    again = admin.post("/api/totes/set-bulk", {"ids": ids, "avgWeightKg": 182.5})                # same value: nothing new is logged
    assert again.json["updated"] == 0 and again.json["unchanged"] == 3 and all(len(wlog(admin, t)) == 1 for t in ids)
    admin.post("/api/totes/set-bulk", {"ids": ids[:1], "avgWeightKg": 190}).raise_for_status()
    assert [(e["oldValue"], e["newValue"]) for e in wlog(admin, ids[0])][0] == ("182.5", "190")


@pytest.mark.parametrize("bad", [0, -5, 5001, "abc", None])
def test_a_bulk_weight_must_be_a_real_weight(srv, admin, bad):
    tid = harvest_totes(admin, n=1)[0]
    assert admin.post("/api/totes/set-bulk", {"ids": [tid], "avgWeightKg": bad}).status == 400
    assert admin.post("/api/totes/set-bulk", {"ids": [], "avgWeightKg": 100}).status == 400


def test_a_bulk_weight_changes_nothing_if_any_tote_is_in_a_run(srv, admin):
    from factories import finalize_run
    free, used = harvest_totes(admin, n=2)
    before = sql(srv, "SELECT avg_weight_kg FROM tote_lots WHERE id=?", (free,))[0][0]
    rid, f = finalize_run(admin, [used])
    assert f.ok, f.text
    r = admin.post("/api/totes/set-bulk", {"ids": [free, used], "avgWeightKg": 123})
    assert r.status == 400 and "cannot be changed" in r.json["error"]
    assert sql(srv, "SELECT avg_weight_kg FROM tote_lots WHERE id=?", (free,))[0][0] == before and wlog(admin, free) == []
    assert admin.post("/api/totes/set-bulk", {"ids": [free, 999999], "avgWeightKg": 123}).status == 404


def test_grind_and_stabilization_method_can_be_set_on_many_totes_and_are_logged(srv, admin):
    lots = load(admin, csv(*[row(date="2026-09-20", grind="Coarse", stab="Citric acid") for _ in range(2)])).json["created"]
    ids = [sql(srv, "SELECT id FROM tote_lots WHERE lot_number=?", (l,))[0][0] for l in lots]
    r = admin.post("/api/totes/set-bulk", {"ids": ids, "grind": "fine", "stabilizationMethod": "fresh", "note": "re-labelled"})
    assert r.status == 200 and r.json["updated"] == 2
    for tid in ids:
        assert sql(srv, "SELECT grind, stabilization_method FROM tote_lots WHERE id=?", (tid,))[0] == ("Fine", "Fresh")
        log = admin.get("/api/totes/%d/ph" % tid).json["stabilityLog"]
        assert {(e["field"], e["oldValue"], e["newValue"]) for e in log if e["field"] in ("Grind", "Stabilization method")} == \
            {("Grind", "Coarse", "Fine"), ("Stabilization method", "Citric acid", "Fresh")}
    assert admin.post("/api/totes/set-bulk", {"ids": ids, "grind": "Fine"}).json["updated"] == 0       # same value: nothing new
    assert sql(srv, "SELECT avg_weight_kg FROM tote_lots WHERE id=?", (ids[0],))[0][0] == 180        # a field left out is untouched


@pytest.mark.parametrize("body", [{"grind": "Chunky"}, {"stabilizationMethod": "Smoked"}, {}, {"grind": "", "avgWeightKg": ""}])
def test_bulk_set_refuses_a_bad_or_empty_request(srv, admin, body):
    tid = harvest_totes(admin, n=1)[0]
    assert admin.post("/api/totes/set-bulk", dict(body, ids=[tid])).status == 400


def test_bulk_set_never_touches_a_tote_in_a_run(srv, admin):
    from factories import finalize_run
    free, used = harvest_totes(admin, n=2)
    rid, f = finalize_run(admin, [used])
    assert f.ok, f.text
    r = admin.post("/api/totes/set-bulk", {"ids": [free, used], "grind": "Fine"})
    assert r.status == 400 and "cannot be changed" in r.json["error"]
    assert sql(srv, "SELECT grind FROM tote_lots WHERE id=?", (free,))[0][0] in ("Coarse", None)


# ---- the Whole grind ---------------------------------------------------------------------------------------------------------------

def tote_row(srv, lot):
    return sql(srv, "SELECT grind, description FROM tote_lots WHERE lot_number=?", (lot,))[0]


def test_a_tote_can_be_checked_in_as_whole(srv, admin):
    r = admin.post("/api/harvest", {"site": "JAM", "species": "SL", "harvestDate": "2026-09-05", "toteCount": 1, "totalKg": 900, "location": "Cold", "grind": "whole"})
    assert r.status == 200, r.text
    assert tote_row(srv, r.json["created"][0]) == ("Whole", "Fresh Stabilized Whole Sugar Kelp")
    assert [t["grind"] for t in admin.get("/api/totes").json["totes"] if t["lot"] == r.json["created"][0]] == ["Whole"]
    bad = admin.post("/api/harvest", {"site": "JAM", "species": "SL", "harvestDate": "2026-09-05", "toteCount": 1, "totalKg": 900, "location": "Cold", "grind": "Chunky"})
    assert bad.status == 400 and "Coarse, Fine or Whole" in bad.json["error"]


def test_whole_is_accepted_by_the_csv_import_and_the_bulk_set(srv, admin):
    r = load(admin, csv(row(date="2026-09-06", grind="Whole"), row(date="2026-09-06", grind="WHOLE"), row(date="2026-09-06")))
    assert r.status == 200, r.text
    lots = r.json["created"]
    assert [tote_row(srv, l)[0] for l in lots] == ["Whole", "Whole", "Coarse"]
    assert tote_row(srv, lots[0])[1].startswith("Fresh Stabilized Whole") and tote_row(srv, lots[2])[1].startswith("Fresh Stabilized Ground")
    where, details = problems(check(admin, csv(row(date="2026-09-06", grind="Chunky"))))
    assert where == [(2, "grind")] and "Whole" in details[0]["message"]
    ids = [sql(srv, "SELECT id FROM tote_lots WHERE lot_number=?", (l,))[0][0] for l in lots]
    assert admin.post("/api/totes/set-bulk", {"ids": [ids[2]], "grind": "Whole"}).json["updated"] == 1
    assert tote_row(srv, lots[2])[0] == "Whole"


def test_only_coarse_totes_can_be_pulled_for_pre_processing(srv, admin):
    whole = admin.post("/api/harvest", {"site": "JAM", "species": "SL", "harvestDate": "2026-09-07", "toteCount": 1, "totalKg": 900, "location": "Cold", "grind": "Whole"}).json["created"][0]
    tid = sql(srv, "SELECT id FROM tote_lots WHERE lot_number=?", (whole,))[0][0]
    bid = admin.post("/api/preproc", {}).json["id"]
    r = admin.post("/api/preproc/%d/inputs" % bid, {"toteIds": [tid]})
    assert r.status == 400 and "only Coarse totes" in r.json["error"]
