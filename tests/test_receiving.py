"""Receiving inventory: deliveries (invoice, PO, order, carrier, documents), several supplier lots and pack sizes in one receipt, stock units, and Adjust > Count."""
import base64
import datetime

import pytest

from factories import sql
from harness import Server

TODAY = datetime.date.today().isoformat()
PDF = base64.b64encode(b"%PDF-1.4 test invoice").decode()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    return {"admin": srv.admin_client(), "plain": plain}


def new_item(admin, name, unit="kg", **kw):
    r = admin.post("/api/consumables", dict({"name": name, "unit": unit, "onHand": 0}, **kw))
    assert r.ok, r.text
    return [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def item(admin, cid):
    return [c for c in admin.get("/api/consumables").json["consumables"] if c["id"] == cid][0]


# ---- a delivery -------------------------------------------------------------------------------------------------------------------

def test_a_receipt_records_the_delivery_details_on_the_lot(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Reagent A")
    r = a.post("/api/consumables/%d/receive" % cid, {"qty": 20, "supplierLot": "L-1", "supplier": "DuBois", "invoiceNo": "IN-100", "invoiceDate": "2026-08-31", "poNumber": "1092",
                                                    "orderNo": "SO-70121302", "deliveryDoc": "PRO 989", "carrier": "Manitoulin", "shipDate": "2026-08-19"})
    assert r.status == 200, r.text
    lot = r.json["lot"]
    assert (lot["invoiceNo"], lot["poNumber"], lot["orderNo"], lot["deliveryDoc"], lot["carrier"]) == ("IN-100", "1092", "SO-70121302", "PRO 989", "Manitoulin")
    rec = a.get("/api/receipts/%d" % r.json["receiptId"]).json
    assert rec["supplier"] == "DuBois" and rec["invoiceDate"] == "2026-08-31" and rec["shipDate"] == "2026-08-19" and len(rec["lots"]) == 1


def test_lots_of_the_same_supplier_and_invoice_share_one_delivery(srv, people):
    a = people["admin"]
    c1, c2 = new_item(a, "Recv Reagent B1"), new_item(a, "Recv Reagent B2")
    body = {"supplier": "Univar", "invoiceNo": "53959279", "poNumber": "P-9"}
    r1 = a.post("/api/consumables/%d/receive" % c1, dict(body, qty=5, supplierLot="U-1"))
    r2 = a.post("/api/consumables/%d/receive" % c2, dict(body, qty=7, supplierLot="U-2", orderNo="16317914"))        # the second line adds a detail the first did not have
    r3 = a.post("/api/consumables/%d/receive" % c2, {"qty": 1, "supplierLot": "U-3", "supplier": "Univar", "invoiceNo": "OTHER"})
    assert r1.json["receiptId"] == r2.json["receiptId"] != r3.json["receiptId"]
    rec = a.get("/api/receipts/%d" % r1.json["receiptId"]).json
    assert len(rec["lots"]) == 2 and rec["orderNo"] == "16317914"
    assert [g for g in rec["log"] if g["field"] == "order_no"]                                                          # the filled-in detail is in the delivery's log


def test_one_receipt_can_hold_several_supplier_lots_and_packs_convert_to_the_stock_unit(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Sorbate", "kg")
    r = a.post("/api/consumables/%d/receive" % cid, {"supplier": "Univar", "invoiceNo": "INV-PK", "lines": [
        {"newPack": {"name": "Box, 50 lb", "qtyPerPack": 22.68}, "packs": 45, "supplierLot": "26011608A", "unitPrice": 141.5, "priceBasis": "pack"},
        {"packId": None, "qty": 10, "supplierLot": "LOOSE-1", "unitPrice": 6.0}]})
    assert r.status == 200, r.text
    lots = {l["supplierLot"]: l for l in r.json["lots"]}
    assert abs(lots["26011608A"]["qtyReceived"] - 1020.6) < 1e-6 and lots["26011608A"]["receivedAs"] == "45 x Box, 50 lb"
    assert abs(lots["26011608A"]["unitPrice"] - 141.5 / 22.68) < 1e-5 and lots["LOOSE-1"]["unitPrice"] == 6.0
    assert abs(item(a, cid)["onHand"] - 1030.6) < 1e-6
    assert [p["name"] for p in item(a, cid)["packs"]] == ["Box, 50 lb"]
    assert lots["26011608A"]["receiptId"] == lots["LOOSE-1"]["receiptId"]


def test_a_bad_line_receives_nothing(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Atomic")
    r = a.post("/api/consumables/%d/receive" % cid, {"lines": [{"qty": 5, "supplierLot": "OK-1"}, {"qty": 3}]})                # the second line has no supplier lot
    assert r.status == 400 and item(a, cid)["onHand"] == 0 and not sql(srv, "SELECT 1 FROM consumable_lots WHERE consumable_id=?", (cid,))
    r = a.post("/api/consumables/%d/receive" % cid, {"lines": [{"qty": 5, "supplierLot": "OK-1", "expiryDate": "2000-01-01"}]})
    assert r.status == 400


def test_packaging_needs_no_supplier_lot_and_uses_the_invoice_number(srv, people):
    a = people["admin"]
    cid = [c["id"] for c in a.get("/api/consumables").json["consumables"] if c["name"] == "New 1,000 L IBC Tote"][0]
    r = a.post("/api/consumables/%d/receive" % cid, {"qty": 60, "supplier": "Island Barrels", "invoiceNo": "304"})
    assert r.status == 200 and r.json["lot"]["supplierLot"] == "INV 304"
    r = a.post("/api/consumables/%d/receive" % cid, {"qty": 1})
    assert r.status == 200 and r.json["lot"]["supplierLot"] == "(none)"
    reagent = new_item(a, "Recv Needs Lot")
    assert a.post("/api/consumables/%d/receive" % reagent, {"qty": 1, "invoiceNo": "X"}).status == 400


# ---- pack sizes -------------------------------------------------------------------------------------------------------------------

def test_anyone_can_add_a_pack_size_and_it_is_logged(srv, people):
    a, plain = people["admin"], people["plain"]
    cid = new_item(a, "Recv Packs", "L")
    r = plain.post("/api/consumables/%d/packs" % cid, {"name": "Drum, 208 L", "qtyPerPack": 208})
    assert r.status == 200 and r.json["pack"]["qtyPerPack"] == 208
    again = plain.post("/api/consumables/%d/packs" % cid, {"name": "drum, 208 l", "qtyPerPack": 208})                       # the same pack is not added twice
    assert again.json["pack"]["id"] == r.json["pack"]["id"]
    assert plain.post("/api/consumables/%d/packs" % cid, {"name": "Drum, 208 L", "qtyPerPack": 200}).status == 409
    assert plain.post("/api/consumables/%d/packs" % cid, {"name": "Bad", "qtyPerPack": 0}).status == 400
    assert plain.post("/api/consumables/%d/packs" % cid, {"name": "", "qtyPerPack": 5}).status == 400
    log = sql(srv, "SELECT event, user_name FROM consumable_pack_log WHERE consumable_id=?", (cid,))
    assert len(log) == 1 and log[0][0] == "added" and log[0][1]
    assert [p["name"] for p in plain.get("/api/consumables/%d/packs" % cid).json["packs"]] == ["Drum, 208 L"]
    rr = plain.post("/api/consumables/%d/receive" % cid, {"packs": 3, "packId": r.json["pack"]["id"], "supplierLot": "D-1"})
    assert rr.status == 200 and rr.json["lot"]["qtyReceived"] == 624


def test_a_pack_of_another_item_cannot_be_used(srv, people):
    a = people["admin"]
    c1, c2 = new_item(a, "Recv Own Pack 1", "L"), new_item(a, "Recv Own Pack 2", "L")
    pk = a.post("/api/consumables/%d/packs" % c1, {"name": "Pail", "qtyPerPack": 18.9}).json["pack"]["id"]
    assert a.post("/api/consumables/%d/receive" % c2, {"lines": [{"packId": pk, "packs": 1, "supplierLot": "X"}]}).status == 400


def test_a_new_item_must_use_a_known_stock_unit(srv, people):
    a = people["admin"]
    assert a.post("/api/consumables", {"name": "Recv Unit KG", "unit": "KG"}).ok
    assert [c["unit"] for c in a.get("/api/consumables").json["consumables"] if c["name"] == "Recv Unit KG"] == ["kg"]
    assert a.post("/api/consumables", {"name": "Recv Unit Bag", "unit": "bag"}).status == 400
    assert a.post("/api/consumables", {"name": "Recv Unit Blank", "unit": ""}).ok


# ---- documents --------------------------------------------------------------------------------------------------------------------

def test_an_invoice_can_be_attached_at_receipt_or_later_and_downloaded(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Docs")
    rid = a.post("/api/consumables/%d/receive" % cid, {"qty": 4, "supplierLot": "DOC-1", "invoiceNo": "I-1", "supplier": "S"}).json["receiptId"]
    r = a.post("/api/receipts/%d/attachments" % rid, {"filename": "invoice IN-1.pdf", "dataB64": PDF, "kind": "invoice"})
    assert r.status == 200 and r.json["attachments"][0]["filename"] == "invoice IN-1.pdf" and r.json["attachments"][0]["kind"] == "invoice"
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "bol.pdf", "dataB64": PDF, "kind": "delivery"}).raise_for_status()           # later, a second document
    rec = a.get("/api/receipts/%d" % rid).json
    assert [d["kind"] for d in rec["attachments"]] == ["invoice", "delivery"]
    assert rec["lots"][0]["docCount"] == 2
    aid = rec["attachments"][0]["id"]
    got = a.get("/api/receipts/%d/attachments/%d/download" % (rid, aid))
    assert got.status == 200 and got.body.startswith(b"%PDF")
    link = a.get("/api/receipts/%d/attachments/%d/download?token=%s" % (rid, aid, a.download_token()), token="")                         # the link the app opens
    assert link.status == 200 and link.body.startswith(b"%PDF")
    assert a.get("/api/receipts/%d/attachments/%d/download" % (rid, aid), token="").status == 401


def test_a_script_cannot_be_attached(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Docs Bad")
    rid = a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "B"}).json["receiptId"]
    for name in ("x.html", "x.svg", "x.exe"):
        assert a.post("/api/receipts/%d/attachments" % rid, {"filename": name, "dataB64": PDF}).status == 400
    assert a.post("/api/receipts/%d/attachments" % rid, {"filename": "x.pdf", "dataB64": ""}).status == 400


def test_an_older_lot_gets_a_delivery_when_a_document_is_attached(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Old Lot")
    lot = a.post("/api/consumables/%d/receive" % cid, {"qty": 2, "supplierLot": "OLD", "poRef": "PO-OLD", "supplier": "Acme"}).json["lot"]
    sql(srv, "UPDATE consumable_lots SET receipt_id=NULL WHERE id=?", (lot["id"],))                                                 # as a lot received before deliveries existed
    rec = a.post("/api/lots/%d/receipt" % lot["id"]).json
    assert rec["supplier"] == "Acme" and rec["poNumber"] == "PO-OLD" and [l["id"] for l in rec["lots"]] == [lot["id"]]
    assert a.post("/api/lots/%d/receipt" % lot["id"]).json["id"] == rec["id"]                                                         # the same delivery the second time


def test_delivery_details_can_be_filled_in_later_and_a_correction_needs_a_reason(srv, people):
    a, plain = people["admin"], people["plain"]
    cid = new_item(a, "Recv Late Invoice")
    rid = a.post("/api/consumables/%d/receive" % cid, {"qty": 2, "supplierLot": "LATE"}).json["receiptId"]
    r = plain.put("/api/receipts/%d" % rid, {"invoiceNo": "IN-LATE", "poNumber": "1083"})                                          # blank -> value: no reason needed
    assert r.status == 200 and r.json["invoiceNo"] == "IN-LATE"
    assert plain.put("/api/receipts/%d" % rid, {"invoiceNo": "IN-LATE-2"}).status == 400                                           # changing a value needs a reason
    ok = plain.put("/api/receipts/%d" % rid, {"invoiceNo": "IN-LATE-2", "reason": "typo"})
    assert ok.status == 200
    log = [(g["field"], g["oldValue"], g["newValue"], g["reason"]) for g in ok.json["log"] if g["field"] == "invoice_no"]
    assert ("invoice_no", "IN-LATE", "IN-LATE-2", "typo") in log and ("invoice_no", None, "IN-LATE", None) in log
    assert plain.put("/api/receipts/%d" % rid, {"arrivalCondition": "issue"}).status == 400                                        # an issue must be described
    assert plain.put("/api/receipts/%d" % rid, {"arrivalCondition": "issue", "conditionNote": "two drums dented"}).status == 200


def test_lots_are_found_by_invoice_po_or_order_number(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Search")
    a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "S-1", "invoiceNo": "ZZ-INV-77", "orderNo": "SO-ZZ-88", "poNumber": "PO-ZZ-99"}).raise_for_status()
    for q in ("ZZ-INV-77", "SO-ZZ-88", "PO-ZZ-99"):
        assert [l["supplierLot"] for l in a.get("/api/lots?q=" + q).json["lots"]] == ["S-1"]
    assert a.get("/api/receipts?q=ZZ-INV-77").json["receipts"][0]["lotCount"] == 1


# ---- Adjust > Count ---------------------------------------------------------------------------------------------------------------

def test_a_count_writes_the_difference_to_the_ledger_and_takes_a_shortage_from_the_lots(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Count")
    a.post("/api/consumables/%d/receive" % cid, {"qty": 10, "supplierLot": "C-1", "expiryDate": "2099-01-01"}).raise_for_status()
    r = a.post("/api/consumables/%d/count" % cid, {"counted": 7.5, "reason": "Spill or loss"})
    assert r.status == 200 and r.json["delta"] == -2.5 and r.json["onHand"] == 7.5
    assert [l["qtyRemaining"] for l in a.get("/api/consumables/%d/lots" % cid).json["lots"]] == [7.5]
    row = sql(srv, "SELECT delta, reason, user_name FROM consumable_txns WHERE consumable_id=? ORDER BY id DESC LIMIT 1", (cid,))[0]
    assert row[0] == -2.5 and row[1] == "Stocktake count: Spill or loss" and row[2]
    s = a.post("/api/consumables/%d/count" % cid, {"counted": 9, "reason": "Found or returned", "note": "back from the lab"})
    assert s.json["delta"] == 1.5 and item(a, cid)["onHand"] == 9 and item(a, cid)["lastCountedBy"]
    same = a.post("/api/consumables/%d/count" % cid, {"counted": 9, "reason": "Stocktake correction"})
    assert same.status == 200 and same.json["delta"] == 0
    assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE consumable_id=? AND reason LIKE 'Stocktake count%'", (cid,))[0][0] == 3     # a count with no difference is still on record


def test_a_count_needs_a_known_reason_and_a_sensible_quantity(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Count Bad")
    for body in ({"counted": 5}, {"counted": 5, "reason": "Because"}, {"counted": -1, "reason": "Damaged"}, {"counted": "x", "reason": "Damaged"}, {"counted": 5, "reason": "Other"}, {"reason": "Damaged"}):
        assert a.post("/api/consumables/%d/count" % cid, body).status == 400, body
    assert a.post("/api/consumables/%d/count" % cid, {"counted": 5, "reason": "Other", "note": "recounted by two people"}).status == 200


def test_containers_are_counted_in_whole_units(srv, people):
    a = people["admin"]
    cid = [c["id"] for c in a.get("/api/consumables").json["consumables"] if c["name"] == "New 1,000 L IBC Tote"][0]
    assert a.post("/api/consumables/%d/count" % cid, {"counted": 12.5, "reason": "Damaged"}).status == 400
    assert a.post("/api/consumables/%d/count" % cid, {"counted": 12, "reason": "Stocktake correction"}).status == 200


# ---- the nightly records archive -------------------------------------------------------------------------------------------------

def manifest(admin):
    r = admin.get("/api/archive/manifest")
    assert r.status == 200, r.text
    return r.json


def receipts_in(m):
    return [f for f in m["files"] if f["path"].startswith("06_Receiving/") and f["kind"] != "index"]


def test_documents_of_a_delivery_are_archived_in_a_fixed_folder_with_stable_names(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Archive")
    rid = a.post("/api/consumables/%d/receive" % cid, {"qty": 3, "supplierLot": "AR-1", "supplier": "DuBois Chemicals Canada Inc.", "invoiceNo": "IN-60133189", "receivedDate": "2026-08-21",
                                                       "poNumber": "1092"}).json["receiptId"]
    nodoc = a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "AR-NODOC", "supplier": "Acme", "invoiceNo": "NO-DOCS"}).json["receiptId"]
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "DuBois IN-60133189.pdf", "dataB64": PDF, "kind": "invoice"}).raise_for_status()
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "DuBois IN-60133189.pdf", "dataB64": PDF, "kind": "invoice"}).raise_for_status()     # the same name twice
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "bol.pdf", "dataB64": PDF, "kind": "delivery"}).raise_for_status()
    m = manifest(a)
    mine = [f for f in receipts_in(m) if "IN-60133189" in f["path"]]
    folder = "06_Receiving/2026/2026-08/2026-08-21_DuBois-Chemicals-Canada_IN-60133189/"
    names = sorted(f["path"][len(folder):] for f in mine)
    assert all(f["path"].startswith(folder) for f in mine) and len(mine) == 3
    assert "IN-60133189_Delivery-Doc_bol.pdf" in names and "IN-60133189_Invoice_DuBois-IN-60133189.pdf" in names
    assert any(n.startswith("IN-60133189_Invoice_DuBois-IN-60133189_") for n in names)                                  # the second copy gets its id so it cannot clash
    assert not [f for f in receipts_in(m) if "NO-DOCS" in f["path"]]                                                    # a delivery with no document has no folder
    assert "06_Receiving/_Index" in m["skeleton"] and m["deliveries"] >= 1
    for f in m["files"]:
        assert len(f["path"]) <= 200 and " " not in f["path"], f["path"]
    # the folder never moves when the delivery's details are corrected, and a new document joins it
    a.put("/api/receipts/%d" % rid, {"supplier": "DuBois", "invoiceNo": "IN-CORRECTED", "reason": "typo"}).raise_for_status()
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "coa.pdf", "dataB64": PDF, "kind": "coa"}).raise_for_status()
    again = [f for f in receipts_in(manifest(a)) if f["path"].startswith(folder)]
    assert len(again) == 4 and {f["path"] for f in mine} <= {f["path"] for f in again}
    assert any(f["path"].endswith("IN-60133189_CoA_coa.pdf") for f in again)


def test_an_archived_document_is_served_by_its_path_and_the_index_lists_the_delivery(srv, people):
    a = people["admin"]
    cid = new_item(a, "Recv Archive Serve")
    rid = a.post("/api/consumables/%d/receive" % cid, {"qty": 2, "supplierLot": "AS-1", "supplier": "Univar Solutions", "invoiceNo": "53959279", "poNumber": "DANIEL 05/29/2026",
                                                       "orderNo": "16317914", "deliveryDoc": "6503210704", "carrier": "Day & Ross", "receivedDate": "2026-06-17"}).json["receiptId"]
    a.post("/api/receipts/%d/attachments" % rid, {"filename": "Univar 53959279.pdf", "dataB64": PDF, "kind": "invoice"}).raise_for_status()
    f = [x for x in receipts_in(manifest(a)) if "53959279" in x["path"]][0]
    assert f["runId"] is None and f["version"].startswith("att")
    got = a.get("/api/archive/file?path=" + f["path"])
    assert got.status == 200 and got.body.startswith(b"%PDF") and got.headers.get("X-Archive-Sha256")
    assert a.get("/api/archive/file?path=06_Receiving/2026/nothing.pdf").status == 404
    idx = a.get("/api/archive/file?path=06_Receiving/_Index/receiving_index.csv")
    assert idx.status == 200
    text = idx.body.decode("utf-8-sig")
    assert "53959279" in text and "DANIEL 05/29/2026" in text and "16317914" in text and "6503210704" in text and "Day & Ross" in text
    assert f["path"].rsplit("/", 1)[0] in text
    assert any(x["path"] == "06_Receiving/_Index/receiving_index.csv" for x in manifest(a)["files"])


def test_the_archive_is_readable_with_the_archive_key_only(srv, people):
    a = people["admin"]
    paths = [f["path"] for f in receipts_in(manifest(a))]
    assert paths
    assert a.get("/api/archive/file?path=" + paths[0], token="").status == 401
