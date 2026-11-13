"""KW-100: conditional release for heavy metals, by destination.

A Quality Manager can release a REVIEWED run whose process specifications and indicator-organism tests are met while its heavy-metals results are still to come.
The product can then ship only to a customer whose destination does not require heavy metals; Canada, Mexico and any customer without a destination wait for the full
release. A failed heavy-metals result puts the unsold lots on hold and the recall report lists what shipped under the conditional release.
"""
import itertools

import pytest

from factories import METALS, MICROBIAL, finished_run, lab_report, lots_of, release_ready_results, sql
from harness import Server

_names = itertools.count(1)


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    plain, plain_info = srv.make_user(role="user")
    amender, amender_info = srv.make_user(role="user", canAmendLog=True)
    admin = srv.admin_client()
    admin.post("/api/destinations", {"name": "Washington", "requiresMetals": False}).raise_for_status()
    admin.post("/api/destinations", {"name": "Oregon", "requiresMetals": True}).raise_for_status()
    dests = {d["name"]: d["id"] for d in admin.get("/api/destinations").json["destinations"]}
    return {"pm": pm, "qm": qm, "plain": plain, "admin": admin, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "dests": dests,
            "amender": amender, "am_pw": amender_info["password"]}


def enable(srv, on=1):
    sql(srv, "UPDATE settings SET value=? WHERE key='conditional_release_enabled'", (on,))


def customer(people, dest=None):
    name = "Cond customer %d" % next(_names)
    people["admin"].post("/api/customers", {"name": name}).raise_for_status()
    cid = [c["id"] for c in people["admin"].get("/api/customers").json["customers"] if c["name"] == name][0]
    if dest:
        r = people["qm"].put("/api/customers/%d" % cid, {"destinationId": people["dests"][dest]})
        assert r.status == 200, r.text
    return cid


def request_metals(srv, rid, analysis="Arsenic (As)"):
    """Test setup: a lab requisition for the run that asks for a heavy-metals analysis."""
    sql(srv, "INSERT INTO lab_requisitions (req_number, run_id, lab_name, created_at) VALUES (?,?,?,?)", ("REQ-T-%d" % next(_names), rid, "Test Lab", "2026-11-01T00:00:00Z"))
    q = sql(srv, "SELECT MAX(id) FROM lab_requisitions")[0][0]
    sql(srv, "INSERT INTO requisition_samples (requisition_id, sample_id, analyses) VALUES (?,?,?)", (q, 1, '["%s"]' % analysis))


def microbial_only(srv, people, rid):
    """Passing microbial results on file, no heavy-metals results (the application rate is set so metals could be judged)."""
    sql(srv, "UPDATE settings SET value=1000 WHERE key='coa_application_rate_kg_ha'")
    att = lab_report(people["qm"], rid)
    people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "M-1", "attachmentId": att,
                                                              "results": [{"specCode": c, "value": v} for c, v in MICROBIAL.items()]}).raise_for_status()
    return att


def add_metals(srv, people, rid, metals=None, report="T-2", only=None):
    """Heavy-metals results from a lab report: all eleven passing (ppm), `metals` overriding some, or just the codes in `only`."""
    values = dict(METALS, **(metals or {}))
    if only is not None:
        values = {c: values[c] for c in only}
    att = lab_report(people["qm"], rid, "metals-%s.pdf" % report)
    r = people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": report, "attachmentId": att,
                                                                  "results": [{"specCode": c, "value": v} for c, v in values.items()]})
    r.raise_for_status()
    return r


def pending_run(srv, people, packs=1, requested=True, micro=True, tds=1.5):
    """A finalized run, reviewed by the Production Manager, TDS recorded, microbial results on file, heavy metals still to come."""
    from factories import finalize_run, harvest_totes
    rid, f = finalize_run(people["admin"], harvest_totes(people["admin"], 1), pack=(("New 1,000 L IBC Tote", packs),))
    assert f.ok, f.text
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=? WHERE id=?", (tds, rid))
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    if micro:
        microbial_only(srv, people, rid)
    if requested:
        request_metals(srv, rid)
    return rid


def conditional(people, rid, who="qm", comment="Metals results due; microbial and TDS pass", password=None):
    pw = password if password is not None else people["qm_pw"]
    return people[who].post("/api/release/runs/%d/conditional" % rid, {"password": pw, "comment": comment})


def state(srv, rid):
    return sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0]


def detail(people, rid):
    return people["qm"].get("/api/release/runs/%d" % rid).json


def ship(people, cid, lot, qty=1, who="plain"):
    return people[who].post("/api/shipments", {"customerId": cid, "lines": [{"fgLotId": lot["id"], "qty": qty}]})


# ---- the gate by category, and the switch ----

def test_the_release_gate_names_its_three_categories(srv, people):
    rid = pending_run(srv, people, micro=False)
    d = detail(people, rid)
    cats = {g["code"]: g["category"] for g in d["gate"]}
    assert cats["as"] == "Heavy metals" and cats["salm"] == "Indicator organisms" and cats["tds"] == "Process specifications"
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "x"})
    assert r.status == 409 and r.json["code"] == "release_gate"
    assert "Not met:" in r.json["error"] and "Heavy metals" in r.json["error"] and "Indicator organisms" in r.json["error"]
    assert [c["name"] for c in people["admin"].get("/api/refdata").json["gateCategories"]] == ["Process specifications", "Heavy metals", "Indicator organisms"]


def test_conditional_release_is_off_until_an_administrator_switches_it_on(srv, people):
    enable(srv, 0)
    rid = pending_run(srv, people)
    r = conditional(people, rid)
    assert r.status == 403 and r.json["code"] == "conditional_off"
    assert state(srv, rid) == "pending_release" and all(l["status"] == "pending_release" for l in lots_of(srv, rid))
    assert detail(people, rid)["conditionalRelease"]["enabled"] is False


# ---- who may sign, and what must be true first ----

def test_only_a_quality_manager_signs_with_their_password_and_a_reason(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid, who="pm", password=people["pm_pw"]).status == 403
    assert conditional(people, rid, who="plain", password="x").status in (400, 403)
    assert conditional(people, rid, who="admin", password="admin-pass").status in (400, 403)
    assert conditional(people, rid, password="wrong").status == 400
    assert conditional(people, rid, comment="").status == 400
    assert state(srv, rid) == "pending_release"
    assert conditional(people, rid).status == 200


def test_the_process_and_microbial_tests_must_already_be_met(srv, people):
    enable(srv)
    rid = pending_run(srv, people, micro=False)                                  # no microbial results at all
    r = conditional(people, rid)
    assert r.status == 409 and r.json["code"] == "conditional_blocked" and "Indicator organisms" in r.json["error"]
    rid2 = pending_run(srv, people, tds=0.5)                                     # TDS below its lower limit
    r = conditional(people, rid2)
    assert r.status == 409 and "Process specifications" in r.json["error"] and "TDS" in r.json["error"]
    assert state(srv, rid) == state(srv, rid2) == "pending_release"


def test_the_run_must_have_been_reviewed(srv, people):
    enable(srv)
    rid = finished_run(people["admin"])                                          # finalized, not reviewed
    r = conditional(people, rid)
    assert r.status == 409 and "review" in r.json["error"]


def test_a_heavy_metals_analysis_must_be_on_a_requisition(srv, people):
    enable(srv)
    rid = pending_run(srv, people, requested=False)
    r = conditional(people, rid)
    assert r.status == 409 and "requisition" in r.json["error"]
    request_metals(srv, rid, analysis="Total Nitrogen")                          # not a heavy metal
    assert conditional(people, rid).status == 409
    request_metals(srv, rid, analysis="Lead (Pb)")
    assert conditional(people, rid).status == 200


def test_a_failed_metal_cannot_be_conditionally_released_and_complete_metals_mean_release_normally(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-1")           # lead fails: 900 kg/ha against a limit of 100
    r = conditional(people, rid)
    assert r.status == 409 and "Lead" in r.json["error"] and "cannot be conditionally released" in r.json["error"]
    rid2 = pending_run(srv, people)
    add_metals(srv, people, rid2)
    r = conditional(people, rid2)
    assert r.status == 409 and "release the product normally" in r.json["error"]


# ---- what a conditional release does ----

def test_a_conditional_release_puts_the_lots_on_hand_and_is_on_the_chain(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    r = conditional(people, rid)
    assert r.status == 200, r.text
    assert state(srv, rid) == "conditional" and [l["status"] for l in lots_of(srv, rid)] == ["on_hand"]
    d = detail(people, rid)
    ev = [e for e in d["events"] if e["type"] == "conditional_release"][0]
    assert ev["capacity"] == "Quality Manager" and ev["comment"] and ev["detail"]["pendingCategories"] == ["metals"] and ev["detail"]["to"] == "conditional"
    assert d["chain"]["ok"] is True
    assert d["releaseStatus"]["stage"].startswith("Conditionally released") and d["label"].startswith("Conditionally released")
    assert d["conditionalBy"] and "heavy" in d["releaseStatus"]["next"].lower()
    assert [i["state"] for i in d["checklist"] if i["key"] == "gate"] == ["warn"]
    assert people["qm"].get("/api/production/%d/coa.pdf" % rid).status == 200
    assert conditional(people, rid).status == 409                                  # not a second time


def test_a_rejected_decision_is_not_offered_for_a_conditional_run_but_reopen_is(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid).status == 200
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "reject", "password": people["qm_pw"], "comment": "no"})
    assert r.status == 409
    r = people["qm"].post("/api/release/runs/%d/reopen" % rid, {"password": people["qm_pw"], "comment": "metals sample was mislabelled"})
    assert r.status == 200
    assert state(srv, rid) == "pending_review" and all(l["status"] == "pending_release" for l in lots_of(srv, rid))


def test_opening_an_amendment_on_a_conditional_run_holds_its_lots_and_cancelling_restores_it(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid).status == 200
    opened = people["amender"].post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "typo fix", "password": people["am_pw"]})
    assert opened.status == 200, opened.text
    assert state(srv, rid) == "amending" and [l["status"] for l in lots_of(srv, rid)] == ["pending_release"]
    aid = opened.json["amendment"]["id"]
    assert people["amender"].post("/api/production/%d/amendments/%d/cancel" % (rid, aid), {"comment": "nothing to change"}).status == 200
    assert state(srv, rid) == "conditional" and [l["status"] for l in lots_of(srv, rid)] == ["on_hand"]


def test_the_integrity_check_has_no_complaint_about_a_conditional_run(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid).status == 200
    issues = [i for i in people["admin"].get("/api/integrity").json["issues"] if i["runId"] == rid and i["area"] in ("Release gate", "Production log")]
    assert issues == []


# ---- shipping by destination ----

def test_product_ships_only_to_a_destination_that_does_not_require_heavy_metals(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    assert conditional(people, rid).status == 200
    lots = lots_of(srv, rid)
    wa, canada, nowhere, oregon = customer(people, "Washington"), customer(people, "Canada"), customer(people), customer(people, "Oregon")
    for cid in (canada, nowhere, oregon):
        r = ship(people, cid, lots[0])
        assert r.status == 400 and r.json["code"] == "destination_requires_metals" and "conditionally released" in r.json["error"], r.text
    assert "no destination" in ship(people, nowhere, lots[0]).json["error"]
    r = ship(people, wa, lots[0])
    assert r.status == 200, r.text
    s = r.json["shipment"]
    assert s["destination"] == "Washington" and s["conditionalRelease"] is True
    assert [l["qty"] for l in lots_of(srv, rid)][0] == lots[0]["qty"] - 1


def test_a_run_can_be_split_between_a_us_customer_and_canada_once_the_metals_are_in(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    assert conditional(people, rid).status == 200
    lots = lots_of(srv, rid)
    wa, canada = customer(people, "Washington"), customer(people, "Canada")
    assert len(lots) == 1 and lots[0]["qty"] == 2
    assert ship(people, wa, lots[0]).status == 200                                 # one unit of the lot goes to the US customer
    assert ship(people, canada, lots[0]).status == 400                           # the other unit waits for Canada
    add_metals(srv, people, rid, report="OK-1")
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "Test data: pH is not set to specification"})
    assert r.status == 200, r.text
    assert state(srv, rid) == "released"
    assert ship(people, canada, lots[0]).status == 200                           # the full release opens every destination


def test_a_cancelled_shipment_is_judged_by_the_destination_it_shipped_to(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    assert conditional(people, rid).status == 200
    lots = lots_of(srv, rid)
    cid = customer(people, "Washington")
    sid = ship(people, cid, lots[0]).json["shipment"]["id"]
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    assert lots_of(srv, rid)[0]["status"] == "on_hand" and lots_of(srv, rid)[0]["qty"] == lots[0]["qty"]
    people["admin"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Canada"]}).raise_for_status()      # the customer moves to Canada afterwards
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "shipped"}).status == 200      # the shipment was made to Washington
    sql(srv, "UPDATE shipments SET destination_id=NULL, destination_name=NULL WHERE id=?", (sid,))
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    r = people["plain"].put("/api/shipments/%d" % sid, {"status": "shipped"})      # an older shipment with no recorded destination cannot be reinstated
    assert r.status == 400 and "no destination" in r.json["error"]


# ---- a failed heavy-metals result afterwards ----

def test_a_failed_metal_holds_the_unsold_lots_and_the_recall_report_lists_what_shipped(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    assert conditional(people, rid).status == 200
    lots = lots_of(srv, rid)
    wa = customer(people, "Washington")
    assert ship(people, wa, lots[0], qty=1).status == 200
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-2")
    after = lots_of(srv, rid)
    assert [l["status"] for l in after] == ["hold"] and after[0]["qty"] == 1
    events = [e for e in detail(people, rid)["events"] if e["type"] == "lab_result_hold"]
    assert events and any("Lead" in p for p in events[-1]["detail"]["problems"])
    recall = people["qm"].get("/api/release/runs/%d/recall" % rid).json
    shipped = recall["shippedUnderConditionalRelease"]
    assert len(shipped) == 1 and shipped[0]["destination"] == "Washington" and shipped[0]["qty"] == 1
    assert recall["customers"][0]["conditionalUnits"] == 1 and recall["customers"][0]["destination"] == "Washington"
    # a Quality Manager cannot lift the hold by hand while the metal still fails
    r = people["qm"].put("/api/fg/%d" % lots[0]["id"], {"status": "on_hand", "reason": "retest pending"})
    assert r.status == 409 and r.json["code"] == "release_gate"
    assert people["qm"].post("/api/release/runs/%d/release-from-hold" % rid, {"reason": "retest pending"}).status == 409


def test_a_missing_metal_alone_never_holds_a_conditional_run(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid).status == 200
    add_metals(srv, people, rid, report="PART", only=("as", "cd"))                # a partial set that passes: the other nine are still to come
    assert [l["status"] for l in lots_of(srv, rid)] == ["on_hand"] and state(srv, rid) == "conditional"
    assert detail(people, rid)["conditionalRelease"]["pending"] != [] and not [e for e in detail(people, rid)["events"] if e["type"] == "lab_result_hold"]


# ---- destinations and the customer's destination ----

def test_canada_and_mexico_exist_and_require_heavy_metals(srv, people):
    d = {x["name"]: x for x in people["plain"].get("/api/destinations").json["destinations"]}
    assert d["Canada"]["requiresMetals"] and d["Mexico"]["requiresMetals"] and d["Canada"]["active"]


def test_only_an_administrator_edits_destinations_and_every_change_is_logged(srv, people):
    assert people["qm"].post("/api/destinations", {"name": "Idaho"}).status == 403
    assert people["plain"].put("/api/destinations/%d" % people["dests"]["Oregon"], {"requiresMetals": False}).status == 403
    r = people["admin"].post("/api/destinations", {"name": "Idaho"})
    assert r.status == 200 and [x for x in r.json["destinations"] if x["name"] == "Idaho"][0]["requiresMetals"] is True      # fail-safe default
    assert people["admin"].post("/api/destinations", {"name": "idaho"}).status == 409
    did = r.json["id"]
    assert people["admin"].put("/api/destinations/%d" % did, {"requiresMetals": False, "name": "Idaho (state)"}).status == 200
    log = people["admin"].get("/api/destinations/%d/log" % did).json["log"]
    assert {x["field"] for x in log} >= {"Name", "Heavy-metals results required before shipping"} and all(x["by"] for x in log)
    assert people["qm"].get("/api/destinations/%d/log" % did).status == 403


def test_an_inactive_destination_is_treated_as_requiring_heavy_metals(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert conditional(people, rid).status == 200
    lots = lots_of(srv, rid)
    cid = customer(people, "Washington")
    people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": False}).raise_for_status()
    try:
        assert ship(people, cid, lots[0]).status == 400
    finally:
        people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": True}).raise_for_status()
    assert ship(people, cid, lots[0]).status == 200


def test_only_an_administrator_or_quality_manager_sets_a_customers_destination(srv, people):
    cid = customer(people)
    assert people["plain"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Washington"]}).status == 403
    assert people["pm"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Washington"]}).status == 403
    assert people["plain"].put("/api/customers/%d" % cid, {"contact": "Pat", "destinationId": None}).status == 200        # an unchanged destination needs no permission
    assert people["plain"].post("/api/customers", {"name": "Sneaky %d" % next(_names), "destinationId": people["dests"]["Washington"]}).status == 403
    assert people["qm"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Washington"]}).status == 200
    assert people["admin"].put("/api/customers/%d" % cid, {"destinationId": 99999}).status == 400
    got = [c for c in people["plain"].get("/api/customers").json["customers"] if c["id"] == cid][0]
    assert got["destination"] == "Washington" and got["destinationId"] == people["dests"]["Washington"] and got["contact"] == "Pat"
    people["admin"].put("/api/customers/%d" % cid, {"destinationId": ""}).raise_for_status()
    rows = sql(srv, "SELECT old_value, new_value FROM config_log WHERE area='customer-destination' ORDER BY id")
    assert (None, "Washington") in rows and ("Washington", None) in rows
