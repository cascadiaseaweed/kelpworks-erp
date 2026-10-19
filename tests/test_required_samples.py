"""KW-13: the default LKE characterization samples are required. They can't be removed (UI or API), their type / description can't be changed and their
quantity can't go below the default; optional samples can still be added, changed and removed."""
import pytest

import kelp_erp_server as K
from factories import finished_run, harvest_totes, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def new_draft(admin):
    totes = harvest_totes(admin, 1)
    r = admin.post("/api/production/drafts", {"sku": "KELPIVEX", "toteIds": totes, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"})
    r.raise_for_status()
    return r.json["run"]["id"]


def points(admin, rid):
    return admin.get("/api/production/%d/sample-points" % rid).json["samplePoints"]


def url(rid, sp):
    return "/api/production/%d/sample-points/%d" % (rid, sp)


def test_the_default_samples_start_as_required_with_their_default_quantity(srv, admin):
    rid = new_draft(admin)
    required = [p for p in points(admin, rid) if p.get("required")]
    assert [(p["type"], p["description"], p["qty"], p["minQty"]) for p in required] == [(t, d, q, q) for t, d, q, _c in K.Handler.DEFAULT_LKE_SAMPLES]


def test_a_required_sample_cannot_be_removed_and_nothing_is_refunded(srv, admin):
    rid = new_draft(admin)
    sp = [p for p in points(admin, rid) if p["description"] == "Retention"][0]
    before = sql(srv, "SELECT on_hand FROM consumables WHERE name='50 mL falcon tube'")
    r = admin.delete(url(rid, sp["id"]))
    assert r.status == 409 and "required" in r.text
    assert sp["id"] in [p["id"] for p in points(admin, rid)]
    assert sql(srv, "SELECT on_hand FROM consumables WHERE name='50 mL falcon tube'") == before


def test_a_required_sample_keeps_its_type_and_description_and_a_minimum_quantity(srv, admin):
    rid = new_draft(admin)
    sp = [p for p in points(admin, rid) if p["description"] == "Retention"][0]                  # 4 x 50 mL falcon tube
    assert admin.put(url(rid, sp["id"]), {"qty": 3}).status == 400
    assert admin.put(url(rid, sp["id"]), {"description": "Other"}).status == 400
    assert admin.put(url(rid, sp["id"]), {"type": "Solid"}).status == 400
    assert [p["qty"] for p in points(admin, rid) if p["id"] == sp["id"]] == [4]
    assert admin.put(url(rid, sp["id"]), {"qty": 6}).status == 200                              # more is fine
    assert admin.put(url(rid, sp["id"]), {"qty": 4, "description": "Retention"}).status == 200  # back to the minimum; the same description is not a change
    assert admin.put(url(rid, sp["id"]), {"qty": 3}).status == 400


def test_optional_samples_can_still_be_added_changed_and_removed(srv, admin):
    rid = new_draft(admin)
    added = admin.post("/api/production/%d/sample-points" % rid, {"stage": "packaging"}).json["samplePoints"][-1]
    assert not added.get("required")
    assert admin.put(url(rid, added["id"]), {"description": "R&D", "qty": 1}).status == 200
    assert admin.delete(url(rid, added["id"])).status == 200
    assert len([p for p in points(admin, rid) if p.get("required")]) == len(K.Handler.DEFAULT_LKE_SAMPLES)


def test_a_finalized_run_keeps_its_required_samples_even_under_an_amendment(srv, admin):
    rid = finished_run(admin)
    am, info = srv.make_user(role="user", canAmendLog=True)
    assert am.post("/api/production/%d/amendments" % rid, {"category": "late_entry", "reason": "check the samples", "password": info["password"]}).status == 200
    sp = [p for p in points(am, rid) if p.get("required") and p["description"] == "Retention"][0]
    assert am.delete(url(rid, sp["id"])).status == 409
    assert am.put(url(rid, sp["id"]), {"qty": sp["minQty"] - 1}).status == 400 and [p["qty"] for p in points(am, rid) if p["id"] == sp["id"]][0] == sp["qty"]


def test_runs_without_the_lock_show_no_extra_keys(srv, admin):
    """A row that is not required carries no `required` / `minQty` key, so the signed log of every earlier run is unchanged."""
    rid = new_draft(admin)
    sql(srv, "UPDATE run_sample_points SET min_qty=NULL WHERE run_id=?", (rid,))
    assert all("required" not in p and "minQty" not in p for p in points(admin, rid))
