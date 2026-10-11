"""Release gate (risk review batch 6): heavy metals and microbial tests must pass and TDS must be at or above its lower limit before Quality can release;
the review is the Production Manager's, the release the Quality Manager's; lab results cannot be quietly replaced; every rule change is on the record.
"""
import pytest

from factories import METALS, MICROBIAL, finished_run, lab_report, lots_of, release_ready_results, release_run, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    both, both_info = srv.make_user(role="user", isProductionManager=True, isQualityManager=True)
    return {"pm": pm, "qm": qm, "both": both, "admin": srv.admin_client(), "pm_pw": pm_info["password"], "qm_pw": qm_info["password"],
            "both_pw": both_info["password"], "pm_info": pm_info}


def reviewed_run(srv, people, tds=1.5):
    """A finalized run whose production log the Production Manager has signed, with TDS recorded in the Packaging QC check."""
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=? WHERE id=?", (tds, rid))
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    return rid


def release(people, rid, comment="Test data: pH is not set to specification"):
    return people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": comment})


def state(srv, rid):
    return sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0]


def gate(people, rid):
    return {g["code"]: g for g in people["qm"].get("/api/release/runs/%d" % rid).json["gate"]}


# ---- the gate ----

def test_the_default_rules_are_heavy_metals_and_microbial_must_pass_and_tds_must_meet_its_lower_limit(srv, people):
    specs = {s["code"]: s for s in people["admin"].get("/api/coa-specs").json["specs"]}
    assert all(specs[c]["releaseRule"] == "pass" for c in list(METALS) + list(MICROBIAL))
    assert specs["tds"]["releaseRule"] == "min"
    assert specs["ph"]["releaseRule"] == "" and specs["potash"]["releaseRule"] == ""


def test_a_run_with_no_lab_results_cannot_be_released(srv, people):
    rid = reviewed_run(srv, people)
    r = release(people, rid)
    assert r.status == 409 and r.json["code"] == "release_gate"
    assert "Arsenic" in r.json["error"] and "Salmonella" in r.json["error"]
    assert state(srv, rid) == "pending_release" and all(l["status"] == "pending_release" for l in lots_of(srv, rid))


def test_heavy_metals_cannot_be_judged_until_the_application_rate_is_set(srv, people):
    sql(srv, "UPDATE settings SET value=0 WHERE key='coa_application_rate_kg_ha'")
    rid = reviewed_run(srv, people)
    att = lab_report(people["qm"], rid)
    people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "L", "reportNumber": "R1", "attachmentId": att,
                                                              "results": [{"specCode": c, "value": v} for c, v in dict(MICROBIAL, **METALS).items()]}).raise_for_status()
    r = release(people, rid)
    assert r.status == 409 and "Application rate not set" in r.json["error"]
    sql(srv, "UPDATE settings SET value=1000 WHERE key='coa_application_rate_kg_ha'")
    assert release(people, rid).status == 200


def test_a_failing_heavy_metal_blocks_release_and_a_comment_does_not_override_it(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid, metals={"pb": "900000"})                # 900000 ppm x 1000 kg/ha = 900 kg/ha, limit 100
    r = release(people, rid, comment="I accept the risk")
    assert r.status == 409 and r.json["code"] == "release_gate" and "Lead" in r.json["error"]
    assert state(srv, rid) == "pending_release"


def test_a_failing_microbial_result_blocks_release(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid, micro={"salm": "Positive"})
    r = release(people, rid)
    assert r.status == 409 and "Salmonella" in r.json["error"]
    assert not gate(people, rid)["salm"]["ok"] and gate(people, rid)["apc"]["ok"]


def test_tds_below_its_lower_limit_blocks_release_and_at_or_above_passes(srv, people):
    rid = reviewed_run(srv, people, tds=0.8)
    release_ready_results(srv, people["qm"], rid)
    r = release(people, rid)
    assert r.status == 409 and "TDS" in r.json["error"] and "below the lower limit" in r.json["error"]
    rid = reviewed_run(srv, people, tds=1)                                                      # exactly the lower limit
    release_ready_results(srv, people["qm"], rid)
    assert gate(people, rid)["tds"]["ok"]
    assert release(people, rid).status == 200


def test_tds_not_recorded_blocks_release(srv, people):
    rid = reviewed_run(srv, people)
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=NULL WHERE id=?", (rid,))
    release_ready_results(srv, people["qm"], rid)
    r = release(people, rid)
    assert r.status == 409 and "TDS has not been recorded" in r.json["error"]


def test_the_gate_result_is_written_into_the_release_event(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid)
    assert release(people, rid).status == 200
    import json
    detail = json.loads(sql(srv, "SELECT detail FROM release_events WHERE run_id=? AND event_type='released'", (rid,))[0][0])
    codes = {g["code"] for g in detail["gate"]}
    assert {"tds", "apc", "salm", "pb", "hg"} <= codes and all(g["status"] in ("pass", "fail") for g in detail["gate"])


# ---- who signs ----

def test_only_a_production_manager_can_sign_the_review(srv, people):
    rid = finished_run(people["admin"])
    r = people["qm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["qm_pw"]})
    assert r.status == 403 and "Production Manager" in r.json["error"]
    assert state(srv, rid) == "pending_review"
    assert people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).status == 200
    assert people["pm"].get("/api/release").json["me"] == {"canReview": True, "canRelease": False}


def test_only_a_quality_manager_can_sign_the_release(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid)
    r = people["pm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["pm_pw"], "comment": "x"})
    assert r.status == 403
    assert state(srv, rid) == "pending_release"


def test_one_person_may_do_both_unless_the_setting_forbids_it(srv, people):
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=1.5 WHERE id=?", (rid,))
    both = people["both"]
    both.post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["both_pw"]}).raise_for_status()
    release_ready_results(srv, people["qm"], rid)
    assert people["admin"].put("/api/settings/release_separate_signers", {"value": 1}).status == 200
    try:
        r = both.post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["both_pw"], "comment": "x"})
        assert r.status == 403 and r.json["code"] == "same_person"
        assert state(srv, rid) == "pending_release"
        assert release(people, rid).status == 200                                              # a different Quality Manager can
    finally:
        people["admin"].put("/api/settings/release_separate_signers", {"value": 0})


def test_the_finalizer_can_be_barred_from_reviewing_their_own_run(srv, people):
    sql(srv, "UPDATE settings SET value=1 WHERE key='review_not_by_finalizer'")
    try:
        rid = finished_run(people["admin"])
        name = sql(srv, "SELECT name FROM users WHERE email=?", (people["pm_info"]["email"],))[0][0]
        sql(srv, "UPDATE production_runs SET finalized_by=? WHERE id=?", (name, rid))
        r = people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]})
        assert r.status == 403 and r.json["code"] == "same_person"
        sql(srv, "UPDATE production_runs SET finalized_by='Someone Else' WHERE id=?", (rid,))
        assert people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).status == 200
    finally:
        sql(srv, "UPDATE settings SET value=0 WHERE key='review_not_by_finalizer'")


# ---- lab results cannot be quietly replaced ----

def test_a_failed_result_must_be_voided_before_a_new_one_is_entered(srv, people):
    rid = reviewed_run(srv, people)
    att = lab_report(people["qm"], rid)
    bad = {"labName": "L", "reportNumber": "R1", "attachmentId": att, "results": [{"specCode": "salm", "value": "Positive"}]}
    people["qm"].post("/api/production/%d/lab-results" % rid, bad).raise_for_status()
    retest = {"labName": "L", "reportNumber": "R2", "attachmentId": att, "results": [{"specCode": "salm", "value": "Negative"}]}
    r = people["qm"].post("/api/production/%d/lab-results" % rid, retest)
    assert r.status == 409 and r.json["code"] == "void_first"
    result_id = [x for x in people["qm"].get("/api/production/%d/lab-results" % rid).json["results"] if not x.get("voidedAt")][0]["id"]
    assert people["qm"].post("/api/production/%d/lab-results/%d/void" % (rid, result_id), {"reason": "Wrong sample tested"}).status == 200
    assert people["qm"].post("/api/production/%d/lab-results" % rid, retest).status == 200


def test_a_result_that_decides_release_needs_its_lab_report(srv, people):
    rid = reviewed_run(srv, people)
    r = people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "L", "reportNumber": "R1", "results": [{"specCode": "apc", "value": "<10"}]})
    assert r.status == 400 and r.json["code"] == "report_required"
    r = people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "L", "reportNumber": "R1", "results": [{"specCode": "potash", "value": "1"}]})
    assert r.status == 200                                                                    # a test with no release rule does not need it


# ---- after release ----

def test_voiding_a_result_the_release_depended_on_holds_the_lots(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid)
    assert release(people, rid).status == 200
    assert all(l["status"] == "on_hand" for l in lots_of(srv, rid))
    pb = [x for x in people["qm"].get("/api/production/%d/lab-results" % rid).json["results"] if x["specCode"] == "pb"][0]
    assert people["qm"].post("/api/production/%d/lab-results/%d/void" % (rid, pb["id"]), {"reason": "Report withdrawn by the lab"}).status == 200
    assert all(l["status"] == "hold" for l in lots_of(srv, rid))
    assert sql(srv, "SELECT COUNT(*) FROM release_events WHERE run_id=? AND event_type='lab_result_hold'", (rid,))[0][0] == 1


def test_a_hash_mismatch_at_release_returns_the_run_to_review_and_the_void_sticks(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid)
    sql(srv, "UPDATE production_runs SET location='Changed behind the app' WHERE id=?", (rid,))
    r = release(people, rid)
    assert r.status == 409
    assert state(srv, rid) == "pending_review"                                                 # the void was committed, not rolled back with the error
    assert sql(srv, "SELECT COUNT(*) FROM release_events WHERE run_id=? AND event_type='voided'", (rid,))[0][0] == 1


# ---- the record of changes to the rules ----

def test_changing_a_specification_is_logged_with_who_and_what(srv, people):
    admin = people["admin"]
    r = admin.put("/api/coa-specs/tds", {"minVal": 1.2, "maxVal": 2, "releaseRule": "min"})
    assert r.status == 200 and r.json["spec"]["minVal"] == 1.2
    log = admin.get("/api/coa-specs/tds/log").json["log"]
    assert [(e["field"], e["old"], e["new"]) for e in log] == [("Minimum", "1.0", "1.2")] and log[0]["by"]
    assert admin.put("/api/coa-specs/tds", {"minVal": 1, "maxVal": 2, "releaseRule": "min"}).status == 200


def test_a_rule_that_needs_a_lower_limit_is_refused_without_one(srv, people):
    admin = people["admin"]
    assert admin.put("/api/coa-specs/potash", {"minVal": "", "releaseRule": "min"}).status == 400
    assert admin.put("/api/coa-specs/potash", {"releaseRule": "sometimes"}).status == 400


def test_changing_a_setting_is_logged(srv, people):
    admin = people["admin"]
    assert admin.put("/api/settings/coa_application_rate_kg_ha", {"value": 1234}).status == 200
    assert admin.put("/api/settings/coa_application_rate_kg_ha", {"value": 1000}).status == 200
    log = admin.get("/api/settings/log").json["log"]
    assert [(e["key"], e["old"], e["new"]) for e in log[:2]] == [("coa_application_rate_kg_ha", "1234.0", "1000.0"), ("coa_application_rate_kg_ha", "1000.0", "1234.0")]
    assert people["qm"].get("/api/settings/log").status == 403


def test_password_resets_and_role_changes_are_on_the_permission_log(srv, people):
    admin = people["admin"]
    victim, info = srv.make_user(role="user")
    uid = [u["id"] for u in admin.get("/api/users").json["users"] if u["email"] == info["email"]][0]
    assert admin.post("/api/users/%d/password" % uid, {"password": "a-brand-new-password-1"}).status == 200
    assert admin.put("/api/users/%d" % uid, {"role": "admin"}).status == 200
    assert admin.put("/api/users/%d" % uid, {"active": False}).status == 200
    rows = [r[0] for r in sql(srv, "SELECT permission FROM user_permission_log WHERE user_id=? ORDER BY id", (uid,))]
    assert "Password reset by an administrator" in rows and "Administrator role" in rows and "Account active" in rows


# ---- recall ----

def test_the_recall_report_names_the_customers_who_received_a_run(srv, people):
    rid = reviewed_run(srv, people)
    release_ready_results(srv, people["qm"], rid)
    assert release(people, rid).status == 200
    admin = people["admin"]
    cust = admin.post("/api/customers", {"name": "Recall Test Farm", "email": "farm@example.com"})
    cust.raise_for_status()
    cid = [c["id"] for c in cust.json["customers"] if c["name"] == "Recall Test Farm"][0]
    lot = lots_of(srv, rid)[0]
    sh = admin.post("/api/shipments", {"customerId": cid, "shipDate": "2026-06-01", "lines": [{"fgLotId": lot["id"], "qty": 1}]})
    sh.raise_for_status()
    rep = people["qm"].get("/api/release/runs/%d/recall" % rid).json
    assert [c["name"] for c in rep["customers"]] == ["Recall Test Farm"] and rep["customers"][0]["units"] == 1
    assert rep["lots"][0]["unitsShipped"] == 1 and rep["lots"][0]["shipments"][0]["customer"] == "Recall Test Farm"
