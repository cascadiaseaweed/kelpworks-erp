"""The sites a tote can come from: only the six agreed sites are offered (by name), the rest stay for the totes that already carry them."""
import pytest

from factories import harvest_totes, sql
from harness import Server

SIX = {"CDV": "Cordova", "COR": "Cormorant", "DIP": "Diplock Island", "JAM": "James Island", "KOD": "Kodiak", "SGR": "Seagrove"}


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def sites(admin):
    return admin.get("/api/refdata").json["sites"]


def test_only_the_six_sites_are_offered_sorted_by_name(srv, admin):
    active = [s for s in sites(admin) if s["active"] and s["code"] != "MIX"]
    assert {s["code"]: s["name"] for s in active} == SIX
    assert [s["name"] for s in active] == sorted(s["name"] for s in active) == ["Cordova", "Cormorant", "Diplock Island", "James Island", "Kodiak", "Seagrove"]
    assert [s["name"] for s in sites(admin)] == sorted(s["name"] for s in sites(admin))                                  # the whole list comes by name too


def test_the_old_sites_stay_but_are_not_offered(srv, admin):
    old = {s["code"]: s for s in sites(admin) if not s["active"]}
    assert {"AOF", "ASG", "KIS", "NO", "RO", "WB"} <= set(old) and all(not s["active"] for s in old.values())


def test_seagrove_moved_from_asg_to_sgr_without_renaming_its_lot_numbers(srv, admin):
    assert sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE site_code='ASG'")[0][0] == 0
    assert sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE site_code='SGR' AND lot_number LIKE 'ASG-%'")[0][0] > 0


def test_cordova_moved_to_cdv_without_renaming_its_lot_numbers(srv, admin):
    assert sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE site_code='COR'")[0][0] == 0
    n = sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE site_code='CDV' AND lot_number LIKE 'COR-%'")[0][0]
    assert n > 0                                                                                                       # the seeded Cordova totes keep the lot numbers printed on them
    shown = {t["lot"]: t["site"] for t in admin.get("/api/totes").json["totes"] if t["lot"].startswith("COR-")}
    assert shown and set(shown.values()) == {"CDV"}
    assert sql(srv, "SELECT COUNT(*) FROM config_log WHERE area='site'")[0][0] == 1


def test_a_check_in_needs_one_of_the_six_sites(srv, admin):
    ok = admin.post("/api/harvest", {"site": "KOD", "species": "SL", "harvestDate": "2026-09-01", "toteCount": 1, "totalKg": 900, "location": "Cold"})
    assert ok.status == 200 and ok.json["created"][0].startswith("KOD-SL-20260901-")
    for bad in ("AOF", "ZZZ", "MIX"):
        r = admin.post("/api/harvest", {"site": bad, "species": "SL", "harvestDate": "2026-09-01", "toteCount": 1, "totalKg": 900, "location": "Cold"})
        assert r.status == 400 and "Choose one of" in r.json["error"], bad
    assert not sql(srv, "SELECT 1 FROM sites WHERE code='ZZZ'")


def test_the_csv_import_refuses_any_other_site_with_its_row_and_column(srv, admin):
    head = "site,species,harvestDate,avgWeightKg\r\n"
    good = admin.post("/api/harvest/bulk", {"csvText": head + "CDV,SL,2026-09-02,100\r\nCOR,SL,2026-09-02,100\r\n", "dryRun": True})
    assert good.status == 200 and good.json["lots"] == ["CDV-SL-20260902-001", "COR-SL-20260902-001"] and not good.json["warnings"]
    bad = admin.post("/api/harvest/bulk", {"csvText": head + "JAM,SL,2026-09-02,100\r\nAOF,SL,2026-09-02,100\r\nQQQ,SL,2026-09-02,100\r\n", "dryRun": True})
    assert bad.status == 400
    assert [(p["row"], p["column"]) for p in bad.json["problems"]] == [(3, "site"), (4, "site")]
    assert not sql(srv, "SELECT 1 FROM sites WHERE code='QQQ'")


def test_the_list_is_set_once_and_a_later_edit_survives_a_restart(srv, admin):
    sql(srv, "UPDATE sites SET active=0 WHERE code='KOD'")
    srv.restart()
    admin = srv.admin_client()
    assert [s["active"] for s in sites(admin) if s["code"] == "KOD"] == [False]
    assert sql(srv, "SELECT COUNT(*) FROM config_log WHERE area='site'")[0][0] == 1
