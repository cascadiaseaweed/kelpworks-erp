"""KW-59: ORP (mV) is a required field in the Liquid section of the Homogenization QC Check and the Packaging QC Check. It is saved and shown like the other QC readings, it is
required for every run still being logged and every run finalized from now on, and a run finalized before it existed is not asked for it (and its signed log has no such key).
KW-17 / KW-25 are screen changes, pinned at the bottom."""
import json
import pathlib

import pytest

from factories import feedstock_detail, finalize_run, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def draft(admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    return rid


def missing(admin, rid):
    prog = admin.get("/api/production/%d/progress" % rid).json["progress"]
    return [m for s in prog["sections"] for m in s["missing"]]


def test_orp_is_saved_with_the_qc_checks_and_may_be_negative(admin):
    rid = draft(admin)
    assert admin.put("/api/production/%d/stages/homogenization" % rid, {"orpMv": -35}).status == 200
    assert admin.put("/api/production/%d/stages/packaging" % rid, {"orpMv": 120.5}).status == 200
    st = [x for x in admin.get("/api/production/drafts").json["drafts"] if x["id"] == rid][0]["stages"]
    assert st["homogenization"]["orpMv"] == -35 and st["packaging"]["orpMv"] == 120.5
    assert st["extraction"].get("orpMv") is None and "orpMv" not in st["separation"]                     # only these two QC checks have it


def test_a_run_in_progress_is_not_complete_without_orp_and_cannot_be_finalized(srv, admin):
    rid = draft(admin)
    admin.put("/api/production/%d/stages/homogenization" % rid, {"orpMv": None}).raise_for_status()
    admin.put("/api/production/%d/stages/packaging" % rid, {"orpMv": None}).raise_for_status()
    m = missing(admin, rid)
    assert "QC Check: ORP (mV)" in m and "LKE QC Check: ORP (mV)" in m
    tote_ids = json_ids(srv, rid)
    body = {"sku": "KELPIVEX", "toteIds": tote_ids, "feedstockDetails": {str(t): feedstock_detail() for t in tote_ids}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert r.status == 400 and "ORP" in r.json["error"], r.text
    admin.put("/api/production/%d/stages/homogenization" % rid, {"orpMv": 40}).raise_for_status()
    admin.put("/api/production/%d/stages/packaging" % rid, {"orpMv": 55}).raise_for_status()
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).status == 200
    assert sql(srv, "SELECT orp_required FROM production_runs WHERE id=?", (rid,)) == [(1,)]               # a run finalized now is held to the rule from then on


def json_ids(srv, rid):
    return json.loads(sql(srv, "SELECT draft_data FROM production_runs WHERE id=?", (rid,))[0][0])["toteIds"]


def test_a_run_finalized_before_orp_existed_is_not_asked_for_it_and_its_log_has_no_such_key(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1))
    assert f.status == 200
    # as if it had been finalized before this change: no ORP readings, no rule
    sql(srv, "UPDATE production_runs SET homog_orp_mv=NULL, packaging_orp_mv=NULL, orp_required=0 WHERE id=?", (rid,))
    assert not [m for m in missing(admin, rid) if "QC Check: ORP" in m]
    run = admin.get("/api/production").json["runs"]
    run = [x for x in run if x["id"] == rid][0]
    assert "orpMv" not in run["stages"]["homogenization"] and "orpMv" not in run["stages"]["packaging"]      # nothing added to the signed snapshot of an older run


def test_the_signed_qc_summary_of_an_older_run_does_not_count_the_orp_fields(srv, admin):
    import kelp_erp_server as K
    rid, f = finalize_run(admin, harvest_totes(admin, 1))
    assert f.status == 200
    now = [x for x in admin.get("/api/production").json["runs"] if x["id"] == rid][0]["qcSummary"]
    assert now["total"] == len(K.QC_FIELD_REGISTRY)                                         # finalized under the ORP rule: counted
    sql(srv, "UPDATE production_runs SET orp_required=0 WHERE id=?", (rid,))                 # as if finalized before it existed
    old = [x for x in admin.get("/api/production").json["runs"] if x["id"] == rid][0]["qcSummary"]
    assert old["total"] == len(K.QC_FIELD_REGISTRY) - 2                                     # exactly what its signed snapshot said then


def test_the_summary_pdf_and_the_qc_charts_know_orp(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1))
    assert f.status == 200
    r = admin.get("/api/production/%d/summary.pdf" % rid)
    assert r.status == 200
    charts = admin.get("/api/qc-charts").json
    orp = [m for m in charts["measures"] if m["key"] == "qc:orp"]
    assert orp and orp[0]["unit"] == "mV" and orp[0]["nonNegative"] is False
    assert {s["key"] for s in orp[0]["sections"]} >= {"homogenization", "packaging"}


def test_the_screens(srv):
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")
    flat = " ".join(js.split())
    # KW-17: the separation sample point comes after Liquid Out
    assert "liquidQc.box, solidsSamplePointBox," in flat and flat.index("recoveredLiquidBox,") < flat.index("liquidQc.box, solidsSamplePointBox,")
    # KW-25: the summary card is configured per KPI
    assert "run_card_show_" in js and "rs-headline" in js
    import kelp_erp_server as K
    keys = {k for k, *_ in K.SETTINGS_DEFAULTS}
    for k in ("volume_out", "final_ph", "final_tds", "final_orp", "extraction_eff", "run_date", "operators", "product", "farms", "ibcs", "feedstock_weight", "packaged", "conv_process", "conv_harvest"):
        assert "run_card_show_" + k in keys
