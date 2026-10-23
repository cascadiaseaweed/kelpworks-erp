"""KW-18 -> KW-63: the centrifuge-solids and screw-press-solids % moisture. KW-18 made at least one of them enough (a unit may be broken or not used). KW-63 asks for BOTH on every run
finalized from now on, unless a unit was marked "not used this run" with a reason (never both units), and for the weight of the recovered liquid. A run finalized before it keeps the
KW-18 rule (production_runs.separation_rules_v2 = 0), so its progress, its amendments and its signed log are unchanged."""
import pathlib

import pytest

from factories import feedstock_detail, finalize_run, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"
LEGACY = "%Moisture of the solids (centrifuge or screw press: at least one)"
CENT = "%Moisture of the solids, centrifuge"
SCREW = "%Moisture of the solids, screw press"
WEIGHT = "Total weight of recovered liquid (kg)"


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


def sep(admin, rid, **payload):
    return admin.put("/api/production/%d/stages/separation" % rid, payload)


def stage(admin, rid):
    return admin.get("/api/production/drafts").json["drafts"] and next(d for d in admin.get("/api/production/drafts").json["drafts"] if d["id"] == rid)["stages"]["separation"]


# ---- both readings are required for a new run -----------------------------------------------------------------------------------------------------------------------------------------
def test_a_new_run_needs_both_moisture_readings(admin):
    rid, totes = draft(admin)
    sep(admin, rid, pctMoisture=None, pctMoistureScrew=42.5).raise_for_status()
    assert missing(admin, rid) == [CENT]
    f = finalize(admin, rid, totes)
    assert f.status == 400 and CENT in f.text
    sep(admin, rid, pctMoisture=None, pctMoistureScrew=None).raise_for_status()
    assert missing(admin, rid) == [CENT, SCREW]
    sep(admin, rid, pctMoisture=35.0, pctMoistureScrew=42.5).raise_for_status()
    assert missing(admin, rid) == []
    f = finalize(admin, rid, totes)
    assert f.ok, f.text


def test_the_two_readings_and_the_liquid_weight_are_marked_required(admin):
    req = admin.get("/api/refdata").json["requiredFields"]["separation"]
    assert {"pctMoisture", "pctMoistureScrew", "liquidWeightKg", "startedAt"} <= set(req)


def test_the_weight_of_the_recovered_liquid_is_required_for_a_new_run(admin):
    rid, totes = draft(admin)
    sep(admin, rid, liquidWeightKg=None).raise_for_status()
    assert missing(admin, rid) == [WEIGHT]
    f = finalize(admin, rid, totes)
    assert f.status == 400 and WEIGHT in f.text
    sep(admin, rid, liquidWeightKg=120).raise_for_status()
    assert missing(admin, rid) == [] and finalize(admin, rid, totes).ok


# ---- a unit that was not used -----------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("unused,other,label", [("centrifugeUnused", "pctMoistureScrew", CENT), ("screwUnused", "pctMoisture", SCREW)])
def test_a_unit_not_used_needs_a_reason_and_then_its_reading_is_not_asked_for(admin, unused, other, label):
    reason_key = unused + "Reason"
    rid, totes = draft(admin)
    assert sep(admin, rid, **{unused: True, reason_key: "   "}).status == 400                      # a blank reason is refused
    assert sep(admin, rid, **{unused: True, reason_key: "Screw broke"}).ok
    s = stage(admin, rid)
    assert s[unused] is True and s[reason_key] == "Screw broke" and s["pctMoisture" if unused == "centrifugeUnused" else "pctMoistureScrew"] is None     # its reading was cleared
    assert missing(admin, rid) == [] and finalize(admin, rid, totes).ok


def test_a_unit_marked_not_used_without_a_reason_is_flagged_as_missing(admin):
    rid, _totes = draft(admin)
    assert sep(admin, rid, centrifugeUnused=True).ok                                                # (the reason key was not sent)
    assert missing(admin, rid) == ["Reason the centrifuge was not used"]


def test_a_reading_typed_for_a_unit_that_was_not_used_is_cleared_and_marking_it_used_again_drops_the_reason(admin):
    rid, _totes = draft(admin)
    assert sep(admin, rid, centrifugeUnused=True, centrifugeUnusedReason="Bypassed", pctMoisture=33.0).ok
    assert stage(admin, rid)["pctMoisture"] is None
    assert sep(admin, rid, centrifugeUnused=False, centrifugeUnusedReason="Bypassed", pctMoisture=36.5).ok
    s = stage(admin, rid)
    assert "centrifugeUnused" not in s and "centrifugeUnusedReason" not in s and s["pctMoisture"] == 36.5


def test_the_centrifuge_and_the_screw_press_cannot_both_be_not_used(admin):
    rid, _totes = draft(admin)
    assert sep(admin, rid, centrifugeUnused=True, centrifugeUnusedReason="a").ok
    r = sep(admin, rid, screwUnused=True, screwUnusedReason="b")
    assert r.status == 400 and "cannot both be marked not used" in r.text
    r = sep(admin, rid, centrifugeUnused=True, centrifugeUnusedReason="a", screwUnused=True, screwUnusedReason="b")
    assert r.status == 400


# ---- a run finalized before this keeps the KW-18 rule ----------------------------------------------------------------------------------------------------------------------------------
def test_an_older_finalized_run_keeps_the_either_one_rule_and_has_none_of_the_new_keys(srv, admin):
    rid, totes = draft(admin)
    assert finalize(admin, rid, totes).ok
    assert sql(srv, "SELECT separation_rules_v2 FROM production_runs WHERE id=?", (rid,)) == [(1,)]                  # stamped at finalize
    sql(srv, "UPDATE production_runs SET separation_rules_v2=0, separation_pct_moisture=NULL, separation_liquid_weight_kg=NULL, separation_liquid_volume_l=NULL WHERE id=?", (rid,))   # as if finalized before KW-63
    assert missing(admin, rid) == []                                                                # one reading (the screw press) is enough, no weight is asked for
    sql(srv, "UPDATE production_runs SET separation_pct_moisture_screw=NULL WHERE id=?", (rid,))
    assert missing(admin, rid) == [LEGACY]
    s = next(r for r in admin.get("/api/production").json["runs"] if r["id"] == rid)["stages"]["separation"]
    assert not {"centrifugeUnused", "screwUnused", "liquidWeightKg", "liquidVolumeL", "liquidEstVolumeL", "liquidVariancePct"} & set(s)          # the signed log of an older run is unchanged


def test_a_historical_entry_is_stamped_as_not_held_to_the_new_rules():
    import kelp_erp_server as K
    src = pathlib.Path(K.__file__).read_text(encoding="utf-8")
    assert 'SET separation_rules_v2=? WHERE id=?", (0 if hist else 1, run_id)' in src                    # finalize: 1 for a new run, 0 for a historical entry (like ORP, KW-59)


def test_the_screens():
    flat = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
    assert "Both readings are required unless that unit was not used this run" in flat and "Enter at least one of the two" in flat       # new rule / the KW-18 text for an older run
    assert "The centrifuge and the screw press cannot both be marked not used." in flat
    assert "rules2" in flat and "run.progress.separationV2 === false" in flat
