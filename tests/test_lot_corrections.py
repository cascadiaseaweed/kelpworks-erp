"""KW-91: an administrator can correct a received lot's unit cost, line amount, pack and quantity (reason required, logged on the lot and its delivery). A quantity change moves the
item's stock by the difference through the ledger and the lot's own quantities, and can never take the lot below what was already used from it."""
import pytest

from factories import approve_suppliers, sql
from harness import Server

_n = [0]


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    return {"admin": srv.admin_client(), "qm": srv.make_user(role="user", isQualityManager=True)[0], "pm": srv.make_user(role="user", isProductionManager=True)[0],
            "plain": srv.make_user(role="user")[0]}


def new_item(admin, unit="kg"):
    _n[0] += 1
    name = "Correctable %d" % _n[0]
    admin.post("/api/consumables", {"name": name, "unit": unit, "onHand": 0, "manufacturer": "Test Maker", "grade": "Food"}).raise_for_status()
    cid = [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]
    approve_suppliers(admin, cid)
    return cid


def receive(admin, cid, qty=10, lot=None, **kw):
    _n[0] += 1
    body = dict({"qty": qty, "supplierLot": lot or "L-%d" % _n[0], "supplier": "Acme Chemicals", "invoiceNo": "INV-%d" % _n[0]}, **kw)
    r = admin.post("/api/consumables/%d/receive" % cid, body)
    r.raise_for_status()
    return r.json["lot"]


def correct(client, lot, **body):
    return client.post("/api/lots/%d/correct" % lot["id"], body)


def row(srv, lot_id):
    return sql(srv, "SELECT qty_received, qty_remaining, unit_price, line_total, received_as FROM consumable_lots WHERE id=?", (lot_id,))[0]


def on_hand(srv, cid):
    return sql(srv, "SELECT on_hand FROM consumables WHERE id=?", (cid,))[0][0]


def lot_log(srv, lot_id):
    return sql(srv, "SELECT event, detail, reason, user_name FROM consumable_lot_log WHERE lot_id=? AND event='receipt_corrected' ORDER BY id", (lot_id,))


def add_pack(admin, cid, name="Box, 25 kg", qty=25):
    r = admin.post("/api/consumables/%d/packs" % cid, {"name": name, "qtyPerPack": qty})
    r.raise_for_status()
    return [p for p in admin.get("/api/consumables/%d/packs" % cid).json["packs"] if p["name"] == name][0]


# ---- who and why ----
def test_only_an_administrator_or_quality_manager_corrects_a_received_lot_and_a_reason_is_required(srv, people):
    a = people["admin"]
    cid = new_item(a)
    lot = receive(a, cid, 10)
    for who in ("pm", "plain"):                                                                         # a Production Manager alone, or any other user, cannot
        assert correct(people[who], lot, qtyReceived=12, reason="x").status == 403
    assert correct(people["qm"], lot, qtyReceived=11, reason="Quality Manager check").status == 200
    assert sql(srv, "SELECT user_name FROM consumable_lot_log WHERE lot_id=? AND event='receipt_corrected'", (lot["id"],))[0][0] != "Plant Admin"          # attributed to the Quality Manager
    assert row(srv, lot["id"])[0] == 11 and on_hand(srv, cid) == 11
    assert correct(a, lot, qtyReceived=10, reason="Back to the delivery note").status == 200
    assert correct(a, lot, qtyReceived=12).status == 400                                               # no reason
    assert correct(a, lot, qtyReceived=12, reason="  ").status == 400
    assert row(srv, lot["id"])[0] == 10 and on_hand(srv, cid) == 10
    assert correct(a, lot, qtyReceived=12, reason="Count was 12").status == 200
    assert correct(a, lot, reason="").json["id"] == lot["id"]                                          # nothing to change: nothing needed


def test_a_correction_that_changes_nothing_logs_nothing(srv, people):
    a = people["admin"]
    cid = new_item(a)
    lot = receive(a, cid, 10, unitPrice=4.5)
    r = correct(a, lot, qtyReceived=10, unitPrice=4.5, packId=None, reason="same")
    assert r.status == 200 and lot_log(srv, lot["id"]) == []
    assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE consumable_id=? AND reason LIKE 'Receipt correction%'", (cid,))[0][0] == 0


# ---- quantity ----
def test_a_quantity_change_moves_the_lot_and_the_item_by_the_difference_with_one_ledger_line(srv, people):
    a = people["admin"]
    cid = new_item(a)
    first, other = receive(a, cid, 10), receive(a, cid, 20)
    assert on_hand(srv, cid) == 30
    r = correct(a, first, qtyReceived=14, reason="Delivery note says 14")
    assert r.status == 200 and r.json["qtyReceived"] == 14 and r.json["qtyRemaining"] == 14
    assert on_hand(srv, cid) == 34 and row(srv, other["id"])[:2] == (20, 20)                           # the other lot is untouched
    ledger = sql(srv, "SELECT delta, reason, ref, user_name FROM consumable_txns WHERE consumable_id=? AND reason LIKE 'Receipt correction%'", (cid,))
    assert ledger == [(4, "Receipt correction (lot %s): quantity 10 -> 14" % first["lotCode"], first["lotCode"], "Plant Admin")]
    r = correct(a, first, qtyReceived=9, reason="Two bags were returned")
    assert r.status == 200 and row(srv, first["id"])[:2] == (9, 9) and on_hand(srv, cid) == 29
    assert row(srv, other["id"])[:2] == (20, 20)                                                       # a decrease is taken from THIS lot, never from another
    assert sql(srv, "SELECT COUNT(*) FROM lot_uses WHERE consumable_id=?", (cid,))[0][0] == 0         # and it is not recorded as a use


def test_the_quantity_cannot_go_below_what_was_already_used_from_the_lot(srv, people):
    a = people["admin"]
    cid = new_item(a)
    lot = receive(a, cid, 10)
    assert a.post("/api/consumables/%d/adjust" % cid, {"delta": -6, "reason": "Production run", "ref": "PR-TEST-1"}).ok          # 6 used from the lot
    assert row(srv, lot["id"])[:2] == (10, 4)
    r = correct(a, lot, qtyReceived=5, reason="Wrong count")
    assert r.status == 409 and "6" in r.text and "already been used" in r.text
    assert row(srv, lot["id"])[:2] == (10, 4) and on_hand(srv, cid) == 4 and lot_log(srv, lot["id"]) == []                     # nothing changed
    assert correct(a, lot, qtyReceived=6, reason="Exactly what was used").status == 200
    assert row(srv, lot["id"])[:2] == (6, 0) and on_hand(srv, cid) == 0
    assert correct(a, lot, qtyReceived=20, reason="Count was higher").status == 200
    assert row(srv, lot["id"])[:2] == (20, 14)
    a.post("/api/consumables/%d/adjust" % cid, {"delta": -3, "reason": "Production run", "ref": "PR-TEST-2"}).raise_for_status()
    assert row(srv, lot["id"])[:2] == (20, 11)                                                          # a later use is allocated against the corrected quantity


def test_a_quantity_must_be_a_positive_number(srv, people):
    a = people["admin"]
    cid = new_item(a)
    lot = receive(a, cid, 10)
    for bad in (0, -3, "abc"):
        assert correct(a, lot, qtyReceived=bad, reason="x").status == 400
    assert row(srv, lot["id"])[0] == 10


def test_a_held_an_expired_and_a_fully_used_lot_can_still_be_corrected(srv, people):
    a = people["admin"]
    cid = new_item(a)
    held = receive(a, cid, 10)
    assert a.put("/api/lots/%d" % held["id"], {"onHold": True, "reason": "recall notice"}).ok
    assert correct(a, held, qtyReceived=11, reason="Count").status == 200
    old = receive(a, cid, 5, receivedDate="2026-01-10", expiryDate="2026-02-10")
    assert correct(a, old, unitPrice=2, reason="Invoice price").status == 200


# ---- unit cost and line amount ----
def test_unit_cost_is_stored_per_stock_unit_and_a_per_pack_cost_is_divided(srv, people):
    a = people["admin"]
    cid = new_item(a)
    pack = add_pack(a, cid)
    lot = receive(a, cid, 10)
    assert correct(a, lot, unitPrice=4.5, reason="From the invoice").status == 200
    assert row(srv, lot["id"])[2] == 4.5
    r = correct(a, lot, packId=pack["id"], packs=2, unitPrice=100, priceBasis="pack", lineTotal=200, reason="Invoice is per box")
    assert r.status == 200 and row(srv, lot["id"]) == (50, 50, 4.0, 200, "2 x Box, 25 kg")                      # 100 per 25 kg box = 4.0 per kg
    assert correct(a, lot, unitPrice=None, lineTotal=None, reason="Price was not on the invoice").status == 200
    assert row(srv, lot["id"])[2:4] == (None, None)
    for bad in ({"unitPrice": -1}, {"unitPrice": "abc"}, {"lineTotal": -5}):
        assert correct(a, lot, reason="x", **bad).status == 400
    assert correct(a, lot, unitPrice=10, priceBasis="pack", reason="x").status == 400                           # a per-pack cost needs the pack chosen
    assert sql(srv, "SELECT cost_per_unit FROM consumables WHERE id=?", (cid,))[0][0] is None                    # the item's own cost is never touched


# ---- pack ----
def test_choosing_a_pack_sets_the_quantity_and_removing_it_clears_the_pack(srv, people):
    a = people["admin"]
    cid = new_item(a)
    box = add_pack(a, cid, "Box, 25 kg", 25)
    lot = receive(a, cid, 40)
    assert correct(a, lot, packId=box["id"], reason="x").status == 400                                          # the number of packs is needed
    r = correct(a, lot, packId=box["id"], packs=3, reason="Received as boxes")
    assert r.status == 200 and r.json["receivedAs"] == "3 x Box, 25 kg" and r.json["qtyReceived"] == 75 and on_hand(srv, cid) == 75
    assert correct(a, lot, qtyReceived=70, reason="x").status == 400                                            # typing a quantity on a packed lot needs the pack choice
    r = correct(a, lot, packId=None, qtyReceived=70, reason="Loose bags")
    assert r.status == 200 and r.json["receivedAs"] is None and r.json["qtyReceived"] == 70 and on_hand(srv, cid) == 70
    other = new_item(a)
    foreign = add_pack(a, other, "Other pack", 5)
    assert correct(a, lot, packId=foreign["id"], packs=1, reason="x").status == 400                             # a pack of another item


# ---- the audit trail ----
def test_every_change_is_logged_on_the_lot_and_its_delivery_with_who_old_new_and_the_reason(srv, people):
    a = people["admin"]
    cid = new_item(a)
    lot = receive(a, cid, 10, unitPrice=4.5)
    assert correct(a, lot, qtyReceived=12, unitPrice=5.25, reason="Invoice re-read").status == 200
    log = lot_log(srv, lot["id"])
    assert len(log) == 2 and all(x[2] == "Invoice re-read" and x[3] == "Plant Admin" for x in log)
    details = {x[1] for x in log}
    assert any(d.startswith("Unit cost") and d.endswith("4.5 -> 5.25") for d in details) and any(d.startswith("Quantity received") and d.endswith("10 -> 12") for d in details)
    rid = sql(srv, "SELECT receipt_id FROM consumable_lots WHERE id=?", (lot["id"],))[0][0]
    rl = sql(srv, "SELECT field, old_value, new_value, reason FROM receipt_log WHERE receipt_id=? AND field LIKE ?", (rid, "Lot %s:%%" % lot["lotCode"]))
    assert ("Lot %s: Quantity received (kg)" % lot["lotCode"], "10", "12", "Invoice re-read") in rl
    trace = a.get("/api/lots/%d/trace" % lot["id"]).json
    assert [g["event"] for g in trace["log"]].count("receipt_corrected") == 2 and trace["lot"]["unitPrice"] == 5.25 and trace["lot"]["qtyReceived"] == 12
    assert [l for l in a.get("/api/lots").json["lots"] if l["id"] == lot["id"]][0]["unitPrice"] == 5.25


def test_a_lot_without_a_delivery_is_corrected_too_and_everything_survives_a_restart():
    with Server() as s:
        a = s.admin_client()
        cid = new_item(a)
        lot = receive(a, cid, 10)
        sql(s, "UPDATE consumable_lots SET receipt_id=NULL WHERE id=?", (lot["id"],))
        assert correct(a, lot, qtyReceived=11, unitPrice=3, reason="No delivery on this one").status == 200
        s.restart()
        assert row(s, lot["id"])[:3] == (11, 11, 3) and on_hand(s, cid) == 11
        assert len(lot_log(s, lot["id"])) == 2
