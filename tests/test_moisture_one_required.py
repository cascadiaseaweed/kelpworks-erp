"""KW-18: the centrifuge-solids and screw-press-solids % moisture are each optional, but at least one of them is required to finalize."""
import pytest

from factories import feedstock_detail, finalize_run, harvest_totes
from harness import Server

LABEL = "%Moisture of the solids (centrifuge or screw press: at least one)"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def draft(admin):
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    return rid, totes


def missing(admin, rid):
    prog = admin.get("/api/production/%d/progress" % rid).json["progress"]
    return [m for s in prog["sections"] if s["key"] == "separation" for m in s["missing"]]


def finalize(admin, rid, totes):
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    return admin.post("/api/production/drafts/%d/finalize" % rid, body)


def moisture(admin, rid, centrifuge, screw):
    admin.put("/api/production/%d/stages/separation" % rid, {"pctMoisture": centrifuge, "pctMoistureScrew": screw}).raise_for_status()


def test_with_both_blank_the_run_cannot_finalize(admin):
    rid, totes = draft(admin)
    moisture(admin, rid, None, None)
    assert missing(admin, rid) == [LABEL]
    f = finalize(admin, rid, totes)
    assert f.status == 400 and "%Moisture of the solids" in f.text


@pytest.mark.parametrize("centrifuge,screw", [(35.0, None), (None, 42.5), (35.0, 42.5)])
def test_any_one_of_the_two_is_enough(admin, centrifuge, screw):
    rid, totes = draft(admin)
    moisture(admin, rid, centrifuge, screw)
    assert missing(admin, rid) == []
    f = finalize(admin, rid, totes)
    assert f.ok, f.text


def test_neither_field_is_marked_required_on_its_own(admin):
    req = admin.get("/api/refdata").json["requiredFields"]["separation"]
    assert "pctMoisture" not in req and "pctMoistureScrew" not in req and "startedAt" in req
