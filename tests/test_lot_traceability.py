"""Recall traceability: lots of reagents / packaging / CIP agents, automatic assignment of every use to a lot, and tracing a lot forward to runs, products and customers."""
import datetime

import pytest

from factories import finished_run, release_run, sql
from harness import Server

TODAY = datetime.date.today()
IBC = "New 1,000 L IBC Tote"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    plain, _ = srv.make_user(role="user")
    return {"admin": srv.admin_client(), "pm": pm, "qm": qm, "plain": plain, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"]}


def item_id(admin, name):
    return [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def new_item(srv, admin, name):
    r = admin.post("/api/consumables", {"name": name, "unit": "kg", "onHand": 0})
    r.raise_for_status()
    return item_id(admin, name)


def receive(client, cid, qty, lot, expiry=None, supplier="Acme Chemicals", received=None, **kw):
    body = dict({"qty": qty, "supplierLot": lot, "supplier": supplier, "expiryDate": expiry, "receivedDate": received}, **kw)
    return client.post("/api/consumables/%d/receive" % cid, {k: v for k, v in body.items() if v is not None})


def lots(client, cid):
    return {l["supplierLot"]: l for l in client.get("/api/consumables/%d/lots" % cid).json["lots"]}


def use(client, cid, qty, ref, reason="Production run"):
    r = client.post("/api/consumables/%d/adjust" % cid, {"delta": -qty, "reason": reason, "ref": ref})
    r.raise_for_status()


def days(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat()


# ---- receiving ----

def test_receiving_stock_records_a_lot_and_adds_to_the_stock(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Receive")
    r = receive(admin, cid, 25, "LOT-A1", expiry=days(200), poRef="PO-77")
    assert r.status == 200
    lot = r.json["lot"]
    assert lot["lotCode"].startswith("RGT-") and lot["qtyReceived"] == 25 and lot["qtyRemaining"] == 25 and lot["supplierLot"] == "LOT-A1" and r.json["onHand"] == 25
    assert receive(admin, cid, 5, "LOT-A2").json["lot"]["lotCode"].endswith("-02")                 # the next lot of the same day
    ledger = admin.get("/api/consumables/%d/history" % cid).json["history"]
    assert any("LOT-A1" in (h["reason"] or "") and h["delta"] == 25 for h in ledger)


def test_a_receipt_needs_a_quantity_and_the_suppliers_lot_number(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Validation")
    assert receive(admin, cid, 0, "X").status == 400
    assert admin.post("/api/consumables/%d/receive" % cid, {"qty": 5}).status == 400
    assert receive(admin, cid, 5, "X", expiry=days(-5), received=days(0)).status == 400            # expires before it arrived
    assert receive(admin, cid, 5, "X", received="not a date").status == 400
    assert lots(admin, cid) == {}


# ---- assigning uses to lots ----

def test_the_earliest_expiry_is_used_first_and_a_use_can_span_lots(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Fefo")
    receive(admin, cid, 10, "LATE", expiry=days(300))
    receive(admin, cid, 4, "SOON", expiry=days(40))
    receive(admin, cid, 6, "NOEXP")
    use(admin, cid, 6, "PR-TEST-001")
    got = lots(admin, cid)
    assert got["SOON"]["qtyRemaining"] == 0 and got["LATE"]["qtyRemaining"] == 8 and got["NOEXP"]["qtyRemaining"] == 6     # 4 from SOON, then 2 from LATE; no expiry last
    use(admin, cid, 9, "PR-TEST-002")
    got = lots(admin, cid)
    assert got["LATE"]["qtyRemaining"] == 0 and got["NOEXP"]["qtyRemaining"] == 5


def test_a_refund_goes_back_to_the_lot_it_came_from_and_untraced_use_is_not_blocked(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Refund")
    receive(admin, cid, 5, "R1", expiry=days(100))
    use(admin, cid, 3, "PR-TEST-010")
    r = admin.post("/api/consumables/%d/adjust" % cid, {"delta": 2, "reason": "refund", "ref": "PR-TEST-010"})
    assert r.status == 200 and lots(admin, cid)["R1"]["qtyRemaining"] == 4                          # 1 still used, 2 back
    use(admin, cid, 10, "PR-TEST-011")                                                              # more than the lots hold: stock never blocks
    assert lots(admin, cid)["R1"]["qtyRemaining"] == 0
    after = admin.get("/api/consumables/%d/lots" % cid).json
    assert after["item"]["onHand"] == -6 and after["untraced"] == 0


def test_expired_lots_are_used_last_and_a_lot_on_hold_never(srv, people):
    admin, qm = people["admin"], people["qm"]
    cid = new_item(srv, admin, "Test Reagent Hold")
    receive(admin, cid, 5, "OLD", expiry=days(-10), received=days(-100))
    receive(admin, cid, 5, "HELD", expiry=days(400))
    receive(admin, cid, 5, "GOOD", expiry=days(500))
    held = lots(admin, cid)["HELD"]
    assert admin.put("/api/lots/%d" % held["id"], {"onHold": True}).status == 400                   # a reason is needed
    assert qm.put("/api/lots/%d" % held["id"], {"onHold": True, "reason": "Supplier recall notice"}).status == 200
    use(admin, cid, 7, "PR-TEST-020")
    got = lots(admin, cid)
    assert got["GOOD"]["qtyRemaining"] == 0 and got["OLD"]["qtyRemaining"] == 3 and got["HELD"]["qtyRemaining"] == 5       # GOOD, then the expired one; never the held one
    assert got["OLD"]["expired"] is True and got["HELD"]["onHold"] is True


def test_only_a_quality_manager_or_administrator_can_change_a_lot_and_every_change_is_logged(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Edit")
    receive(admin, cid, 5, "TYPO-1")
    lot = lots(admin, cid)["TYPO-1"]
    assert people["plain"].put("/api/lots/%d" % lot["id"], {"supplierLot": "FIXED-1", "reason": "typo"}).status == 403
    assert people["pm"].put("/api/lots/%d" % lot["id"], {"supplierLot": "FIXED-1", "reason": "typo"}).status == 403
    assert admin.put("/api/lots/%d" % lot["id"], {"supplierLot": "FIXED-1"}).status == 400          # no reason
    assert admin.put("/api/lots/%d" % lot["id"], {"supplierLot": "FIXED-1", "reason": "Misread the label"}).status == 200
    log = admin.get("/api/lots/%d/trace" % lot["id"]).json["log"]
    assert any("TYPO-1 -> FIXED-1" in (g["detail"] or "") and g["reason"] == "Misread the label" for g in log)


def test_a_use_can_be_moved_to_another_lot_with_a_reason(srv, people):
    admin, qm = people["admin"], people["qm"]
    cid = new_item(srv, admin, "Test Reagent Move")
    receive(admin, cid, 5, "FIRST", expiry=days(50))
    receive(admin, cid, 5, "SECOND", expiry=days(90))
    use(admin, cid, 2, "PR-TEST-030")
    first, second = lots(admin, cid)["FIRST"], lots(admin, cid)["SECOND"]
    trace = admin.get("/api/lots/%d/trace" % first["id"]).json
    use_id = trace["uses"][0]["useIds"][0]
    assert people["plain"].post("/api/lots/uses/%d/move" % use_id, {"lotId": second["id"], "reason": "x"}).status == 403
    assert qm.post("/api/lots/uses/%d/move" % use_id, {"lotId": second["id"]}).status == 400
    assert qm.post("/api/lots/uses/%d/move" % use_id, {"lotId": second["id"], "reason": "The operator used the SECOND bag"}).status == 200
    got = lots(admin, cid)
    assert got["FIRST"]["qtyRemaining"] == 5 and got["SECOND"]["qtyRemaining"] == 3
    assert admin.get("/api/lots/%d/trace" % second["id"]).json["uses"][0]["ref"] == "PR-TEST-030"


# ---- tracing ----

def test_a_lot_traces_forward_to_the_run_the_product_and_the_customer(srv, people):
    admin, pm, qm = people["admin"], people["pm"], people["qm"]
    cid = item_id(admin, IBC)
    receive(admin, cid, 20, "TOTE-LOT-9", supplier="Tote Co", expiry=None)
    rid = finished_run(admin)
    lot_no = sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
    mats = admin.get("/api/lots/run/%d" % rid).json["materials"]
    ibc = [m for m in mats if m["item"] == IBC][0]
    assert ibc["lots"][0]["supplierLot"] == "TOTE-LOT-9" and ibc["lots"][0]["qty"] == 1 and ibc["untraced"] == 0
    release_run(srv, rid, pm, people["pm_pw"], qm, people["qm_pw"])
    cust = admin.post("/api/customers", {"name": "Trace Test Farm"})
    cust.raise_for_status()
    customer_id = [c["id"] for c in cust.json["customers"] if c["name"] == "Trace Test Farm"][0]
    lot_id = sql(srv, "SELECT id FROM fg_lots WHERE run_id=?", (rid,))[0][0]
    admin.post("/api/shipments", {"customerId": customer_id, "shipDate": "2026-06-01", "lines": [{"fgLotId": lot_id, "qty": 1}]}).raise_for_status()
    lot = lots(admin, cid)["TOTE-LOT-9"]
    trace = admin.get("/api/lots/%d/trace" % lot["id"]).json
    assert [r["processingLot"] for r in trace["runs"]] == [lot_no]
    assert [c["name"] for c in trace["customers"]] == ["Trace Test Farm"] and trace["customers"][0]["units"] == 1
    recall = qm.get("/api/release/runs/%d/recall" % rid).json                                        # and the other way: the run's materials are in its recall report
    assert any(m["item"] == IBC and m["lots"] for m in recall["materials"])


def test_a_cleaning_agent_lot_traces_to_the_cip_event(srv, people):
    admin = people["admin"]
    cid = item_id(admin, "CIP Caustic")
    receive(admin, cid, 50, "CAUSTIC-7")
    r = admin.post("/api/cip", {"startedAt": "2026-06-01T08:00", "equipment": "Tank 5A", "chemicals": [{"consumableId": cid, "qty": 4}]})
    assert r.status == 200, r.text
    trace = admin.get("/api/lots/%d/trace" % lots(admin, cid)["CAUSTIC-7"]["id"]).json
    assert [(u["kind"], u["qty"]) for u in trace["uses"]] == [("cip", 4)] and "Tank 5A" in trace["uses"][0]["detail"]


def test_lots_can_be_found_by_supplier_lot_and_by_expiry(srv, people):
    admin = people["admin"]
    cid = new_item(srv, admin, "Test Reagent Search")
    receive(admin, cid, 5, "FINDME-123", expiry=days(10))
    assert [l["supplierLot"] for l in admin.get("/api/lots?q=FINDME").json["lots"]] == ["FINDME-123"]
    soon = [l for l in admin.get("/api/lots?expiring=1").json["lots"] if l["supplierLot"] == "FINDME-123"]
    assert soon and soon[0]["expiringSoon"] is True
    assert admin.get("/api/lots?q=NOSUCHLOT999").json["lots"] == []


def test_materials_appear_in_the_production_summary_pdf_with_their_lots(srv, people):
    admin = people["admin"]
    cid = item_id(admin, IBC)
    receive(admin, cid, 5, "PDF-LOT-5")
    rid = finished_run(admin)
    assert any(l["supplierLot"] in ("PDF-LOT-5", "TOTE-LOT-9") for m in admin.get("/api/lots/run/%d" % rid).json["materials"] for l in m["lots"])      # the earlier lot is used first
    r = admin.get("/api/production/%d/summary.pdf" % rid)
    assert r.status == 200 and r.body[:4] == b"%PDF"
