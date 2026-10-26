"""KW-23: a delivery's supplier is chosen from the item's approved suppliers (never typed); an administrator or Production Manager maintains the list."""
import pytest

from factories import sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    pm, _ = srv.make_user(role="user", isProductionManager=True)
    qm, _ = srv.make_user(role="user", isQualityManager=True)
    return {"admin": srv.admin_client(), "plain": plain, "pm": pm, "qm": qm}


def new_item(admin, name):
    r = admin.post("/api/consumables", {"name": name, "unit": "kg", "onHand": 0, "manufacturer": "Test Maker", "grade": "Food"})
    assert r.ok, r.text
    return [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def suppliers(client, cid):
    return [s["name"] for s in client.get("/api/consumables/%d/suppliers" % cid).json["suppliers"]]


def receive(client, cid, supplier, lot="L-1", **kw):
    return client.post("/api/consumables/%d/receive" % cid, dict({"qty": 5, "supplierLot": lot, "supplier": supplier}, **kw))


def test_only_an_admin_or_production_manager_maintains_the_list(srv, people):
    cid = new_item(people["admin"], "Sup Item 1")
    assert people["plain"].post("/api/consumables/%d/suppliers" % cid, {"name": "Acme"}).status == 403
    assert people["qm"].post("/api/consumables/%d/suppliers" % cid, {"name": "Acme"}).status == 403            # the Quality Manager flag grants nothing here
    assert people["pm"].post("/api/consumables/%d/suppliers" % cid, {"name": "  Acme   Chemicals "}).status == 200
    assert suppliers(people["plain"], cid) == ["Acme Chemicals"]                                               # everyone can read it; spacing is tidied
    assert people["admin"].post("/api/consumables/%d/suppliers" % cid, {"name": "acme chemicals"}).status == 409   # same supplier, any case
    assert people["admin"].post("/api/consumables/%d/suppliers" % cid, {"name": "  "}).status == 400
    assert people["admin"].post("/api/consumables/99999/suppliers", {"name": "X"}).status == 404
    sid = people["admin"].get("/api/consumables/%d/suppliers" % cid).json["suppliers"][0]["id"]
    assert people["plain"].delete("/api/consumables/%d/suppliers/%d" % (cid, sid)).status == 403
    assert people["pm"].delete("/api/consumables/%d/suppliers/%d" % (cid, sid)).status == 200
    assert suppliers(people["admin"], cid) == []
    log = sql(srv, "SELECT supplier, action, user_name FROM consumable_supplier_log WHERE consumable_id=? ORDER BY id", (cid,))
    assert [(r[0], r[1]) for r in log] == [("Acme Chemicals", "added"), ("Acme Chemicals", "removed")] and log[0][2]      # append-only: who and what


def test_the_list_comes_with_the_inventory_items(srv, people):
    cid = new_item(people["admin"], "Sup Item 2")
    people["admin"].post("/api/consumables/%d/suppliers" % cid, {"name": "Univar"}).raise_for_status()
    item = [c for c in people["plain"].get("/api/consumables").json["consumables"] if c["id"] == cid][0]
    assert [s["name"] for s in item["suppliers"]] == ["Univar"]


def test_receiving_takes_only_an_approved_supplier_spelled_as_stored(srv, people):
    a = people["admin"]
    cid = new_item(a, "Sup Item 3")
    r = receive(a, cid, "Univar")
    assert r.status == 400 and "not an approved supplier" in r.text                                          # nothing approved yet
    assert not sql(srv, "SELECT 1 FROM consumable_lots WHERE consumable_id=?", (cid,))                        # and nothing was recorded
    a.post("/api/consumables/%d/suppliers" % cid, {"name": "Univar Solutions"}).raise_for_status()
    assert receive(a, cid, "Somebody Else", lot="L-2").status == 400
    ok = receive(a, cid, "univar solutions", lot="L-3")
    assert ok.status == 200, ok.text
    assert ok.json["lot"]["supplier"] == "Univar Solutions"                                                  # stored as the list spells it
    assert receive(a, cid, "", lot="L-4").status == 200                                                      # a blank supplier is still allowed


def test_a_lot_and_a_delivery_can_only_be_corrected_to_an_approved_supplier(srv, people):
    a = people["admin"]
    cid = new_item(a, "Sup Item 4")
    a.post("/api/consumables/%d/suppliers" % cid, {"name": "Acme"}).raise_for_status()
    r = receive(a, cid, "Acme", invoiceNo="INV-4")
    lot, rid = r.json["lot"], r.json["receiptId"]
    qm = people["qm"]
    bad = qm.put("/api/lots/%d" % lot["id"], {"supplier": "Nobody", "reason": "typo"})
    assert bad.status == 400 and "approved supplier" in bad.text
    assert qm.put("/api/lots/%d" % lot["id"], {"supplier": "Acme", "notes": "same supplier is fine", "reason": "note"}).status == 200
    assert a.put("/api/receipts/%d" % rid, {"supplier": "Nobody", "reason": "typo"}).status == 400
    a.post("/api/consumables/%d/suppliers" % cid, {"name": "Acme West"}).raise_for_status()
    assert a.put("/api/receipts/%d" % rid, {"supplier": "Acme West", "reason": "supplier changed"}).status == 200
    # an older record whose supplier is no longer on the list can still be saved unchanged
    sid = [s["id"] for s in a.get("/api/consumables/%d/suppliers" % cid).json["suppliers"] if s["name"] == "Acme West"][0]
    a.delete("/api/consumables/%d/suppliers/%d" % (cid, sid)).raise_for_status()
    assert a.put("/api/receipts/%d" % rid, {"supplier": "Acme West", "notes": "no change of supplier"}).status == 200
