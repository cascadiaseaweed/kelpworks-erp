"""KW-05 / KW-06: odour is never mandatory. Feedstock odour + intensity are no longer required to finalize a run (earlier readings stay), and the final QC check
(the Packaging QC Check on the pasteurized product) has an optional odour observation that is shown on the production summary."""
import pytest

import kelp_erp_server as K
from factories import feedstock_detail, finalize_run, finished_run, harvest_totes, stage_payloads
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def run_of(admin, rid):
    return [r for r in admin.get("/api/production").json["runs"] if r["id"] == rid][0]


def test_odour_is_not_a_required_feedstock_field():
    keys = [k for k, _ in K.REQUIRED_FEEDSTOCK]
    assert "odour" not in keys and "odourIntensity" not in keys
    assert "ph" in keys and "weightKg" in keys and "surfacePhoto" in keys                  # everything else is still required


def test_a_run_finalizes_with_the_feedstock_odour_fields_blank(srv, admin, monkeypatch):
    totes = harvest_totes(admin, 1)
    detail = feedstock_detail()
    detail.update({"odour": None, "odourIntensity": None})
    monkeypatch.setattr("factories.feedstock_detail", lambda: dict(detail))
    rid, f = finalize_run(admin, totes)
    assert f.ok, f.text
    inp = run_of(admin, rid)["inputs"][0]
    assert not inp["odour"] and not inp["odourIntensity"]
    feed = [s for s in run_of(admin, rid)["progress"]["sections"] if s["key"] == "feedstock"][0]
    assert feed["done"] and not feed["missing"]


def test_an_odour_that_was_recorded_is_kept(srv, admin):
    rid = finished_run(admin)                                                              # the helper still records "Mild" / "Mild"
    inp = run_of(admin, rid)["inputs"][0]
    assert (inp["odour"], inp["odourIntensity"]) == ("Mild", "Mild")


def test_the_final_qc_odour_is_optional_and_part_of_the_log_only_when_written(srv, admin):
    plain = finished_run(admin)
    assert "odour" not in run_of(admin, plain)["stages"]["packaging"]                     # nothing added to a log without it: earlier signed snapshots stay valid
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    r = admin.put("/api/production/%d/stages/packaging" % rid, {"odour": "Marine", "odourIntensity": "Mild"})
    assert r.status == 200, r.text
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).ok
    pk = run_of(admin, rid)["stages"]["packaging"]
    assert (pk["odour"], pk["odourIntensity"]) == ("Marine", "Mild")


def test_the_final_qc_odour_can_be_added_under_an_amendment_and_is_a_logged_change(srv, admin):
    rid = finished_run(admin)
    am, info = srv.make_user(role="user", canAmendLog=True)
    opened = am.post("/api/production/%d/amendments" % rid, {"category": "late_entry", "reason": "noted an odour at the final check", "password": info["password"]})
    assert opened.status == 200, opened.text
    assert am.put("/api/production/%d/stages/packaging" % rid, {"odour": "Sweet (Apple Juice)"}).status == 200
    pv = am.get("/api/production/%d/amendments/%d/preview" % (rid, opened.json["amendment"]["id"])).json
    assert any("odour" in c["field"].lower() for c in pv["changes"]), pv["changes"]


def test_the_production_summary_shows_the_final_odour(srv, admin):
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    admin.put("/api/production/%d/stages/packaging" % rid, {"odour": "Marine", "odourIntensity": "Mild"}).raise_for_status()
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).ok
    pdf = admin.get("/api/production/%d/summary.pdf" % rid)
    assert pdf.status == 200
    text = " ".join(" ".join(str(x) for x in (l if isinstance(l, (list, tuple)) else [l])) for l in K.pdf_text_lines(pdf.body))
    assert "final odour Marine, mild" in text, text[:2000]
