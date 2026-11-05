"""KW-96: laboratory results with no Certificate of Analysis specification ("additional analyses") appear on the Quality Control charts, for trend monitoring only.
Read-only over existing lab results: nothing about the certificate, the release gate or how results are entered changes."""
import os
import re

import pytest

import kelp_erp_server as K
from factories import finished_run, lab_report, sql
from harness import Server

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def qm(srv):
    client, _ = srv.make_user(role="user", isQualityManager=True)
    return client


@pytest.fixture(scope="module")
def plain(srv):
    client, _ = srv.make_user(role="user")
    return client


def add_results(qm, rid, rows, report="R-1"):
    r = qm.post("/api/production/%d/lab-results" % rid, {"labName": "SGS", "reportNumber": report, "results": rows})
    assert r.status == 200, r.text
    return r


def measures(admin):
    return {m["key"]: m for m in admin.get("/api/qc-charts").json["measures"]}


def points(m):
    return [p for s in m["sections"] for p in s["points"]]


# ---- the pure naming rule -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Iron", "iron", "Fe", "FE", "Iron (Fe)", "iron(fe)", "Iron (Fe) - ICP-MS after microwave digestion", "Fe - total", "Iron, total"])
def test_every_spelling_of_an_element_is_one_chart(name):
    assert K.qc_extra_analyte(name) == ("fe", "Iron (Fe)", True)


@pytest.mark.parametrize("name", ["Iron oxide", "Total iron", "Iron (Mn)", "Ferric iron", "K2O"])
def test_a_different_measurement_is_not_merged_into_an_element(name):
    slug, label, is_element = K.qc_extra_analyte(name)
    assert not is_element and slug != "fe" and label == name


def test_aluminium_and_aluminum_are_one_element():
    assert K.qc_extra_analyte("Aluminium")[0] == K.qc_extra_analyte("Aluminum (Al)")[0] == "al"


def test_other_names_get_a_key_of_their_letters_and_digits():
    assert K.qc_extra_analyte("Total Kjeldahl Nitrogen (as N)") == ("total_kjeldahl_nitrogen_as_n", "Total Kjeldahl Nitrogen (as N)", False)
    assert K.qc_extra_analyte("   ")[0] == "analyte"


def test_a_long_name_gives_a_short_unique_key_the_limits_route_accepts():
    a = K.qc_extra_analyte("Polycyclic aromatic compounds measured after solvent extraction method A")[0]
    b = K.qc_extra_analyte("Polycyclic aromatic compounds measured after solvent extraction method B")[0]
    assert a != b and len(a) <= 48 and len(b) <= 48
    assert re.fullmatch(r"[a-z]+:[a-z_0-9]+", "labx:" + a)                       # what PUT /api/qc-charts/limits requires of a metric


def test_no_element_here_is_one_of_the_certificate_metals():
    assert not set(K.QC_ELEMENTS) & {"as", "cd", "cr", "co", "cu", "pb", "hg", "mo", "ni", "se", "zn"}      # those have specifications and their own charts


# ---- the chart data ----------------------------------------------------------------------------------------------------------------

def test_an_additional_analysis_on_a_completed_run_is_charted(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Manganese (Mn)", "value": "4.2", "unit": "ppm", "method": "ICP"}])
    m = measures(admin)["labx:mn"]
    assert (m["group"], m["label"], m["unit"], m["nonNegative"]) == ("labx", "Manganese (Mn)", "ppm", True)
    assert [s["key"] for s in m["sections"]] == ["lab"]
    pts = [p for p in points(m) if p["r"] == rid]
    assert len(pts) == 1 and pts[0]["v"] == 4.2 and pts[0]["q"] == "" and pts[0]["n"] == "R-1"


def test_a_run_that_is_not_completed_is_not_charted(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Boron", "value": "7", "unit": "ppm"}])
    assert "labx:b" in measures(admin)
    sql(srv, "UPDATE production_runs SET status='draft' WHERE id=?", (rid,))
    try:
        assert not [p for p in points(measures(admin).get("labx:b", {"sections": []})) if p["r"] == rid]
    finally:
        sql(srv, "UPDATE production_runs SET status='completed' WHERE id=?", (rid,))


def test_the_spellings_and_units_of_one_element_make_one_series_in_ppm(srv, admin, qm):
    r1, r2, r3 = finished_run(admin), finished_run(admin), finished_run(admin)
    add_results(qm, r1, [{"analyte": "Iron", "value": "50", "unit": "ppm"}])
    add_results(qm, r2, [{"analyte": "Fe", "value": "0.01", "unit": "%"}])                                     # 0.01 % = 100 ppm
    add_results(qm, r3, [{"analyte": "Iron (Fe) - ICP-MS after microwave digestion", "value": "70000", "unit": "ppb"}])   # 70 ppm
    m = measures(admin)["labx:fe"]
    assert m["label"] == "Iron (Fe)" and m["unit"] == "ppm"
    by_run = {p["r"]: p["v"] for p in points(m)}
    assert by_run[r1] == 50 and round(by_run[r2], 6) == 100 and round(by_run[r3], 6) == 70
    assert {n["name"] for n in m["names"]} == {"Iron", "Fe", "Iron (Fe) - ICP-MS after microwave digestion"}
    assert m["skipped"] == {"count": 0, "units": []}


def test_iron_oxide_is_its_own_chart(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Iron oxide", "value": "3", "unit": "ppm"}])
    ms = measures(admin)
    assert "labx:iron_oxide" in ms and ms["labx:iron_oxide"]["label"] == "Iron oxide"
    assert rid not in {p["r"] for p in points(ms["labx:fe"])}


def test_a_below_detection_result_is_kept_and_flagged(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Barium", "value": "<0.5", "unit": "ppm"}])
    p = [p for p in points(measures(admin)["labx:ba"]) if p["r"] == rid][0]
    assert p["q"] == "<" and p["v"] == 0.5


def test_text_results_and_an_unknown_unit_are_not_plotted(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Calcium", "value": "Negative", "unit": ""}, {"analyte": "Strontium", "value": "9", "unit": "furlongs"}])
    ms = measures(admin)
    assert "labx:ca" not in ms or rid not in {p["r"] for p in points(ms["labx:ca"])}                   # a Negative result has no number
    assert "labx:sr" not in ms or rid not in {p["r"] for p in points(ms["labx:sr"])}                   # an element in a unit that cannot be converted
    rid2 = finished_run(admin)
    add_results(qm, rid2, [{"analyte": "Strontium", "value": "9", "unit": "ppm"}])
    sr = measures(admin)["labx:sr"]
    assert sr["skipped"] == {"count": 1, "units": ["furlongs"]} and [p["r"] for p in points(sr)] == [rid2]


def test_another_analyte_keeps_its_own_unit_and_the_first_unit_wins(srv, admin, qm):
    r1, r2, r3 = finished_run(admin), finished_run(admin), finished_run(admin)
    add_results(qm, r1, [{"analyte": "Total Kjeldahl Nitrogen", "value": "1.2", "unit": "%"}])
    add_results(qm, r2, [{"analyte": "total kjeldahl nitrogen", "value": "1.4", "unit": "%"}])
    add_results(qm, r3, [{"analyte": "Total Kjeldahl Nitrogen", "value": "12000", "unit": "mg/kg"}])    # a different unit: left out, never converted
    m = measures(admin)["labx:total_kjeldahl_nitrogen"]
    assert m["unit"] == "%" and m["label"] == "Total Kjeldahl Nitrogen"                                  # the spelling used most often names the chart
    assert sorted(p["v"] for p in points(m)) == [1.2, 1.4]
    assert m["skipped"] == {"count": 1, "units": ["mg/kg"]}


def test_a_voided_result_is_left_out(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Titanium", "value": "2", "unit": "ppm"}])
    x = [x for x in qm.get("/api/production/%d/lab-results" % rid).json["results"] if x["analyte"] == "Titanium"][0]
    assert qm.post("/api/production/%d/lab-results/%d/void" % (rid, x["id"]), {"reason": "wrong sample"}).status == 200
    assert "labx:ti" not in measures(admin)


def test_a_run_excluded_from_the_statistics_is_flagged_as_for_every_other_lab_chart(srv, admin, qm):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Lithium", "value": "1", "unit": "ppm"}])
    sql(srv, "UPDATE production_runs SET exclude_from_stats=1, exclude_reason='test' WHERE id=?", (rid,))
    runs = {r["id"]: r for r in admin.get("/api/qc-charts").json["runs"]}
    assert runs[rid]["excluded"] is True and rid in {p["r"] for p in points(measures(admin)["labx:li"])}   # the page hides excluded runs unless asked, like every measurement


def test_a_test_with_a_specification_is_still_only_in_its_own_chart(srv, admin, qm):
    rid = finished_run(admin)
    att = lab_report(qm, rid)                                                                                          # a result that decides release needs its report
    r = qm.post("/api/production/%d/lab-results" % rid, {"labName": "SGS", "reportNumber": "R-2", "attachmentId": att,
                "results": [{"specCode": "pb", "value": "1", "unit": "ppm"}, {"analyte": "Vanadium", "value": "3", "unit": "ppm"}]})
    assert r.status == 200, r.text
    ms = measures(admin)
    assert "lab:pb" in ms and not [k for k in ms if k.startswith("labx:") and "lead" in k]
    assert rid in {p["r"] for p in points(ms["lab:pb"])} and rid in {p["r"] for p in points(ms["labx:v"])}
    assert "labx:pb" not in ms


def test_there_is_no_specification_line_for_these_charts(srv, admin, qm):
    refs = admin.get("/api/qc-charts").json["refs"]
    assert not [k for k in refs if k.startswith("labx:")]


# ---- control limits use the existing route ------------------------------------------------------------------------------------------------

def test_a_quality_manager_can_set_and_clear_limits_for_an_additional_analysis(srv, admin, qm, plain):
    rid = finished_run(admin)
    add_results(qm, rid, [{"analyte": "Fe", "value": "50", "unit": "ppm"}])
    body = {"metric": "labx:fe", "section": "lab", "lcl": 10, "ucl": 200, "center": 80, "note": "trend limits"}
    assert plain.put("/api/qc-charts/limits", body).status == 403
    assert qm.put("/api/qc-charts/limits", body).status == 200
    d = admin.get("/api/qc-charts").json
    assert d["limits"]["labx:fe|lab"]["ucl"] == 200 and d["limits"]["labx:fe|lab"]["setBy"]
    assert sql(srv, "SELECT COUNT(*) FROM qc_chart_limit_log WHERE metric='labx:fe'")[0][0] >= 1                       # logged
    assert qm.put("/api/qc-charts/limits", {"metric": "labx:fe", "section": "lab"}).status == 200                     # cleared
    assert "labx:fe|lab" not in admin.get("/api/qc-charts").json["limits"]


# ---- nothing else moves ------------------------------------------------------------------------------------------------------------------

def test_the_certificate_and_the_release_gate_do_not_see_the_extra_analysis(srv, admin, qm):
    rid = finished_run(admin)
    before = qm.get("/api/production/%d/lab-results" % rid).json["coa"]["summary"]
    add_results(qm, rid, [{"analyte": "Sodium", "value": "300", "unit": "ppm"}])
    after = qm.get("/api/production/%d/lab-results" % rid).json["coa"]["summary"]
    assert {k: v for k, v in after.items() if k != "additionalCount"} == {k: v for k, v in before.items() if k != "additionalCount"}
    assert after["additionalCount"] == before["additionalCount"] + 1                                                    # it is an additional analysis, exactly as before


def test_the_page_lists_the_new_group():
    app = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    assert "lab: 'Laboratory results (CoA specification)'" in app                                               # the tests that have a specification are named as such, beside the group without one
    assert "labx: 'Laboratory - additional analyses (no CoA specification)'" in app
    assert "['qc', 'feedstock', 'lab', 'labx']" in app and "function qcExtraNote(m)" in app and "qcExtraNote(m)," in app
