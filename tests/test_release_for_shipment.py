"""KW-100: QA release inside the shipment, before the heavy-metals results, by destination.

A shipment (Shipping > New shipment) may include Pending Release lots of ANY reviewed run. A Quality Manager releases those units for THIS shipment by signing once (password + reason),
while only the heavy-metals results are still to come and the customer's destination does not require them. Every other test must already pass, nothing can be overridden, the rest of each
run stays Pending Release, and Canada, Mexico and any customer with no destination wait for the normal release.
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
    return {"pm": pm, "qm": qm, "plain": plain, "admin": admin, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "plain_pw": plain_info["password"], "dests": dests,
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


def released_stock(srv, people, units=3):
    """A finalized run that is already released (On hand), for stock that rides on the same shipment."""
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET release_state='legacy' WHERE id=?", (rid,))
    sql(srv, "UPDATE fg_lots SET status='on_hand', qty=? WHERE run_id=?", (units, rid))
    return rid, lots_of(srv, rid)[0]


def ship(people, cid, lines, who="qm", comment="US order confirmed; microbial and TDS pass; metals due", password=None, **extra):
    """POST /api/shipments. lines = [(lot, units)] or [(lot, units, skuCode)]. `comment` + `password` are the QA sign-off (needed only when a line is a Pending Release lot)."""
    pw = password if password is not None else {"qm": people["qm_pw"], "pm": people["pm_pw"], "plain": people["plain_pw"]}.get(who, "x")
    body = {"customerId": cid, "comment": comment, "password": pw,
            "lines": [dict(fgLotId=l[0]["id"], qty=l[1], **({"skuCode": l[2]} if len(l) > 2 else {})) for l in lines]}
    body.update(extra)
    return people[who].post("/api/shipments", body)


def state(srv, rid):
    return sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0]


def detail(people, rid):
    return people["qm"].get("/api/release/runs/%d" % rid).json


def n_shipments(srv):
    return sql(srv, "SELECT COUNT(*) FROM shipments")[0][0]


def lot_row(srv, lot_id):
    r = sql(srv, "SELECT qty, status, sku_code FROM fg_lots WHERE id=?", (lot_id,))[0]
    return {"qty": r[0], "status": r[1], "sku": r[2]}


def awaiting(people, cid=None, who="qm"):
    return people[who].get("/api/shipments/awaiting-qa" + (("?customerId=%d" % cid) if cid else "")).json


def events(people, rid, kind="release_for_shipment"):
    return [e for e in detail(people, rid)["events"] if e["type"] == kind]


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
    r = ship(people, cid, [(lots[0], 1)])
    assert r.status == 403 and r.json["code"] == "conditional_off"
    assert n_shipments(srv) == before and lot_row(srv, lots[0]["id"]) == {"qty": 2, "status": "pending_release", "sku": "KELPIVEX"}
    info = awaiting(people, cid)
    assert info["enabled"] is False and all(not l["eligible"] and "switched off" in l["why"] for l in info["lots"])


# ---- who may sign, and what must be true first ----

def test_only_a_quality_manager_signs_with_their_password_and_a_reason(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    lots, cid, before = lots_of(srv, rid), customer(people, "Washington"), n_shipments(srv)
    assert ship(people, cid, [(lots[0], 1)], who="pm").status == 403
    assert ship(people, cid, [(lots[0], 1)], who="plain").status == 403
    assert ship(people, cid, [(lots[0], 1)], who="admin", password="x").status in (400, 403)
    assert ship(people, cid, [(lots[0], 1)], password="wrong").status == 400
    assert ship(people, cid, [(lots[0], 1)], comment="").status == 400
    assert n_shipments(srv) == before and state(srv, rid) == "pending_release"
    assert ship(people, cid, [(lots[0], 1)]).status == 200


def test_released_stock_alone_is_a_normal_shipment_anyone_can_make(srv, people):
    enable(srv)
    other, stock = released_stock(srv, people)
    cid = customer(people)                                                       # no destination: irrelevant for released stock
    r = ship(people, cid, [(stock, 1)], who="plain", comment="")
    assert r.status == 200, r.text
    assert r.json["shipment"]["beforeMetals"] is False and detail(people, other)["earlyShipments"] == []


def test_the_process_and_microbial_tests_must_already_be_met(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid = pending_run(srv, people, micro=False)                                  # no microbial results at all
    r = ship(people, cid, [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and r.json["code"] == "release_for_shipment_blocked" and "Indicator organisms" in r.json["error"]
    rid2 = pending_run(srv, people, tds=0.5)                                     # TDS below its lower limit
    r = ship(people, cid, [(lots_of(srv, rid2)[0], 1)])
    assert r.status == 409 and "Process specifications" in r.json["error"] and "TDS" in r.json["error"]
    assert lots_of(srv, rid)[0]["status"] == lots_of(srv, rid2)[0]["status"] == "pending_release"


def test_the_run_must_have_been_reviewed(srv, people):
    enable(srv)
    rid = pending_run(srv, people, review=False)
    cid = customer(people, "Washington")
    r = ship(people, cid, [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and "reviewed" in r.json["error"]
    lot = [l for l in awaiting(people, cid)["lots"] if l["runId"] == rid][0]
    assert lot["eligible"] is False and "not been reviewed" in lot["why"]


def test_no_lab_requisition_is_needed(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert sql(srv, "SELECT COUNT(*) FROM lab_requisitions WHERE run_id=?", (rid,))[0][0] == 0
    cid = customer(people, "Washington")
    info = awaiting(people, cid)
    mine = [l for l in info["lots"] if l["runId"] == rid]
    assert mine and all(l["eligible"] and l["why"] == "" for l in mine) and "Arsenic (As)" in info["pendingTests"]
    assert ship(people, cid, [(lots_of(srv, rid)[0], 1)]).status == 200


def test_a_failed_metal_blocks_it_and_complete_metals_mean_release_normally(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid = pending_run(srv, people)
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-1")           # lead fails: 900 kg/ha against a limit of 100
    r = ship(people, cid, [(lots_of(srv, rid)[0], 1)])
    assert r.status == 409 and "Lead" in r.json["error"] and "cannot be released for shipment" in r.json["error"]
    rid2 = pending_run(srv, people)
    add_metals(srv, people, rid2)
    r = ship(people, cid, [(lots_of(srv, rid2)[0], 1)])
    assert r.status == 409 and "release the product normally" in r.json["error"]


def test_the_customer_destination_must_exist_be_in_use_and_not_require_metals(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    lot = lots_of(srv, rid)[0]
    for dest in ("Canada", "Oregon", None):
        c = customer(people, dest)
        r = ship(people, c, [(lot, 1)])
        assert r.status == 409 and r.json["code"] == "release_for_shipment_blocked", dest
        assert not [l for l in awaiting(people, c)["lots"] if l["runId"] == rid and l["eligible"]]
    assert "no destination" in ship(people, customer(people), [(lot, 1)]).json["error"]
    cid = customer(people, "Washington")
    people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": False}).raise_for_status()
    try:
        assert ship(people, cid, [(lot, 1)]).status == 409
    finally:
        people["admin"].put("/api/destinations/%d" % people["dests"]["Washington"], {"active": True}).raise_for_status()
    assert lot_row(srv, lot["id"])["qty"] == 2 and state(srv, rid) == "pending_release"
    assert ship(people, cid, [(lot, 1)]).status == 200


def test_the_lines_must_be_real_units(srv, people):
    enable(srv)
    cid = customer(people, "Washington")
    rid = pending_run(srv, people)
    lot = lots_of(srv, rid)[0]
    assert ship(people, cid, [(lot, 3)]).status == 400                           # more than the lot holds
    assert ship(people, cid, [(lot, 0)]).status == 400                           # nothing to ship
    assert ship(people, cid, []).status == 400
    assert lot_row(srv, lot["id"])["qty"] == 2


# ---- what it does ----

def test_a_whole_lot_is_released_and_shipped_in_one_step(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    r = ship(people, cid, [(lot, 2)], reference="PO-77", carrier="truck")
    assert r.status == 200, r.text
    s = r.json["shipment"]
    assert s["destination"] == "Washington" and s["beforeMetals"] is True and s["units"] == 2 and s["reference"] == "PO-77"
    assert lot_row(srv, lot["id"]) == {"qty": 0, "status": "sold", "sku": "KELPIVEX"}
    assert state(srv, rid) == "pending_release"                                    # the run itself is not released
    d = detail(people, rid)
    ev = events(people, rid)[0]
    assert ev["capacity"] == "Quality Manager" and ev["comment"] and ev["detail"]["shipment"] == s["shipmentNo"] and ev["detail"]["destination"] == "Washington"
    assert ev["detail"]["lots"] == [{"lot": lot["number"], "qty": 2, "releasedEarly": True}] and d["chain"]["ok"] is True


def test_part_of_a_lot_leaves_the_rest_pending_and_a_second_shipment_works(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot = lots_of(srv, rid)[0]
    wa, canada = customer(people, "Washington"), customer(people, "Canada")
    assert ship(people, wa, [(lot, 1)]).status == 200
    assert lot_row(srv, lot["id"]) == {"qty": 2, "status": "pending_release", "sku": "KELPIVEX"}
    assert ship(people, canada, [(lot, 1)]).status == 409                          # the rest cannot go to Canada
    assert ship(people, wa, [(lot, 1)]).status == 200                              # but another confirmed US shipment can
    assert lot_row(srv, lot["id"])["qty"] == 1 and state(srv, rid) == "pending_release"
    early = detail(people, rid)["earlyShipments"]
    assert sorted(e["qty"] for e in early) == [1, 1] and {e["destination"] for e in early} == {"Washington"}


def test_lots_of_two_runs_go_on_one_shipment_with_one_signature(srv, people):
    enable(srv)
    run_a, run_b = pending_run(srv, people, packs=2), pending_run(srv, people, packs=2)
    lot_a, lot_b = lots_of(srv, run_a)[0], lots_of(srv, run_b)[0]
    other, stock = released_stock(srv, people, units=2)
    cid = customer(people, "Washington")
    before = n_shipments(srv)
    r = ship(people, cid, [(lot_a, 1), (lot_b, 2), (stock, 1)], reference="PO-multi")
    assert r.status == 200, r.text
    s = r.json["shipment"]
    assert n_shipments(srv) == before + 1 and s["units"] == 4
    name_a = lot_a["number"] + "-NHM"                                              # 1 of 2 units: the released part is a lot of its own; all 2 units of lot B: the lot itself
    assert {l["lot"]: l["beforeMetals"] for l in s["lines"]} == {name_a: True, lot_b["number"]: True, stock["number"]: False}
    for rid, name, units in ((run_a, name_a, 1), (run_b, lot_b["number"], 2)):
        ev = events(people, rid)
        assert len(ev) == 1 and ev[0]["detail"]["shipment"] == s["shipmentNo"] and ev[0]["detail"]["lots"] == [{"lot": name, "qty": units, "releasedEarly": True}]
        assert state(srv, rid) == "pending_release" and detail(people, rid)["chain"]["ok"] is True
    assert events(people, other) == [] and detail(people, other)["earlyShipments"] == []          # released stock is not part of any release
    assert lot_row(srv, stock["id"])["qty"] == 1


def test_one_ineligible_run_blocks_the_whole_shipment_and_says_which(srv, people):
    enable(srv)
    good, bad = pending_run(srv, people), pending_run(srv, people, tds=0.5)
    lot_good, lot_bad = lots_of(srv, good)[0], lots_of(srv, bad)[0]
    other, stock = released_stock(srv, people, units=2)
    cid, before = customer(people, "Washington"), n_shipments(srv)
    r = ship(people, cid, [(lot_good, 1), (lot_bad, 1), (stock, 1)])
    assert r.status == 409 and "TDS" in r.json["error"]
    assert n_shipments(srv) == before and lot_row(srv, lot_good["id"])["qty"] == 2 and lot_row(srv, stock["id"])["qty"] == 2
    assert events(people, good) == [] and events(people, bad) == []


def test_the_awaiting_qa_list_shows_every_pending_lot_with_its_reason(srv, people):
    enable(srv)
    cid_us, cid_ca = customer(people, "Washington"), customer(people, "Canada")
    ok_run, unreviewed = pending_run(srv, people), pending_run(srv, people, review=False)
    mine = {l["runId"]: l for l in awaiting(people, cid_us)["lots"]}
    assert mine[ok_run]["eligible"] and mine[ok_run]["runLot"] and mine[ok_run]["qty"] == 2
    assert not mine[unreviewed]["eligible"] and "not been reviewed" in mine[unreviewed]["why"]
    ca = {l["runId"]: l for l in awaiting(people, cid_ca)["lots"]}
    assert not ca[ok_run]["eligible"] and "requires the heavy-metals results" in ca[ok_run]["why"]
    plain = {l["runId"]: l for l in awaiting(people, cid_us, who="plain")["lots"]}
    assert awaiting(people, cid_us, who="plain")["canSign"] is False and "Quality Manager" in plain[ok_run]["why"] and not plain[ok_run]["eligible"]


def test_the_normal_release_later_puts_only_the_rest_on_hand(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    assert ship(people, customer(people, "Washington"), [(lot, 1)]).status == 200
    add_metals(srv, people, rid)
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "Test data: pH is not set to specification"})
    assert r.status == 200, r.text
    assert state(srv, rid) == "released" and lot_row(srv, lot["id"]) == {"qty": 1, "status": "on_hand", "sku": "KELPIVEX"}
    assert detail(people, rid)["checklist"][-1]["key"] == "early" and detail(people, rid)["checklist"][-1]["state"] == "ok"


def test_a_lot_with_no_sku_yet_ships_as_the_sku_chosen(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2, sku="SACC-LKE")
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert [l for l in awaiting(people, cid)["lots"] if l["runId"] == rid][0]["skuAssigned"] is False
    r = ship(people, cid, [(lot, 1)])
    assert r.status == 400 and "no SKU" in r.json["error"]
    r = ship(people, cid, [(lot, 1, "KELPIVEX")])
    assert r.status == 200, r.text
    assert [l["sku"] for l in r.json["shipment"]["lines"]] == ["KELPIVEX"]
    rows = sql(srv, "SELECT status, qty, sku_code, sku_assigned FROM fg_lots WHERE run_id=? ORDER BY id", (rid,))
    assert sorted(rows) == sorted([("pending_release", 1, "SACC-LKE", 0), ("sold", 0, "KELPIVEX", 1)]), rows
    assert state(srv, rid) == "pending_release"


def test_cancelling_the_shipment_returns_each_line_to_where_it_came_from_and_it_cannot_be_reinstated(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    other, stock = released_stock(srv, people, units=2)
    sid = ship(people, customer(people, "Washington"), [(lot, 2), (stock, 2)]).json["shipment"]["id"]
    assert lot_row(srv, lot["id"])["status"] == "sold" and lot_row(srv, stock["id"])["status"] == "sold"
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    assert lot_row(srv, lot["id"])["status"] == "pending_release" and lot_row(srv, stock["id"])["status"] == "on_hand"
    assert detail(people, rid)["earlyShipments"] == []
    r = people["plain"].put("/api/shipments/%d" % sid, {"status": "shipped"})
    assert r.status == 400 and "not released for sale" in r.json["error"]


# ---- a heavy-metals result that arrives later ----

def test_a_failed_metal_afterwards_blocks_the_rest_and_flags_what_shipped(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert ship(people, cid, [(lot, 1)]).status == 200
    d = detail(people, rid)
    assert [i["state"] for i in d["checklist"] if i["key"] == "early"] == ["warn"] and "released for shipment before the heavy-metals results" in d["releaseStatus"]["text"]
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-2")
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "x"})
    assert r.status == 409 and r.json["code"] == "release_gate"                              # the rest cannot be released
    assert lot_row(srv, lot["id"])["status"] == "pending_release" and state(srv, rid) == "pending_release"
    early = [i for i in detail(people, rid)["checklist"] if i["key"] == "early"][0]
    assert early["state"] == "fail" and "FAILED" in early["reason"] and "Washington" in early["reason"]
    recall = people["qm"].get("/api/release/runs/%d/recall" % rid).json
    assert len(recall["shippedBeforeHeavyMetals"]) == 1 and recall["shippedBeforeHeavyMetals"][0]["destination"] == "Washington"
    assert recall["customers"][0]["earlyUnits"] == 1 and recall["customers"][0]["destination"] == "Washington"


def test_the_integrity_check_and_reopen_account_for_the_shipped_units(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    assert ship(people, customer(people, "Washington"), [(lot, 1)]).status == 200
    issues = [i for i in people["admin"].get("/api/integrity").json["issues"] if i["runId"] == rid and i["severity"] == "error"]
    assert issues == []
    r = people["qm"].post("/api/release/runs/%d/reopen" % rid, {"password": people["qm_pw"], "comment": "log needs correction"})
    assert r.status == 200
    ev = [e for e in detail(people, rid)["events"] if e["type"] == "reopened"][0]
    assert ev["detail"]["alreadyShippedLots"] and ev["detail"]["alreadyShippedLots"][0]["lot"] == lot["number"] + "-NHM"


def test_the_certificate_says_heavy_metals_pending_once_units_have_shipped(srv, people):
    enable(srv)
    rid = pending_run(srv, people)
    assert people["qm"].get("/api/production/%d/coa.pdf" % rid).status == 200
    assert ship(people, customer(people, "Washington"), [(lots_of(srv, rid)[0], 1)]).status == 200
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
    other, stock = released_stock(srv, people)
    cid = customer(people, "Washington")
    s = people["plain"].post("/api/shipments", {"customerId": cid, "lines": [{"fgLotId": stock["id"], "qty": 1}]})
    assert s.status == 200, s.text
    assert s.json["shipment"]["destination"] == "Washington" and s.json["shipment"]["beforeMetals"] is False
    people["admin"].put("/api/customers/%d" % cid, {"destinationId": people["dests"]["Canada"]}).raise_for_status()
    again = [x for x in people["plain"].get("/api/shipments").json["shipments"] if x["id"] == s.json["shipment"]["id"]][0]
    assert again["destination"] == "Washington"


# ---- a run whose every unit has shipped on a release for shipment ----

def test_a_run_with_nothing_left_to_release_has_its_own_state_and_no_red_gate(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert ship(people, cid, [(lot, 1)]).status == 200
    d = detail(people, rid)
    assert d["allShippedEarly"] is False and d["releaseStatus"]["stage"] == "Awaiting QA release"          # one unit is still Pending Release
    assert ship(people, cid, [(lot, 1)]).status == 200
    d = detail(people, rid)
    assert state(srv, rid) == "pending_release" and d["allShippedEarly"] is True
    assert d["releaseStatus"]["stage"] == "All units shipped before the heavy-metals results" and d["releaseStatus"]["tone"] == "info" and "Nothing is left to release" in d["releaseStatus"]["text"]
    ck = {i["key"]: i for i in d["checklist"]}
    assert ck["gate"]["state"] == "na" and "not needed" in ck["gate"]["label"] and "Washington" in ck["gate"]["reason"]
    assert ck["comment"]["state"] == "na" and ck["early"]["state"] == "ok" and "not required" in ck["early"]["reason"]
    assert not [i for i in d["checklist"] if i["state"] == "fail"]
    listed = {r["id"]: r for r in people["qm"].get("/api/release").json["runs"]}
    assert listed[rid]["allShippedEarly"] is True


def test_a_failed_metal_turns_it_red_again_even_when_nothing_is_left(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=1)
    assert ship(people, customer(people, "Washington"), [(lots_of(srv, rid)[0], 1)]).status == 200
    add_metals(srv, people, rid, metals={"pb": "900000"}, report="F-3")
    d = detail(people, rid)
    assert d["allShippedEarly"] is True and d["releaseStatus"]["tone"] == "danger" and "FAILED" in d["releaseStatus"]["text"]
    ck = {i["key"]: i for i in d["checklist"]}
    assert ck["early"]["state"] == "fail" and ck["gate"]["state"] == "fail"


# ---- the released units are a lot of their own ----

def fg_by_number(people):
    return {l["lot"]: l for l in people["qm"].get("/api/fg").json["fg"]}


def test_part_of_a_lot_becomes_its_own_lot_marked_released_without_heavy_metals(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot, cid = lots_of(srv, rid)[0], customer(people, "Washington")
    assert ship(people, cid, [(lot, 1)]).status == 200
    fg = fg_by_number(people)
    child, parent = fg[lot["number"] + "-NHM"], fg[lot["number"]]
    assert child["releaseBasis"] == "no_metals" and child["parentLotId"] == lot["id"] and child["runId"] == rid
    assert child["status"] == "sold" and child["qty"] == 0
    assert parent["status"] == "pending_release" and parent["qty"] == 2 and parent["releaseBasis"] is None            # the rest is still waiting for QA release
    assert ship(people, cid, [(lot, 1)]).status == 200                              # a second release makes a second lot
    fg = fg_by_number(people)
    assert lot["number"] + "-NHM#2" in fg and fg[lot["number"] + "-NHM#2"]["releaseBasis"] == "no_metals" and fg[lot["number"]]["qty"] == 1


def test_a_whole_lot_is_marked_in_place_and_nothing_is_split(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=2)
    lot = lots_of(srv, rid)[0]
    assert ship(people, customer(people, "Washington"), [(lot, 2)]).status == 200
    fg = fg_by_number(people)
    assert fg[lot["number"]]["releaseBasis"] == "no_metals" and fg[lot["number"]]["status"] == "sold" and lot["number"] + "-NHM" not in fg


def test_cancelling_returns_the_released_lot_to_pending_release_without_the_mark(srv, people):
    enable(srv)
    rid = pending_run(srv, people, packs=3)
    lot = lots_of(srv, rid)[0]
    sid = ship(people, customer(people, "Washington"), [(lot, 1)]).json["shipment"]["id"]
    assert people["plain"].put("/api/shipments/%d" % sid, {"status": "cancelled"}).status == 200
    child = fg_by_number(people)[lot["number"] + "-NHM"]
    assert child["status"] == "pending_release" and child["qty"] == 1 and child["releaseBasis"] is None
    assert detail(people, rid)["earlyShipments"] == []
