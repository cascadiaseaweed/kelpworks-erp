"""KW-100: release units for a shipment before the heavy-metals results, by destination.

While only the heavy-metals results are still to come, a Quality Manager can release a lot, or part of a lot, of a REVIEWED run for a confirmed shipment to a customer whose
destination does not require them. Release and shipment are one signed act; the rest of the run stays Pending Release. Every other test must already pass, nothing can be
overridden, and Canada, Mexico and any customer with no destination wait for the normal release.
"""
import itertools

import pytest

from factories import METALS, MICROBIAL, finalize_run, finished_run, harvest_totes, lab_report, lots_of, sql
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
    name = "Early customer %d" % next(_names)
    people["admin"].post("/api/customers", {"name": name}).raise_for_status()
    cid = [c["id"] for c in people["admin"].get("/api/customers").json["customers"] if c["name"] == name][0]
    if dest:
        r = people["qm"].put("/api/customers/%d" % cid, {"destinationId": people["dests"][dest]})
        assert r.status == 200, r.text
    return cid


def microbial_only(srv, people, rid):
    """Passing microbial results on file, no heavy-metals results (the application rate is set so metals could be judged)."""
    sql(srv, "UPDATE settings SET value=1000 WHERE key='coa_application_rate_kg_ha'")
    att = lab_report(people["qm"], rid)
    people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "M-1", "attachmentId": att,
                                                              "results": [{"specCode": c, "value": v} for c, v in MICROBIAL.items()]}).raise_for_status()


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


def pending_run(srv, people, packs=2, micro=True, tds=1.5, sku="KELPIVEX", review=True):
    """A finalized run, reviewed by the Production Manager, TDS recorded, microbial results on file, heavy metals still to come. NO lab requisition."""
    rid, f = finalize_run(people["admin"], harvest_totes(people["admin"], 1), sku=sku, pack=(("New 1,000 L IBC Tote", packs),))
    assert f.ok, f.text
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=? WHERE id=?", (tds, rid))
    if review:
        people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    if micro:
        microbial_only(srv, people, rid)
    return rid


def release_ship(people, rid, cid, lines, who="qm", comment="US order confirmed; microbial and TDS pass; metals due", password=None, **extra):
    """lines = [(lot, units)] or [(lot, units, skuCode)]."""
    body = {"customerId": cid, "comment": comment, "password": password if password is not None else people["qm_pw"],
            "lines": [dict(fgLotId=l[0]["id"], qty=l[1], **({"skuCode": l[2]} if len(l) > 2 else {})) for l in lines]}
    body.update(extra)
    return people[who].post("/api/release/runs/%d/release-for-shipment" % rid, body)


def state(srv, rid):
    return sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0]


def detail(people, rid):
    return people["qm"].get("/api/release/runs/%d" % rid).json


def n_shipments(srv):
    return sql(srv, "SELECT COUNT(*) FROM shipments")[0][0]


def lot_row(srv, lot_id):
    r = sql(srv, "SELECT qty, status, sku_code FROM fg_lots WHERE id=?", (lot_id,))[0]
    return {"qty": r[0], "status": r[1], "sku": r[2]}


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


def test_it_is_off_until_an_administrator_switches_it_on(srv, people):
    enable(srv, 0)
    rid = pending_run(srv, people)
    lots, cid, before = lots_of(srv, rid), customer(people, "Washington"), n_shipments(srv)
    r = release_ship(people, rid, cid, [(lots[0], 1)])
    assert r.status == 403 and r.json["code"] == "conditional_off"
    assert n_shipments(srv) == before and lot_row(srv, lots[0]["id"]) == {"qty": 2, "status": "pending_release", "sku": "KELPIVEX"}
    info = detail(people, rid)["releaseForShipment"]
    assert info["enabled"] is False and info["eligible"] is False


# ---- who may sign, and what must be true first ----

def test_only_a_quality_manager_signs_with_their_password_and_a_reason(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    lots, cid, before = lots_of(srv, rid), customer(people, "Washington"), n_shipments(srv)
    assert release_ship(people, rid, cid, [(lots[0], 1)], who="pm", password=people["pm_pw"]).status == 403
    assert release_ship(people, rid, cid, [(lots[0], 1)], who="plain", password="x").status in (400, 403)
    assert release_ship(people, rid, cid, [(lots[0], 1)], who="admin", password="x").status in (400, 403)
    assert release_ship(people, rid, cid, [(lots[0], 1)], password="wrong").status == 400
    assert release_ship(people, rid, cid, [(lots[0], 1)], comment="").status == 400
    assert n_shipments(srv) == before and state(srv, rid) == "pending_release"
    assert release_ship(people, rid, cid, [(lots[0], 1)]).status == 200


def test_the_process_and_microbial_tests_must_already_be_met(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid = pending_run(srv, people, micro=False)                                  # no microbial results at all
    r = release_ship(people, rid, cid, [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and r.json["code"] == "release_for_shipment_blocked" and "Indicator organisms" in r.json["error"]
    rid2 = pending_run(srv, people, tds=0.5)                                     # TDS below its lower limit
    r = release_ship(people, rid2, cid, [(lots_of(srv, rid2)[0], 1)])
    assert r.status == 409 and "Process specifications" in r.json["error"] and "TDS" in r.json["error"]
    assert lots_of(srv, rid)[0]["status"] == lots_of(srv, rid2)[0]["status"] == "pending_release"


def test_the_run_must_have_been_reviewed(srv, people):
    enable(srv)
    rid = pending_run(srv, people, review=False)
    r = release_ship(people, rid, customer(people, "Washington"), [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and "review" in r.json["error"]


def test_no_lab_requisition_is_needed(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert sql(srv, "SELECT COUNT(*) FROM lab_requisitions WHERE run_id=?", (rid,))[0][0] == 0
    info = detail(people, rid)["releaseForShipment"]
    assert info["eligible"] is True and info["reasons"] == [] and "Arsenic (As)" in info["pending"]
    assert release_ship(people, rid, customer(people, "Washington"), [(lots_of(srv, rid)[0], 1)]).status == 200


def test_a_failed_metal_blocks_it_and_complete_metals_mean_release_normally(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid = pending_run(srv, people)
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-1")           # lead fails: 900 kg/ha against a limit of 100
    r = release_ship(people, rid, cid, [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and "Lead" in r.json["error"] and "cannot be released for shipment" in r.json["error"]
    rid2 = pending_run(srv, people)
    add_metals(srv, people, rid2)
    r = release_ship(people, rid2, cid, [(lots_of(srv, rid2)[0], 1)])
    assert r.status == 409 and "release the product normally" in r.json["error"]


def test_the_customer_destination_must_exist_be_in_use_and_not_require_metals(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    lot = lots_of(srv, rid)[0]
    for dest in ("Canada", "Oregon", None):
        r = release_ship(people, rid, customer(people, dest), [(lot, 1)])
        assert r.status == 409 and r.json["code"] == "release_for_shipment_blocked", dest
    assert "no destination" in release_ship(people, rid, customer(people), [(lot, 1)]).json["error"]
    cid = customer(people, "Washington")
    people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": False}).raise_for_status()
    try:
        assert release_ship(people, rid, cid, [(lot, 1)]).status == 409
    finally:
        people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": True}).raise_for_status()
    assert lot_row(srv, lot["id"])["qty"] == 2 and state(srv, rid) == "pending_release"
    assert release_ship(people, rid, cid, [(lot, 1)]).status == 200


def test_the_lines_must_be_pending_release_units_of_this_run(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid, other = pending_run(srv, people), pending_run(srv, people)
    lot, other_lot = lots_of(srv, rid)[0], lots_of(srv, other)[0]
    assert release_ship(people, rid, cid, [(other_lot, 1)]).status == 409                     # a Pending Release lot of ANOTHER run is not this release
    assert release_ship(people, rid, cid, [(lot, 0)]).status == 409                           # no units chosen
    assert release_ship(people, rid, cid, [(lot, 3)]).status == 400                           # more than the lot holds
    assert release_ship(people, rid, cid, []).status == 409
    assert lot_row(srv, lot["id"])["qty"] == 2 and lot_row(srv, other_lot["id"])["qty"] == 2


# ---- what it does ----

def test_a_whole_lot_is_released_and_shipped_in_one_step(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    r = release_ship(people, rid, cid, [(lot, 2)], reference="PO-77", carrier="truck")
    assert r.status == 200, r.text
    ship = r.json["shipment"]
    assert ship["destination"] == "Washington" and ship["beforeMetals"] is True
    assert ship["units"] == 2 and ship["reference"] == "PO-77"
    assert lot_row(srv, lot["id"]) == {"qty": 0, "status": "sold", "sku": "KELPIVEX"}
    assert state(srv, rid) == "pending_release"                                    # the run itself is not released
    d = detail(people, rid)
    ev = [e for e in d["events"] if e["type"] == "release_for_shipment"][0]
    assert ev["capacity"] == "Quality Manager" and ev["comment"] and ev["detail"]["shipment"] == ship["shipmentNo"] and ev["detail"]["destination"] == "Washington"
    assert ev["detail"]["lots"] == [{"lot": lot["number"], "qty": 2, "releasedEarly": True}] and d["chain"]["ok"] is True
    assert r.json["release"]["state"] == "pending_release"


def test_part_of_a_lot_leaves_the_rest_pending_and_a_second_release_works(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot = lots_of(srv, rid)[0]
    wa, canada = customer(people, "Washington"), customer(people, "Canada")
    assert release_ship(people, rid, wa, [(lot, 1)]).status == 200
    assert lot_row(srv, lot["id"]) == {"qty": 2, "status": "pending_release", "sku": "KELPIVEX"}
    assert release_ship(people, rid, canada, [(lot, 1)]).status == 409                       # the rest cannot go to Canada
    assert lot_row(srv, lot["id"])["qty"] == 2
    assert release_ship(people, rid, wa, [(lot, 1)]).status == 200                           # but another confirmed US shipment can
    assert lot_row(srv, lot["id"])["qty"] == 1 and state(srv, rid) == "pending_release"
    early = detail(people, rid)["earlyShipments"]
    assert sorted(e["qty"] for e in early) == [1, 1] and {e["destination"] for e in early} == {"Washington"}


def test_the_normal_release_later_puts_only_the_rest_on_hand(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    assert release_ship(people, rid, customer(people, "Washington"), [(lot, 1)]).status == 200
    add_metals(srv, people, rid)
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "Test data: pH is not set to specification"})
    assert r.status == 200, r.text
    assert state(srv, rid) == "released" and lot_row(srv, lot["id"]) == {"qty": 1, "status": "on_hand", "sku": "KELPIVEX"}
    assert detail(people, rid)["checklist"][-1]["key"] == "early" and detail(people, rid)["checklist"][-1]["state"] == "ok"


def test_a_lot_with_no_sku_yet_ships_as_the_sku_chosen(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2, sku="SACC-LKE")
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert detail(people, rid)["releaseForShipment"]["lots"][0]["skuAssigned"] is False
    r = release_ship(people, rid, cid, [(lot, 1)])
    assert r.status == 400 and "no SKU" in r.json["error"]
    r = release_ship(people, rid, cid, [(lot, 1, "KELPIVEX")])
    assert r.status == 200, r.text
    assert [l["sku"] for l in r.json["shipment"]["lines"]] == ["KELPIVEX"]
    rows = sql(srv, "SELECT status, qty, sku_code, sku_assigned FROM fg_lots WHERE run_id=? ORDER BY id", (rid,))
    assert sorted(rows) == sorted([("pending_release", 1, "SACC-LKE", 0), ("sold", 0, "KELPIVEX", 1)]), rows
    assert state(srv, rid) == "pending_release"


def test_cancelling_the_shipment_returns_the_units_to_pending_release_and_it_cannot_be_reinstated(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    sid = release_ship(people, rid, customer(people, "Washington"), [(lot, 2)]).json["shipment"]["id"]
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    assert lot_row(srv, lot["id"]) == {"qty": 2, "status": "pending_release", "sku": "KELPIVEX"}
    assert detail(people, rid)["earlyShipments"] == []
    r = people["plain"].put("/api/shipments/%d" % sid, {"status": "shipped"})
    assert r.status == 400 and "not released for sale" in r.json["error"]


# ---- a heavy-metals result that arrives later ----

def test_a_failed_metal_afterwards_blocks_the_rest_and_flags_what_shipped(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert release_ship(people, rid, cid, [(lot, 1)]).status == 200
    d = detail(people, rid)
    assert [i["state"] for i in d["checklist"] if i["key"] == "early"] == ["warn"] and "released for shipment before the heavy-metals results" in d["releaseStatus"]["text"]
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-2")
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "x"})
    assert r.status == 409 and r.json["code"] == "release_gate"                              # the rest cannot be released
    assert lot_row(srv, lot["id"])["status"] == "pending_release" and state(srv, rid) == "pending_release"
    d = detail(people, rid)
    early = [i for i in d["checklist"] if i["key"] == "early"][0]
    assert early["state"] == "fail" and "FAILED" in early["reason"] and "Washington" in early["reason"]
    recall = people["qm"].get("/api/release/runs/%d/recall" % rid).json
    assert len(recall["shippedBeforeHeavyMetals"]) == 1 and recall["shippedBeforeHeavyMetals"][0]["destination"] == "Washington"
    assert recall["customers"][0]["earlyUnits"] == 1 and recall["customers"][0]["destination"] == "Washington"


def test_the_integrity_check_and_reopen_account_for_the_shipped_units(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    assert release_ship(people, rid, customer(people, "Washington"), [(lot, 1)]).status == 200
    issues = [i for i in people["admin"].get("/api/integrity").json["issues"] if i["runId"] == rid and i["severity"] == "error"]
    assert issues == []
    r = people["qm"].post("/api/release/runs/%d/reopen" % rid, {"password": people["qm_pw"], "comment": "log needs correction"})
    assert r.status == 200
    ev = [e for e in detail(people, rid)["events"] if e["type"] == "reopened"][0]
    assert ev["detail"]["alreadyShippedLots"] and ev["detail"]["alreadyShippedLots"][0]["lot"] == lot["number"]


def test_the_certificate_says_heavy_metals_pending_once_units_have_shipped(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert people["qm"].get("/api/production/%d/coa.pdf" % rid).status == 200
    assert release_ship(people, rid, customer(people, "Washington"), [(lots_of(srv, rid)[0], 1)]).status == 200
    r = people["qm"].get("/api/production/%d/coa.pdf" % rid)
    assert r.status == 200 and r.body[:4] == b"%PDF"


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


def test_every_shipment_keeps_the_destination_it_was_made_to(srv, people):
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET packaging_tds_pct=1.5, release_state='legacy' WHERE id=?", (rid,))
    sql(srv, "UPDATE fg_lots SET status='on_hand' WHERE run_id=?", (rid,))
    cid = customer(people, "Washington")
    lot = lots_of(srv, rid)[0]
    s = people["plain"].post("/api/shipments", {"customerId": cid, "lines": [{"fgLotId": lot["id"], "qty": 1}]})
    assert s.status == 200, s.text
    assert s.json["shipment"]["destination"] == "Washington" and s.json["shipment"]["beforeMetals"] is False
    people["admin"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Canada"]}).raise_for_status()
    again = [x for x in people["plain"].get("/api/shipments").json["shipments"] if x["id"] == s.json["shipment"]["id"]][0]
    assert again["destination"] == "Washington"


# ---- released stock on the same shipment ----

def released_stock(srv, people, units=3):
    """A finalized run that is already released (On hand), for stock that rides on the same shipment."""
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET release_state='legacy' WHERE id=?", (rid,))
    sql(srv, "UPDATE fg_lots SET status='on_hand', qty=? WHERE run_id=?", (units, rid))
    return rid, lots_of(srv, rid)[0]


def test_released_stock_can_ride_on_the_same_shipment_and_only_the_release_counts_as_early(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    pending_lot = lots_of(srv, rid)[0]
    other, stock = released_stock(srv, people)
    cid = customer(people, "Washington")
    info = detail(people, rid)["releaseForShipment"]
    assert stock["id"] in [l["id"] for l in info["onHandLots"]] and pending_lot["id"] not in [l["id"] for l in info["onHandLots"]]
    r = release_ship(people, rid, cid, [(pending_lot, 1), (stock, 2)])
    assert r.status == 200, r.text
    ship = r.json["shipment"]
    assert ship["units"] == 3 and {l["lot"]: l["beforeMetals"] for l in ship["lines"]} == {pending_lot["number"]: True, stock["number"]: False}
    assert lot_row(srv, pending_lot["id"])["qty"] == 1 and lot_row(srv, stock["id"]) == {"qty": 1, "status": "on_hand", "sku": lot_row(srv, stock["id"])["sku"]}
    # only the released-early line is "shipped before the heavy-metals results", on the run it belongs to
    assert [e["qty"] for e in detail(people, rid)["earlyShipments"]] == [1]
    assert detail(people, other)["earlyShipments"] == [] and not [i for i in detail(people, other)["checklist"] if i["key"] == "early"]
    recall = people["qm"].get("/api/release/runs/%d/recall" % other).json
    assert recall["shippedBeforeHeavyMetals"] == [] and recall["customers"][0]["earlyUnits"] == 0 and recall["customers"][0]["units"] == 2
    ev = [e for e in detail(people, rid)["events"] if e["type"] == "release_for_shipment"][0]
    assert {x["lot"]: x["releasedEarly"] for x in ev["detail"]["lots"]} == {pending_lot["number"]: True, stock["number"]: False}


def test_a_release_for_shipment_needs_at_least_one_pending_release_lot_of_the_run(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    other, stock = released_stock(srv, people)
    cid, before = customer(people, "Washington"), n_shipments(srv)
    r = release_ship(people, rid, cid, [(stock, 1)])                               # released stock alone is a normal shipment, not a release
    assert r.status == 409 and "at least one Pending Release lot" in r.json["error"]
    assert n_shipments(srv) == before and lot_row(srv, stock["id"])["qty"] == 3


def test_the_rules_for_released_stock_still_apply_on_the_same_shipment(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    pending_lot = lots_of(srv, rid)[0]
    other_pending = lots_of(srv, pending_run(srv, people))[0]                      # another run's lot that is NOT released
    cid = customer(people, "Washington")
    assert release_ship(people, rid, cid, [(pending_lot, 1), (other_pending, 1)]).status == 400
    other, stock = released_stock(srv, people, units=1)
    assert release_ship(people, rid, cid, [(pending_lot, 1), (stock, 2)]).status == 400          # more than is on hand
    assert lot_row(srv, pending_lot["id"])["qty"] == 2 and lot_row(srv, stock["id"])["qty"] == 1


def test_cancelling_a_mixed_shipment_returns_each_line_to_where_it_came_from(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    pending_lot = lots_of(srv, rid)[0]
    other, stock = released_stock(srv, people, units=2)
    sid = release_ship(people, rid, customer(people, "Washington"), [(pending_lot, 2), (stock, 2)]).json["shipment"]["id"]
    assert lot_row(srv, pending_lot["id"])["status"] == "sold" and lot_row(srv, stock["id"])["status"] == "sold"
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    assert lot_row(srv, pending_lot["id"])["status"] == "pending_release" and lot_row(srv, stock["id"])["status"] == "on_hand"
    assert detail(people, rid)["earlyShipments"] == []
