"""Release integrity (risk review batch 1): who may change finished goods and lab results, a tote belongs to one run, shipments cannot oversell.

Each test below failed on the code reviewed in docs/architecture-risk-review.md (findings R-01, R-02, R-03, R-06, R-14).
"""
import threading

import pytest

from factories import finalize_run, finished_run, harvest_totes, lots_of, release_run, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    """A plain user, a Production Manager, a Quality Manager and an amender, plus the passwords the sign-offs need."""
    plain, plain_info = srv.make_user(role="user")
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    am, am_info = srv.make_user(role="user", canAmendLog=True)
    return {"plain": plain, "pm": pm, "qm": qm, "amender": am, "admin": srv.admin_client(),
            "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "am_pw": am_info["password"]}


def released(srv, people):
    """A finalized AND released run: returns (run id, its lots)."""
    rid = finished_run(people["admin"])
    lots = release_run(srv, rid, people["pm"], people["pm_pw"], people["qm"], people["qm_pw"])
    assert lots and all(l["status"] == "on_hand" for l in lots), lots
    return rid, lots


def lot_row(srv, lot_id):
    return sql(srv, "SELECT status, qty, tds, location FROM fg_lots WHERE id=?", (lot_id,))[0]


def audit_events(srv, run_id, event_type):
    return sql(srv, "SELECT user_name, capacity, comment, detail FROM release_events WHERE run_id=? AND event_type=?", (run_id, event_type))


# ---- R-01: finished-goods lots ----

def test_plain_user_cannot_change_status_units_or_tds(srv, people):
    rid, lots = released(srv, people)
    lot = lots[0]
    before = lot_row(srv, lot["id"])
    for body in ({"status": "hold", "reason": "x y z"}, {"qty": 999, "reason": "x y z"}, {"tds": 9, "reason": "x y z"}):
        assert people["plain"].put("/api/fg/%d" % lot["id"], body).status == 403, body
    assert lot_row(srv, lot["id"]) == before


def test_unknown_status_and_bad_quantity_are_rejected(srv, people):
    rid, lots = released(srv, people)
    lid = lots[0]["id"]
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "banana", "reason": "test"}).status == 400
    assert people["qm"].put("/api/fg/%d" % lid, {"qty": -7, "reason": "test"}).status == 400
    assert people["qm"].put("/api/fg/%d" % lid, {"qty": "abc", "reason": "test"}).status == 400


def test_a_change_needs_a_reason_and_is_audited(srv, people):
    rid, lots = released(srv, people)
    lid = lots[0]["id"]
    assert people["pm"].put("/api/fg/%d" % lid, {"qty": lots[0]["qty"] - 1}).status == 400          # no reason
    r = people["pm"].put("/api/fg/%d" % lid, {"qty": lots[0]["qty"] - 1, "reason": "Counted one drum short"})
    assert r.status == 200, r.text
    ev = audit_events(srv, rid, "fg_lot_edited")
    assert len(ev) == 1 and "Counted one drum short" in ev[0][2] and ev[0][1] == "Production Manager"
    assert people["admin"].get("/api/release/verify").json["ok"] is True                              # the chain still verifies


def test_hold_is_for_quality_managers_only(srv, people):
    rid, lots = released(srv, people)
    lid = lots[0]["id"]
    assert people["pm"].put("/api/fg/%d" % lid, {"status": "hold", "reason": "suspect lot"}).status == 403
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "hold", "reason": "suspect lot"}).status == 200
    assert lot_row(srv, lid)[0] == "hold"
    assert people["pm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "all clear"}).status == 403      # lifting a hold too
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "all clear"}).status == 200


def test_pending_release_and_disposed_lots_cannot_be_changed_by_hand(srv, people):
    rid = finished_run(people["admin"])                           # finalized, not released: its lots are pending release
    lot = lots_of(srv, rid)[0]
    assert lot["status"] == "pending_release"
    assert people["qm"].put("/api/fg/%d" % lot["id"], {"status": "on_hand", "reason": "force it"}).status == 400
    rid2, lots2 = released(srv, people)
    sql(srv, "UPDATE fg_lots SET status='disposed', qty=0 WHERE id=?", (lots2[0]["id"],))
    assert people["qm"].put("/api/fg/%d" % lots2[0]["id"], {"status": "on_hand", "qty": 5, "reason": "revive"}).status == 400


def test_anyone_can_move_a_lot_and_the_move_is_logged(srv, people):
    rid, lots = released(srv, people)
    r = people["plain"].put("/api/fg/%d" % lots[0]["id"], {"location": "Warehouse B"})
    assert r.status == 200, r.text
    assert lot_row(srv, lots[0]["id"])[3] == "Warehouse B"
    assert sql(srv, "SELECT COUNT(*) FROM location_moves WHERE entity_type='fg' AND entity_id=?", (lots[0]["id"],))[0][0] >= 1


# ---- R-02: lab results ----

def test_only_a_quality_manager_can_enter_or_void_lab_results(srv, people):
    rid = finished_run(people["admin"])
    body = {"labName": "Some Lab", "reportNumber": "R-1", "results": [{"specCode": "salm", "value": "Negative"}]}
    for who in ("plain", "pm", "amender", "admin"):
        assert people[who].post("/api/production/%d/lab-results" % rid, body).status == 403, who
    added = people["qm"].post("/api/production/%d/lab-results" % rid, body)
    assert added.status == 200, added.text
    result_id = added.json["results"][0]["id"]
    assert people["plain"].post("/api/production/%d/lab-results/%d/void" % (rid, result_id), {"reason": "typo"}).status == 403
    assert people["qm"].post("/api/production/%d/lab-results/%d/void" % (rid, result_id), {"reason": "typo"}).status == 200
    assert people["plain"].get("/api/production/%d/lab-results" % rid).status == 200               # everyone can still READ them


def test_release_needs_the_required_results(srv, people):
    rid = finished_run(people["admin"])
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    r = people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"]})
    assert r.status == 409 and "required lab results" in r.text


def test_voiding_a_required_result_after_release_holds_the_lots(srv, people):
    rid, lots = released(srv, people)
    results = people["qm"].get("/api/production/%d/lab-results" % rid).json["results"]
    salmonella = [x for x in results if x["specCode"] == "salm"][0]
    assert people["qm"].post("/api/production/%d/lab-results/%d/void" % (rid, salmonella["id"]), {"reason": "wrong sample"}).status == 200
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}
    assert len(audit_events(srv, rid, "lab_result_hold")) == 1


def test_a_failing_result_after_release_holds_the_lots(srv, people):
    rid, lots = released(srv, people)
    r = people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "T-2",
                                                                    "results": [{"specCode": "salm", "value": "Positive"}]})
    assert r.status == 200, r.text
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}


# ---- R-03: a tote belongs to one run ----

def test_a_consumed_tote_cannot_go_into_a_second_run(srv, people):
    admin = people["admin"]
    tote = harvest_totes(admin, 1)[0]
    rid1, f1 = finalize_run(admin, [tote])
    assert f1.ok, f1.text
    assert sql(srv, "SELECT status FROM tote_lots WHERE id=?", (tote,))[0][0] == "consumed"
    body = {"sku": "KELPIVEX", "toteIds": [tote], "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.post("/api/production/drafts", body)
    assert r.status == 409, r.text
    assert sql(srv, "SELECT status FROM tote_lots WHERE id=?", (tote,))[0][0] == "consumed"
    accepted = sql(srv, "SELECT COUNT(DISTINCT run_id) FROM run_inputs WHERE tote_lot_id=? AND decision='accepted'", (tote,))[0][0]
    assert accepted == 1


def test_a_tote_locked_to_one_draft_cannot_be_taken_by_another(srv, people):
    admin = people["admin"]
    tote = harvest_totes(admin, 1)[0]
    rid1, _ = finalize_run(admin, [tote], finalize=False)             # the draft now holds the tote (wip)
    assert sql(srv, "SELECT status FROM tote_lots WHERE id=?", (tote,))[0][0] == "wip"
    body = {"sku": "KELPIVEX", "toteIds": [tote], "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.post("/api/production/drafts", body)
    assert r.status == 409, r.text
    assert sql(srv, "SELECT run_id FROM tote_lots WHERE id=?", (tote,))[0][0] == rid1


def test_tote_status_cannot_be_edited_into_a_second_use(srv, people):
    admin = people["admin"]
    tote = harvest_totes(admin, 1)[0]
    finalize_run(admin, [tote])                                        # consumed
    assert admin.put("/api/totes/%d" % tote, {"status": "in_stock"}).status == 400
    assert admin.put("/api/totes/%d" % tote, {"avgWeightKg": 5}).status == 400
    fresh = harvest_totes(admin, 1)[0]
    assert admin.put("/api/totes/%d" % fresh, {"status": "hold"}).status == 200                      # in stock <-> hold is allowed
    assert admin.put("/api/totes/%d" % fresh, {"status": "in_stock"}).status == 200
    assert admin.put("/api/totes/%d" % fresh, {"status": "consumed"}).status == 400


# ---- R-06: shipments ----

@pytest.fixture
def stock(srv, people):
    """A released lot with exactly 100 units on hand, and a customer."""
    rid, lots = released(srv, people)
    sql(srv, "UPDATE fg_lots SET qty=100, status='on_hand' WHERE id=?", (lots[0]["id"],))
    cust = people["admin"].post("/api/customers", {"name": "Customer %d" % rid}).json["customers"][-1]["id"]
    return lots[0]["id"], cust


def test_the_same_lot_on_two_lines_cannot_ship_more_than_is_on_hand(srv, people, stock):
    lid, cust = stock
    r = people["plain"].post("/api/shipments", {"customerId": cust, "lines": [{"fgLotId": lid, "qty": 60}, {"fgLotId": lid, "qty": 60}]})
    assert r.status == 400, r.text
    assert lot_row(srv, lid)[:2] == ("on_hand", 100.0)


def test_parallel_shipments_cannot_oversell(srv, people, stock):
    lid, cust = stock
    codes = []

    def ship(i):
        codes.append(people["plain"].post("/api/shipments", {"customerId": cust, "shipmentNo": "PAR-%d-%d" % (lid, i),
                                                              "lines": [{"fgLotId": lid, "qty": 60}]}).status)
    threads = [threading.Thread(target=ship, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(200) == 1, codes
    assert lot_row(srv, lid)[1] == 40.0


def test_a_cancelled_shipment_cannot_be_reinstated_without_the_stock(srv, people, stock):
    lid, cust = stock
    a = people["plain"].post("/api/shipments", {"customerId": cust, "shipmentNo": "RE-A-%d" % lid, "lines": [{"fgLotId": lid, "qty": 100}]}).json["shipment"]["id"]
    assert people["plain"].put("/api/shipments/%d" % a, {"status": "cancelled"}).status == 200
    assert people["plain"].post("/api/shipments", {"customerId": cust, "shipmentNo": "RE-B-%d" % lid, "lines": [{"fgLotId": lid, "qty": 100}]}).status == 200
    assert people["plain"].put("/api/shipments/%d" % a, {"status": "shipped"}).status == 400
    assert lot_row(srv, lid)[1] >= 0


def test_units_returned_to_a_disposed_lot_go_on_hold(srv, people, stock):
    lid, cust = stock
    s = people["plain"].post("/api/shipments", {"customerId": cust, "shipmentNo": "DISP-%d" % lid, "lines": [{"fgLotId": lid, "qty": 4}]}).json["shipment"]["id"]
    sql(srv, "UPDATE fg_lots SET status='disposed', qty=0 WHERE id=?", (lid,))                          # the rest of the lot was disposed
    assert people["plain"].put("/api/shipments/%d" % s, {"status": "cancelled"}).status == 200
    assert lot_row(srv, lid)[:2] == ("hold", 4.0)


# ---- R-14: a run under amendment ----

def test_only_an_amender_can_edit_a_run_under_amendment(srv, people):
    rid = finished_run(people["admin"])
    opened = people["amender"].post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "typo fix", "password": people["am_pw"]})
    assert opened.status == 200, opened.text
    assert people["plain"].put("/api/production/%d" % rid, {"notes": "changed by anyone"}).status == 403
    assert people["amender"].put("/api/production/%d" % rid, {"notes": "changed by the amender"}).status == 200
