"""KW-10 / KW-11: a run names its homogenization tank(s) (2A, 2B or both); each tank has its own level, solids-loading Process Check and dilution entries, the
recommended dilution water is calculated per tank, and the run-level homogenization values are the totals of the tanks (litres summed, %solids volume-weighted)."""
import pytest

from factories import finalize_run, harvest_totes
from harness import Server


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


def stage(admin, rid, **body):
    r = admin.put("/api/production/%d/stages/homogenization" % rid, body)
    assert r.status == 200, r.text
    return r


def hom(admin, rid):
    return [d for d in admin.get("/api/production/drafts").json["drafts"] if d["id"] == rid][0]["stages"]["homogenization"]


def missing(admin, rid):
    prog = admin.get("/api/production/%d/progress" % rid).json["progress"]
    return [m for s in prog["sections"] if s["key"] == "homogenization" for m in s["missing"]]


A = dict(slurryL2a=2000, wetSolidsWtG2a=600, liquidWtG2a=400, dilutionWaterL2a=400, postDilutionTankL2a=2400)   # 60 % solids, target 50 %
B = dict(slurryL2b=1000, wetSolidsWtG2b=300, liquidWtG2b=700, dilutionWaterL2b=0, postDilutionTankL2b=1000)      # 30 % solids, already below the target


def test_one_tank_calculates_its_own_dilution_and_the_run_level_values_follow(admin):
    rid = draft(admin)
    stage(admin, rid, tanks="2A", targetPctWetSolids=50, **A)
    h = hom(admin, rid)
    assert h["tanks"] == "2A" and abs(h["pctWetSolids2a"] - 0.6) < 1e-9
    assert abs(h["dilutionWaterTargetL2a"] - 400) < 1e-6                                   # 2000 x 0.6 / 0.5 - 2000
    assert abs(h["lotPctSolids2a"] - 0.5) < 1e-9                                           # 0.6 x (2400 - 400) / 2400
    assert (h["slurryL"], h["dilutionWaterL"], h["postDilutionTankL"]) == (2000, 400, 2400)  # run level = the one tank
    assert (h["wetSolidsWtG"], h["liquidWtG"]) == (600, 400)
    assert abs(h["pctWetSolids"] - 0.6) < 1e-9 and abs(h["lotPctSolids"] - 0.5) < 1e-9
    assert not any(k.endswith("2b") for k in h)                                            # nothing for the tank that is not used


def test_both_tanks_are_calculated_separately_and_totalled(admin):
    rid = draft(admin)
    stage(admin, rid, tanks="2AB", targetPctWetSolids=50, **A, **B)
    h = hom(admin, rid)
    assert abs(h["dilutionWaterTargetL2a"] - 400) < 1e-6 and h["dilutionWaterTargetL2b"] == 0   # tank 2B is already at or below the target: no water
    assert abs(h["pctWetSolids2b"] - 0.3) < 1e-9
    assert (h["slurryL"], h["dilutionWaterL"], h["postDilutionTankL"]) == (3000, 400, 3400)
    assert abs(h["dilutionWaterTargetL"] - 400) < 1e-6
    assert abs(h["pctWetSolids"] - (0.6 * 2000 + 0.3 * 1000) / 3000) < 1e-9                  # volume-weighted
    assert abs(h["lotPctSolids"] - (0.5 * 2400 + 0.3 * 1000) / 3400) < 1e-9
    assert "wetSolidsWtG" not in h or h["wetSolidsWtG"] is None                             # sample weights of two tanks are not additive


def test_a_total_exists_only_when_every_tank_in_use_has_its_value(admin):
    rid = draft(admin)
    stage(admin, rid, tanks="2AB", targetPctWetSolids=50, **A, **dict(B, postDilutionTankL2b=None))
    h = hom(admin, rid)
    assert h["postDilutionTankL"] is None and h["lotPctSolids"] is None                     # tank 2B's final level is missing
    assert h["slurryL"] == 3000


def test_switching_to_one_tank_clears_the_other_and_recalculates(admin):
    rid = draft(admin)
    stage(admin, rid, tanks="2AB", targetPctWetSolids=50, **A, **B)
    stage(admin, rid, tanks="2B")
    h = hom(admin, rid)
    assert not any(k.endswith("2a") for k in h) and h["slurryL"] == 1000 and abs(h["pctWetSolids"] - 0.3) < 1e-9


def test_only_the_tanks_in_use_are_required(admin):
    rid = draft(admin)
    stage(admin, rid, tanks="2A", targetPctWetSolids=50, **A)
    assert missing(admin, rid) == []
    stage(admin, rid, tanks="2AB")
    assert sorted(missing(admin, rid)) == sorted(["Pre-Dilution Tank Level, Tank 2B (L)", "Wet-solids-wt, Tank 2B (g)", "Liquid-wt, Tank 2B (g)",
                                                  "Dilution water added, Tank 2B (L)", "Post-Dilution Tank Level, Tank 2B (L)"])
    stage(admin, rid, **B)
    assert missing(admin, rid) == []


def test_the_tank_choice_is_required_and_must_be_valid(admin):
    rid = draft(admin)
    assert admin.put("/api/production/%d/stages/homogenization" % rid, {"tanks": "3A"}).status == 400
    stage(admin, rid, tanks="")
    assert "Homogenization tank(s)" in missing(admin, rid)


def test_a_run_with_one_tank_still_finalizes_and_a_two_tank_run_too(srv, admin):
    for tanks, extra in (("2A", {}), ("2AB", B)):
        totes = harvest_totes(admin, 1)
        rid, f = finalize_run(admin, totes, finalize=False)
        stage(admin, rid, tanks=tanks, targetPctWetSolids=50, **A, **extra)
        body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): __import__("factories").feedstock_detail() for t in totes},
                "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
        r = admin.post("/api/production/drafts/%d/finalize" % rid, body)
        assert r.ok, (tanks, r.text)


def test_the_capacity_setting_exists_for_the_screen(admin):
    assert admin.get("/api/refdata").json["settings"]["homog_tank_capacity_each_l"]["value"] == 5000
