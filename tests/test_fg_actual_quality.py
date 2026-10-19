"""KW-31: a finished-goods lot shows the run's ACTUAL final TDS and pH (the Packaging QC Check), not the product's target; they are read-only and follow an amendment."""
import pytest

from factories import feedstock_detail, finalize_run, harvest_totes, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def finalized(admin, tds=1.9, ph=3.72):
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    admin.put("/api/production/%d/stages/packaging" % rid, {"tdsPct": tds, "qcPh": ph}).raise_for_status()
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    f = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert f.ok, f.text
    return rid


def lots(admin, rid):
    return [l for l in admin.get("/api/fg").json["fg"] if l["runId"] == rid]


def test_the_lot_shows_the_actual_final_tds_and_ph_not_the_target(admin):
    rid = finalized(admin)
    l = lots(admin, rid)
    assert l and all(x["tds"] == 1.9 and x["ph"] == 3.72 for x in l)                        # the KELPIVEX target is 1.8


def test_the_values_follow_the_packaging_qc_check_under_an_amendment(srv, admin):
    rid = finalized(admin)
    am, info = srv.make_user(role="user", canAmendLog=True)
    opened = am.post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "final TDS was mistyped", "password": info["password"]})
    assert opened.status == 200, opened.text
    assert am.put("/api/production/%d/stages/packaging" % rid, {"tdsPct": 1.95, "qcPh": 3.8}).status == 200
    l = lots(admin, rid)
    assert all(x["tds"] == 1.95 and x["ph"] == 3.8 for x in l)


def test_tds_and_ph_cannot_be_typed_on_the_lot_by_anyone(srv, admin):
    rid = finalized(admin)
    pm, _ = srv.make_user(role="user", isProductionManager=True)
    lot = lots(admin, rid)[0]
    for body in ({"tds": 2.5, "reason": "correction"}, {"ph": 4.0, "reason": "correction"}):
        r = pm.put("/api/fg/%d" % lot["id"], body)
        assert r.status == 400 and "amend the run" in r.text
    assert pm.put("/api/fg/%d" % lot["id"], {"tds": lot["tds"], "ph": lot["ph"], "location": "Cold"}).status == 200      # sending the current values is not a change
    assert [x["tds"] for x in lots(admin, rid)] == [1.9] * len(lots(admin, rid))
