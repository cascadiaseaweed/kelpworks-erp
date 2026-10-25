"""KW-81: a supplier has a record (details, approval status with a review date, documents with expiry dates, history). Only an administrator or Production Manager changes suppliers (as KW-23); everyone can
read. A delivery or lot keeps the supplier NAME it was recorded with. The approved-supplier lists of items point at the records; existing names become records once, exact matches merged, near-matches only listed."""
import base64
import datetime
import os
from urllib.parse import quote

import pytest

from factories import sql
from harness import Server

PDF = base64.b64encode(b"%PDF-1.4 test certificate").decode()


def today(days=0):
    return (datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=days)).isoformat()


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


_n = [0]


def make(client, **over):
    _n[0] += 1
    body = dict({"name": "Supplier %d" % _n[0], "type": "Distributor"}, **over)
    r = client.post("/api/suppliers", body)
    assert r.ok, r.text
    return r.json


def new_item(admin, name):
    assert admin.post("/api/consumables", {"name": name, "unit": "kg", "onHand": 0}).ok
    return [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def history(srv, sid):
    return sql(srv, "SELECT field, old_value, new_value, reason, user_name FROM supplier_log WHERE supplier_id=? ORDER BY id", (sid,))


# ---- who may change what -------------------------------------------------------------------------------------------------------------------------------------------------
def test_only_an_administrator_or_production_manager_changes_suppliers_and_everyone_can_read(srv, people):
    for who in ("plain", "qm"):                                                                                  # the Quality Manager flag alone grants nothing, as in KW-23
        assert people[who].post("/api/suppliers", {"name": "Nope Co", "type": "Lab"}).status == 403
    s = make(people["pm"], name="PM Made Co")
    sid = s["id"]
    assert make(people["admin"], name="Admin Made Co")["name"] == "Admin Made Co"
    for who in ("plain", "qm"):
        assert people[who].put("/api/suppliers/%d" % sid, {"notes": "x"}).status == 403
        assert people[who].post("/api/suppliers/%d/documents" % sid, {"filename": "c.pdf", "dataB64": PDF}).status == 403
        assert people[who].get("/api/suppliers").status == 200
        assert people[who].get("/api/suppliers/%d" % sid).json["canEdit"] is False
        assert people[who].get("/api/suppliers/by-name?name=" + quote("pm made co")).json["supplier"]["id"] == sid          # any case
    assert people["pm"].get("/api/suppliers/%d" % sid).json["canEdit"] is True
    assert people["plain"].get("/api/suppliers/99999").status == 404


def test_a_supplier_needs_a_name_a_type_and_valid_fields_and_names_are_unique_in_any_case(srv, people):
    a = people["admin"]
    assert a.post("/api/suppliers", {"type": "Lab"}).status == 400
    assert a.post("/api/suppliers", {"name": "  ", "type": "Lab"}).status == 400
    assert a.post("/api/suppliers", {"name": "No Type Co"}).status == 400
    assert a.post("/api/suppliers", {"name": "Bad Type Co", "type": "Pirate"}).status == 400
    assert a.post("/api/suppliers", {"name": "Bad Status Co", "type": "Lab", "approvalStatus": "Maybe"}).status == 400
    assert a.post("/api/suppliers", {"name": "Bad Date Co", "type": "Lab", "approvalDate": "tomorrow"}).status == 400
    assert a.post("/api/suppliers", {"name": "Bad Mail Co", "type": "Lab", "contactEmail": "nobody"}).status == 400
    first = make(a, name="  Unique   Spacing  Co ")
    assert first["name"] == "Unique Spacing Co" and first["approvalStatus"] == "Conditional"                 # spacing tidied; a new supplier starts Conditional
    assert a.post("/api/suppliers", {"name": "unique spacing co", "type": "Lab"}).status == 409


def test_every_agreed_field_is_stored_and_the_next_review_is_suggested_from_the_approval_date(srv, people):
    a = people["admin"]
    s = make(a, name="Full Record Co", type="Manufacturer", accountNumber="ACC-77", contactName="Pat Lee", contactPhone="250-555-0100", contactEmail="pat@full.example",
             address="1 Dock Rd, Port Edward BC", website="https://full.example", paymentTerms="Net 30", currency="CAD", taxNumber="123456789 RT0001", approvalStatus="Approved",
             approvalDate="2026-03-31", notes="Organic certificate on file")
    got = a.get("/api/suppliers/%d" % s["id"]).json
    assert (got["type"], got["accountNumber"], got["contactName"], got["contactPhone"], got["contactEmail"], got["address"], got["website"], got["paymentTerms"], got["currency"],
            got["taxNumber"], got["approvalStatus"], got["approvalDate"], got["notes"]) == ("Manufacturer", "ACC-77", "Pat Lee", "250-555-0100", "pat@full.example", "1 Dock Rd, Port Edward BC",
            "https://full.example", "Net 30", "CAD", "123456789 RT0001", "Approved", "2026-03-31", "Organic certificate on file")
    assert got["nextReviewDate"] == "2027-03-31"                                                                 # approval date + supplier_review_interval_months (12)
    leap = make(a, approvalStatus="Approved", approvalDate="2024-02-29")
    assert leap["nextReviewDate"] == "2025-02-28"                                                                # a month-end never overflows
    own = make(a, approvalStatus="Approved", approvalDate="2026-03-31", nextReviewDate="2026-09-30")
    assert own["nextReviewDate"] == "2026-09-30"                                                                 # a date typed by a person wins


def test_the_two_new_settings_exist_with_their_defaults(srv, people):
    st = people["plain"].get("/api/settings").json["settings"]
    assert st["supplier_review_interval_months"]["value"] == 12 and st["supplier_doc_warning_days"]["value"] == 30


# ---- the history -----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_every_change_is_in_an_append_only_history_and_a_status_change_needs_a_reason(srv, people):
    a, pm = people["admin"], people["pm"]
    s = make(pm, name="History Co")
    sid = s["id"]
    assert pm.put("/api/suppliers/%d" % sid, {"contactName": "Sam Ray", "paymentTerms": "Net 45"}).status == 200
    assert pm.put("/api/suppliers/%d" % sid, {"approvalStatus": "Approved"}).status == 400                       # who approved it and why
    ok = pm.put("/api/suppliers/%d" % sid, {"approvalStatus": "Approved", "reason": "Audit passed 2026-10-20"})
    assert ok.status == 200 and ok.json["approvalStatus"] == "Approved"
    assert pm.put("/api/suppliers/%d" % sid, {"contactName": "Sam Ray"}).status == 200                           # nothing changed: nothing logged
    rows = history(srv, sid)
    assert [r[0] for r in rows] == ["record", "Contact name", "Payment terms", "Approval status"] and rows[0][2] == "created (Conditional)"
    assert rows[1][1:3] == (None, "Sam Ray") and rows[3][1:4] == ("Conditional", "Approved", "Audit passed 2026-10-20") and all(r[4] for r in rows)
    shown = a.get("/api/suppliers/%d" % sid).json["history"]
    assert shown[0]["field"] == "Approval status" and shown[0]["reason"] == "Audit passed 2026-10-20" and len(shown) == 4        # newest first
    assert a.put("/api/suppliers/%d" % sid, {"name": make(a, name="Taken Co")["name"]}).status == 409           # a name that is already a supplier


# ---- the item lists (KW-23) and the records -----------------------------------------------------------------------------------------------------------------------------------
def test_a_name_added_to_an_items_list_becomes_a_record_once_whatever_its_case(srv, people):
    a = people["admin"]
    c1, c2 = new_item(a, "List Item 1"), new_item(a, "List Item 2")
    assert a.post("/api/consumables/%d/suppliers" % c1, {"name": "Brand New Chemicals"}).status == 200
    rec = a.get("/api/suppliers/by-name?name=" + quote("Brand New Chemicals")).json["supplier"]
    assert rec["approvalStatus"] == "Conditional" and rec["nItems"] == 1 and [i["name"] for i in rec["items"]] == ["List Item 1"]
    assert a.post("/api/consumables/%d/suppliers" % c2, {"name": "brand new CHEMICALS"}).status == 200            # the same supplier: no second record
    assert [s["name"] for s in a.get("/api/consumables/%d/suppliers" % c2).json["suppliers"]] == ["Brand New Chemicals"]       # the record's own spelling
    assert len([s for s in a.get("/api/suppliers?q=" + quote("brand new")).json["suppliers"]]) == 1
    assert a.get("/api/suppliers/by-name?name=" + quote("Brand New Chemicals")).json["supplier"]["nItems"] == 2
    assert sql(srv, "SELECT COUNT(*) FROM consumable_suppliers WHERE supplier_id IS NULL")[0][0] == 0


def test_receipts_and_lots_keep_the_name_they_were_recorded_with_after_a_rename(srv, people):
    a = people["admin"]
    cid = new_item(a, "Rename Item")
    s = make(a, name="Old Name Ltd")
    assert a.post("/api/consumables/%d/suppliers" % cid, {"name": "Old Name Ltd"}).status == 200
    rcv = a.post("/api/consumables/%d/receive" % cid, {"qty": 5, "supplierLot": "R-1", "supplier": "Old Name Ltd", "invoiceNo": "INV-9"})
    assert rcv.status == 200, rcv.text
    renamed = a.put("/api/suppliers/%d" % s["id"], {"name": "New Name Corp"})
    assert renamed.status == 200 and renamed.json["name"] == "New Name Corp"
    assert [x["name"] for x in a.get("/api/consumables/%d/suppliers" % cid).json["suppliers"]] == ["New Name Corp"]          # the item list follows
    assert sql(srv, "SELECT supplier FROM consumable_lots WHERE consumable_id=?", (cid,))[0][0] == "Old Name Ltd"           # history does not
    assert sql(srv, "SELECT supplier FROM inventory_receipts WHERE invoice_no='INV-9'")[0][0] == "Old Name Ltd"
    assert a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "R-2", "supplier": "New Name Corp"}).status == 200
    assert history(srv, s["id"])[-1][:3] == ("Name", "Old Name Ltd", "New Name Corp")


def test_items_are_linked_to_a_supplier_from_its_own_record_so_receiving_offers_it(srv, people):
    a, pm, plain, qm = people["admin"], people["pm"], people["plain"], people["qm"]
    s = make(a, name="Linkable Co")
    sid = s["id"]
    c1, c2 = new_item(a, "Link Item 1"), new_item(a, "Link Item 2")
    assert a.get("/api/consumables/%d/suppliers" % c1).json["suppliers"] == []                                 # a new supplier is on no item's list yet
    for who in (plain, qm):
        assert who.post("/api/suppliers/%d/items" % sid, {"consumableId": c1}).status == 403
    r = pm.post("/api/suppliers/%d/items" % sid, {"consumableId": c1})
    assert r.status == 200 and [i["name"] for i in r.json["items"]] == ["Link Item 1"] and r.json["nItems"] == 1
    assert [x["name"] for x in a.get("/api/consumables/%d/suppliers" % c1).json["suppliers"]] == ["Linkable Co"]       # the Receive dropdown for that item
    assert a.post("/api/consumables/%d/receive" % c1, {"qty": 1, "supplierLot": "LK-1", "supplier": "Linkable Co"}).status == 200
    assert pm.post("/api/suppliers/%d/items" % sid, {"consumableId": c1}).status == 409                         # already on the list
    assert pm.post("/api/suppliers/%d/items" % sid, {"consumableId": 99999}).status == 404
    assert pm.post("/api/suppliers/%d/items" % sid, {"consumableId": "x"}).status == 404
    assert plain.delete("/api/suppliers/%d/items/%d" % (sid, c1)).status == 403
    assert pm.delete("/api/suppliers/%d/items/%d" % (sid, c1)).status == 200
    assert a.get("/api/consumables/%d/suppliers" % c1).json["suppliers"] == [] and a.get("/api/suppliers/%d" % sid).json["items"] == []
    assert pm.delete("/api/suppliers/%d/items/%d" % (sid, c1)).status == 404
    rows = [r for r in history(srv, sid) if r[0] == "Items they supply"]
    assert [(r[1] is None, r[2] is None) for r in rows] == [(True, False), (False, True)] and "Link Item 1" in (rows[0][2] or "") and "Link Item 1" in (rows[1][1] or "")
    log = sql(srv, "SELECT supplier, action FROM consumable_supplier_log WHERE consumable_id=? ORDER BY id", (c1,))
    assert log == [("Linkable Co", "added"), ("Linkable Co", "removed")]                                         # the item-side log (KW-23) is kept too
    # the item's own list (Inventory Items > Edit) also leaves a line in the supplier's history
    assert a.post("/api/consumables/%d/suppliers" % c2, {"name": "Linkable Co"}).status == 200
    assert "Link Item 2" in history(srv, sid)[-1][2] and history(srv, sid)[-1][0] == "Items they supply"
    link_id = a.get("/api/consumables/%d/suppliers" % c2).json["suppliers"][0]["id"]
    assert a.delete("/api/consumables/%d/suppliers/%d" % (c2, link_id)).status == 200
    assert "Link Item 2" in history(srv, sid)[-1][1]


# ---- documents -------------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_document_with_an_expiry_date_is_stored_served_and_flagged(srv, people):
    a, pm = people["admin"], people["pm"]
    s = make(a, approvalStatus="Approved", approvalDate=today(), nextReviewDate=today(300))
    sid = s["id"]
    soon, past, far = today(10), today(-5), today(200)
    for name, kind, exp in (("organic.pdf", "Organic certificate", soon), ("insurance.pdf", "Insurance", far), ("other.pdf", "Other", None)):
        r = pm.post("/api/suppliers/%d/documents" % sid, {"filename": name, "dataB64": PDF, "kind": kind, "expiryDate": exp, "note": "scan"})
        assert r.status == 200, r.text
    docs = {d["filename"]: d for d in a.get("/api/suppliers/%d" % sid).json["documents"]}
    assert docs["organic.pdf"]["status"] == "expiring" and docs["insurance.pdf"]["status"] == "ok" and docs["other.pdf"]["status"] == "none"
    assert docs["organic.pdf"]["kind"] == "Organic certificate" and docs["organic.pdf"]["uploadedBy"]
    assert a.get("/api/suppliers/%d" % sid).json["flags"] == ["docs_expiring"]
    did = docs["organic.pdf"]["id"]
    assert pm.put("/api/suppliers/%d/documents/%d" % (sid, did), {"expiryDate": past}).status == 200
    assert a.get("/api/suppliers/%d" % sid).json["flags"] == ["docs_expired"] or "docs_expired" in a.get("/api/suppliers/%d" % sid).json["flags"]
    assert [r[0] for r in history(srv, sid)][-1].startswith("document expiry") and history(srv, sid)[-1][1:3] == (soon, past)
    got = a.get("/api/suppliers/%d/attachments/%d/download" % (sid, did))
    assert got.status == 200 and got.body.startswith(b"%PDF")
    link = a.get("/api/suppliers/%d/attachments/%d/download?token=%s" % (sid, did, a.download_token()), token="")
    assert link.status == 200 and link.body.startswith(b"%PDF")
    assert a.get("/api/suppliers/%d/attachments/%d/download" % (sid, did), token="").status == 401


def test_only_safe_files_with_a_real_date_are_accepted(srv, people):
    a = people["admin"]
    sid = make(a)["id"]
    for name in ("x.html", "x.svg", "x.exe"):
        assert a.post("/api/suppliers/%d/documents" % sid, {"filename": name, "dataB64": PDF}).status == 400
    assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "c.pdf", "dataB64": ""}).status == 400
    assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "c.pdf", "dataB64": PDF, "expiryDate": "soon"}).status == 400
    assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "c.pdf", "dataB64": PDF, "kind": "Whatever"}).json["documents"][0]["kind"] == "Other"


def test_a_removed_document_needs_a_reason_disappears_and_its_file_stays(srv, people):
    a, plain = people["admin"], people["plain"]
    sid = make(a)["id"]
    a.post("/api/suppliers/%d/documents" % sid, {"filename": "old cert.pdf", "dataB64": PDF, "kind": "Certificate"}).raise_for_status()
    did = a.get("/api/suppliers/%d" % sid).json["documents"][0]["id"]
    stored = sql(srv, "SELECT stored_name FROM supplier_documents WHERE id=?", (did,))[0][0]
    assert plain.delete("/api/suppliers/%d/documents/%d" % (sid, did), body={"reason": "x"}).status == 403
    assert a.delete("/api/suppliers/%d/documents/%d" % (sid, did), body={}).status == 400
    assert a.delete("/api/suppliers/%d/documents/%d" % (sid, did), body={"reason": "Replaced by the 2026 certificate"}).status == 200
    assert a.get("/api/suppliers/%d" % sid).json["documents"] == []
    assert a.get("/api/suppliers/%d/attachments/%d/download" % (sid, did)).status == 404
    assert os.path.isfile(os.path.join(srv.tmp, "uploads", stored))                                              # never deleted from disk
    row = sql(srv, "SELECT removed_by, removed_reason FROM supplier_documents WHERE id=?", (did,))[0]
    assert row[0] and row[1] == "Replaced by the 2026 certificate"
    last = history(srv, sid)[-1]
    assert last[0] == "document removed" and last[1] == "old cert.pdf" and last[3] == "Replaced by the 2026 certificate"
    assert a.delete("/api/suppliers/%d/documents/%d" % (sid, did), body={"reason": "again"}).status == 404


def test_supplier_documents_count_against_the_daily_upload_limit(srv, people):
    a = people["admin"]
    sid = make(a)["id"]
    big = base64.b64encode(b"%PDF-1.4 " + b"x" * (600 * 1024)).decode()
    sql(srv, "UPDATE supplier_documents SET uploaded_at='2020-01-01T00:00:00Z'")
    sql(srv, "UPDATE receipt_attachments SET uploaded_at='2020-01-01T00:00:00Z'")
    sql(srv, "UPDATE run_attachments SET uploaded_at='2020-01-01T00:00:00Z'")
    assert a.put("/api/settings/upload_user_daily_mb", {"value": 1}).status == 200
    try:
        assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "a.pdf", "dataB64": big}).status == 200
        refused = a.post("/api/suppliers/%d/documents" % sid, {"filename": "b.pdf", "dataB64": big})
        assert refused.status == 429 and refused.json["code"] == "upload_quota"
        assert len(a.get("/api/suppliers/%d" % sid).json["documents"]) == 1
    finally:
        a.put("/api/settings/upload_user_daily_mb", {"value": 300})


# ---- the list: search, filters, flags ----------------------------------------------------------------------------------------------------------------------------------------
def test_the_list_searches_filters_and_flags_what_needs_attention(srv, people):
    a = people["admin"]
    tag = "Zed%d" % os.getpid()
    ok = make(a, name=tag + " Fine", type="Manufacturer", approvalStatus="Approved", approvalDate=today(), nextReviewDate=today(200), contactName="Quinn Fine")
    late = make(a, name=tag + " Late", type="Carrier", approvalStatus="Approved", approvalDate=today(-400), nextReviewDate=today(-3))
    none = make(a, name=tag + " NoDate", type="Carrier", approvalStatus="Conditional")
    off = make(a, name=tag + " Off", type="Lab", approvalStatus="Inactive")
    sid = make(a, name=tag + " Docs", type="Service", approvalStatus="Approved", approvalDate=today(), nextReviewDate=today(200))["id"]
    a.post("/api/suppliers/%d/documents" % sid, {"filename": "e.pdf", "dataB64": PDF, "expiryDate": today(-1)}).raise_for_status()
    a.post("/api/suppliers/%d/documents" % sid, {"filename": "s.pdf", "dataB64": PDF, "expiryDate": today(5)}).raise_for_status()
    by = {s["name"]: s for s in a.get("/api/suppliers?q=" + tag).json["suppliers"]}
    assert set(by) == {tag + " Fine", tag + " Late", tag + " NoDate", tag + " Off", tag + " Docs"}
    assert by[tag + " Fine"]["flags"] == [] and by[tag + " Late"]["flags"] == ["review_overdue"] and by[tag + " NoDate"]["flags"] == ["no_review_date"]
    assert by[tag + " Off"]["flags"] == []                                                                       # an Inactive supplier is never "overdue"
    assert by[tag + " Docs"]["flags"] == ["docs_expired", "docs_expiring"] and by[tag + " Docs"]["nDocs"] == 2
    q = lambda extra: sorted(s["name"][len(tag) + 1:] for s in a.get("/api/suppliers?q=%s%s" % (tag, extra)).json["suppliers"])
    assert q("&type=Carrier") == ["Late", "NoDate"] and q("&status=Inactive") == ["Off"] and q("&flag=review_overdue") == ["Late"]
    assert q("&flag=any") == ["Docs", "Late", "NoDate"] and q("&flag=docs_expired") == ["Docs"]
    assert [s["name"] for s in a.get("/api/suppliers?q=quinn").json["suppliers"]] == [tag + " Fine"]             # also searches the contact name
    listing = a.get("/api/suppliers").json
    assert listing["types"] == ["Manufacturer", "Distributor", "Carrier", "Lab", "Service"] and "Not approved" in listing["statuses"] and "Organic certificate" in listing["docKinds"]
    assert a.get("/api/suppliers/by-name?name=nobody%20here").json == {"supplier": None}


# ---- the one-time migration ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_existing_supplier_names_become_records_once_exact_matches_merged_near_matches_only_listed():
    with Server() as s:
        a = s.admin_client()
        c1, c2 = new_item(a, "Mig Item A"), new_item(a, "Mig Item B")
        sql(s, "DELETE FROM supplier_log")                                                                        # a database as it was before KW-81: names on item lists, no records
        sql(s, "UPDATE consumable_suppliers SET supplier_id=NULL")
        sql(s, "DELETE FROM suppliers")
        sql(s, "DELETE FROM app_flags WHERE key='migrated_suppliers_from_item_lists_kw81'")
        sql(s, "DELETE FROM config_log WHERE area='supplier'")
        for cid, name in ((c1, "Univar Solutions Canada"), (c2, "univar  SOLUTIONS canada"), (c1, "DuBois Chemicals Canada"), (c2, "DuBois Chemicals Canada Inc."),
                          (c1, "Island Barrels & Totes")):
            sql(s, "INSERT INTO consumable_suppliers (consumable_id,name,created_by,created_at) VALUES (?,?,'old',?)", (cid, name, "2026-10-01T00:00:00Z"))
        names_before = sorted(sql(s, "SELECT consumable_id, name FROM consumable_suppliers"))
        s.restart()
        a = s.admin_client()
        recs = sql(s, "SELECT name, approval_status, created_by FROM suppliers ORDER BY name")
        assert recs == [("DuBois Chemicals Canada", "Approved", "System"), ("DuBois Chemicals Canada Inc.", "Approved", "System"), ("Island Barrels & Totes", "Approved", "System"),
                        ("Univar Solutions Canada", "Approved", "System")]                                         # the two Univar spellings merged (exact); the two DuBois names were NOT
        assert sql(s, "SELECT COUNT(*) FROM consumable_suppliers WHERE supplier_id IS NULL")[0][0] == 0
        assert sql(s, "SELECT COUNT(DISTINCT supplier_id) FROM consumable_suppliers WHERE LOWER(name) LIKE 'univar%'")[0][0] == 1
        assert sorted(sql(s, "SELECT consumable_id, name FROM consumable_suppliers")) == names_before             # the item lists are untouched
        listing = a.get("/api/suppliers").json
        assert listing["nearMatches"] == [{"a": "DuBois Chemicals Canada", "b": "DuBois Chemicals Canada Inc."}]
        assert [r[0] for r in sql(s, "SELECT supplier_id FROM supplier_log WHERE field='record'")] and len(sql(s, "SELECT 1 FROM supplier_log")) == 4
        s.restart()                                                                                               # a second start changes nothing
        assert len(sql(s, "SELECT 1 FROM suppliers")) == 4 and len(sql(s, "SELECT 1 FROM config_log WHERE area='supplier' AND key='near_match'")) == 1
        sql(s, "INSERT INTO consumable_suppliers (consumable_id,name,created_by,created_at) VALUES (?,'Late Arrival Co','old','2026-10-02T00:00:00Z')", (c1,))     # a link made by older code
        s.restart()
        assert sql(s, "SELECT approval_status FROM suppliers WHERE name='Late Arrival Co'") == [("Conditional",)]
        assert sql(s, "SELECT COUNT(*) FROM consumable_suppliers WHERE supplier_id IS NULL")[0][0] == 0


def test_a_new_database_has_no_suppliers_and_the_migration_is_recorded():
    with Server() as s:
        assert sql(s, "SELECT COUNT(*) FROM suppliers")[0][0] == 0
        assert sql(s, "SELECT 1 FROM app_flags WHERE key='migrated_suppliers_from_item_lists_kw81'")
        s.restart()
        assert sql(s, "SELECT COUNT(*) FROM suppliers")[0][0] == 0
