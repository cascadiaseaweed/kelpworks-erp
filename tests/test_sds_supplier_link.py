"""KW-83: a safety data sheet of the controlled library (Documents tab, SharePoint master) points at a supplier RECORD. Linked automatically when the supplier name in the file name matches one
record exactly (ignoring case and spacing), otherwise chosen by an administrator; a likely record is only SUGGESTED. The link is logged on the sheet and on the supplier, survives revisions, withdrawal
and renames, shows on the supplier record, and an overdue sheet raises a supplier flag. Safety data sheets can no longer be uploaded as a supplier document."""
import base64
import datetime

import pytest

from factories import sql
from harness import Server
from test_documents_library import KEY, accept, docs, put, remove, waiting

PDF = base64.b64encode(b"%PDF-1.4 test").decode()
_n = [0]


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    pm, _ = srv.make_user(role="user", isProductionManager=True)
    return {"admin": srv.admin_client(), "plain": plain, "pm": pm}


def uniq(prefix):
    _n[0] += 1
    return "%s%d" % (prefix, _n[0])


def supplier(admin, name, **over):
    r = admin.post("/api/suppliers", dict({"name": name, "type": "Distributor"}, **over))
    assert r.ok, r.text
    return r.json["id"]


def add_sheet(srv, admin, chemical, supp, issue="2025-03-14"):
    """Put a sheet in the SharePoint folder and accept it as new. Returns its row from /api/documents."""
    name = "SDS_%s_%s_%s.pdf" % (chemical.replace(" ", "-"), supp.replace(" ", "-"), issue)
    put(srv, "sds", name, ("%%PDF %s" % name).encode())
    item = waiting(admin, "sds", change="new", filename=name)
    assert item, name
    assert accept(admin, item[0]).status == 200
    return name, sheet(admin, chemical)


def sheet(client, chemical):
    return [s for s in docs(client)["sds"] if s["chemical"] == chemical][0]


def log(srv, sid):
    return sql(srv, "SELECT field, old_value, new_value, user_name FROM supplier_log WHERE supplier_id=? ORDER BY id", (sid,))


def edits(srv, did):
    return sql(srv, "SELECT field, old_value, new_value, user_name FROM document_edits WHERE doc_id=? AND kind='sds' ORDER BY id", (did,))


# ---- automatic link, suggestion, nothing guessed ---------------------------------------------------------------------------------------------------------------------
def test_a_new_sheet_is_linked_when_the_supplier_name_matches_a_record_exactly(srv, people):
    a = people["admin"]
    chem, name = uniq("Linkchem"), uniq("Acme")
    sid = supplier(a, name.lower())                                                  # a case difference does not matter
    _, sds = add_sheet(srv, a, chem, name)
    assert sds["supplierId"] == sid and sds["supplierName"] == name.lower() and sds["supplier"] == name and sds["supplierSuggestion"] is None
    assert edits(srv, sds["id"])[-1][:3] == ("Supplier", "-", name.lower())
    last = log(srv, sid)[-1]
    assert last[0] == "Safety data sheet" and last[1] is None and chem in last[2] and "2025-03-14" in last[2]


def test_without_a_matching_record_the_sheet_stays_unlinked_and_a_likely_one_is_only_suggested(srv, people):
    a = people["admin"]
    chem, short = uniq("Suggchem"), uniq("Univarx")
    full = supplier(a, short + " Solutions Canada")
    _, sds = add_sheet(srv, a, chem, short)
    assert sds["supplierId"] is None and sds["supplierName"] is None
    assert sds["supplierSuggestion"] == {"id": full, "name": short + " Solutions Canada"}                       # suggested, never applied by itself
    assert [x[0] for x in log(srv, full)] == ["record"]                                                       # nothing about the sheet: not linked
    _, none = add_sheet(srv, a, uniq("Nomatch"), uniq("Nobody"))
    assert none["supplierId"] is None and none["supplierSuggestion"] is None


def test_two_records_that_both_start_with_the_name_give_no_suggestion(srv, people):
    a = people["admin"]
    short = uniq("Twin")
    supplier(a, short + " East")
    supplier(a, short + " West")
    _, sds = add_sheet(srv, a, uniq("Twinchem"), short)
    assert sds["supplierSuggestion"] is None and sds["supplierId"] is None


def test_a_supplier_created_or_renamed_later_picks_up_the_waiting_sheet(srv, people):
    a = people["admin"]
    chem, name = uniq("Latechem"), uniq("Laterco")
    _, sds = add_sheet(srv, a, chem, name)
    assert sds["supplierId"] is None
    sid = supplier(a, name)                                                              # added after the sheet, the usual order
    assert sheet(a, chem)["supplierId"] == sid
    chem2, name2 = uniq("Renamechem"), uniq("Renamerco")
    _, sds2 = add_sheet(srv, a, chem2, name2)
    other = supplier(a, uniq("Other Co"))
    assert sheet(a, chem2)["supplierId"] is None
    assert a.put("/api/suppliers/%d" % other, {"name": name2, "reason": "renamed"}).ok
    assert sheet(a, chem2)["supplierId"] == other and sheet(a, chem2)["supplierName"] == name2


# ---- the link stays ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_link_survives_a_revision_a_withdrawal_a_return_and_a_rename(srv, people):
    a = people["admin"]
    chem, name = uniq("Keepchem"), uniq("Keepco")
    sid = supplier(a, name)
    fname, sds = add_sheet(srv, a, chem, name, "2024-02-01")
    assert sds["supplierId"] == sid
    newer = "SDS_%s_%s_2025-02-01.pdf" % (chem, name)
    remove(srv, "sds", fname)
    put(srv, "sds", newer, b"%PDF newer")
    rev = waiting(a, "sds", change="revision", filename=newer)
    assert rev and accept(a, rev[0]).status == 200
    assert sheet(a, chem)["supplierId"] == sid and sheet(a, chem)["issueDate"] == "2025-02-01"
    remove(srv, "sds", newer)
    gone = waiting(a, "sds", change="missing")
    assert gone and accept(a, gone[0]).status == 200
    w = sheet(a, chem)
    assert w["status"] == "withdrawn" and w["supplierId"] == sid
    put(srv, "sds", newer, b"%PDF newer")
    back = waiting(a, "sds", change="revision", filename=newer)
    assert back and accept(a, back[0]).status == 200
    assert sheet(a, chem)["status"] == "current" and sheet(a, chem)["supplierId"] == sid
    assert a.put("/api/suppliers/%d" % sid, {"name": name + " Renamed", "reason": "new legal name"}).ok
    assert sheet(a, chem)["supplierId"] == sid and sheet(a, chem)["supplierName"] == name + " Renamed" and sheet(a, chem)["supplier"] == name   # file-name text stays


# ---- the administrator chooses ----------------------------------------------------------------------------------------------------------------------------------------
def test_an_administrator_chooses_and_unlinks_and_both_sides_are_logged(srv, people):
    a = people["admin"]
    chem, short = uniq("Choosechem"), uniq("Pickco")
    one, two = supplier(a, short + " One Inc"), supplier(a, uniq("Second Co"))
    _, sds = add_sheet(srv, a, chem, short)
    did = sds["id"]
    assert sds["supplierSuggestion"]["id"] == one
    for who in ("plain", "pm"):
        assert people[who].put("/api/sds-documents/%d" % did, {"supplierId": one}).status == 403
    assert a.put("/api/sds-documents/%d" % did, {"supplierId": 999999}).status == 404
    r = a.put("/api/sds-documents/%d" % did, {"supplierId": one})
    assert r.status == 200 and r.json["sds"]["supplierId"] == one and r.json["sds"]["supplierSuggestion"] is None
    assert edits(srv, did)[-1][:3] == ("Supplier", "-", short + " One Inc") and "Safety data sheet" in [x[0] for x in log(srv, one)]
    assert a.put("/api/sds-documents/%d" % did, {"supplierId": two}).ok                       # moved: removed from one, added to two
    assert log(srv, one)[-1][0] == "Safety data sheet" and chem in log(srv, one)[-1][1] and log(srv, one)[-1][2] is None
    assert log(srv, two)[-1][1] is None and chem in log(srv, two)[-1][2]
    assert edits(srv, did)[-1][:3] == ("Supplier", short + " One Inc", sql(srv, "SELECT name FROM suppliers WHERE id=?", (two,))[0][0])
    n = len(edits(srv, did))
    assert a.put("/api/sds-documents/%d" % did, {"supplierId": two}).ok and len(edits(srv, did)) == n          # no change, no log line
    r = a.put("/api/sds-documents/%d" % did, {"supplierId": None})
    assert r.status == 200 and r.json["sds"]["supplierId"] is None
    assert edits(srv, did)[-1][2] == "-" and log(srv, two)[-1][2] is None


# ---- on the supplier record ---------------------------------------------------------------------------------------------------------------------------------------------
def test_the_supplier_lists_its_sheets_current_and_withdrawn_and_flags_an_overdue_one(srv, people):
    a, plain = people["admin"], people["plain"]
    name = uniq("Flagco")
    sid = supplier(a, name, approvalStatus="Approved", approvalDate=datetime.date.today().isoformat(), nextReviewDate=(datetime.date.today() + datetime.timedelta(days=300)).isoformat())
    fresh = uniq("Freshchem")
    old = uniq("Oldchem")
    item = a.post("/api/consumables", {"name": uniq("Sdsitem"), "unit": "kg", "onHand": 0})
    assert item.ok
    cid = [c["id"] for c in a.get("/api/consumables").json["consumables"] if c["name"].startswith("Sdsitem")][-1]
    _, f = add_sheet(srv, a, fresh, name, datetime.date.today().replace(day=1).isoformat())
    assert a.get("/api/suppliers/%d" % sid).json["flags"] == []
    gname, o = add_sheet(srv, a, old, name, "2019-05-20")
    assert a.put("/api/sds-documents/%d" % o["id"], {"itemIds": [cid]}).ok
    d = plain.get("/api/suppliers/%d" % sid).json
    by = {s["chemical"]: s for s in d["sds"]}
    assert by[old]["reviewStatus"] == "overdue" and by[old]["issueDate"] == "2019-05-20" and by[old]["status"] == "current" and [i["id"] for i in by[old]["items"]] == [cid]
    assert by[fresh]["reviewStatus"] in ("ok", "soon") and "sds_review_overdue" in d["flags"]
    q = lambda flag: [s["name"] for s in a.get("/api/suppliers?flag=%s&q=%s" % (flag, name)).json["suppliers"]]
    assert q("sds_review_overdue") == [name] and q("any") == [name]
    remove(srv, "sds", gname)                                                           # withdrawn: listed, greyed, and no longer raises the flag
    gone = [i for i in waiting(a, "sds", change="missing") if old in (i.get("filename") or "")]
    assert gone and accept(a, gone[0]).status == 200
    d = a.get("/api/suppliers/%d" % sid).json
    by = {s["chemical"]: s for s in d["sds"]}
    assert by[old]["status"] == "withdrawn" and by[old]["reviewStatus"] is None and "sds_review_overdue" not in d["flags"]
    assert q("sds_review_overdue") == []


# ---- safety data sheets are not supplier documents any more ---------------------------------------------------------------------------------------------------------------
def test_a_safety_data_sheet_cannot_be_uploaded_as_a_supplier_document(srv, people):
    a = people["admin"]
    sid = supplier(a, uniq("Nodocco"))
    r = a.post("/api/suppliers/%d/documents" % sid, {"filename": "sds.pdf", "dataB64": PDF, "kind": "SDS"})
    assert r.status == 400 and "Documents" in r.text
    assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "cert.pdf", "dataB64": PDF, "kind": "Certificate"}).ok
    assert "SDS" not in a.get("/api/suppliers").json["docKinds"]


def test_a_document_already_stored_with_the_old_sds_kind_is_still_listed(srv, people):
    a = people["admin"]
    sid = supplier(a, uniq("Oldkindco"))
    assert a.post("/api/suppliers/%d/documents" % sid, {"filename": "old.pdf", "dataB64": PDF, "kind": "Other"}).ok
    sql(srv, "UPDATE supplier_documents SET kind='SDS' WHERE supplier_id=?", (sid,))
    assert [d["kind"] for d in a.get("/api/suppliers/%d" % sid).json["documents"]] == ["SDS"]


# ---- the one-time step for sheets already in the library --------------------------------------------------------------------------------------------------------------
def test_the_one_time_step_links_exact_matches_only_and_runs_once():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        a = s.admin_client()
        exact, near = supplier(a, "Migrate Exact Co"), supplier(a, "Migrate Near Company Ltd")
        for chem, supp in (("Migchem1", "migrate  EXACT co"), ("Migchem2", "Migrate Near")):
            sql(s, "INSERT INTO sds_documents (chemical, supplier, sds_key, issue_date, status, filename, uploaded_at, updated_at) VALUES (?,?,?,?,'current',?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (chem, supp, "%s|%s" % (chem.lower(), supp.lower()), "2025-01-01", "SDS_%s.pdf" % chem))
        sql(s, "DELETE FROM app_flags WHERE key='migrated_sds_supplier_links_kw83'")
        s.restart()
        a = s.admin_client()
        assert sheet(a, "Migchem1")["supplierId"] == exact and sheet(a, "Migchem2")["supplierId"] is None
        assert sheet(a, "Migchem2")["supplierSuggestion"]["id"] == near                       # only suggested
        assert log(s, exact)[-1][3] == "System" and edits(s, sheet(a, "Migchem1")["id"])[-1][3] == "System"
        assert a.put("/api/sds-documents/%d" % sheet(a, "Migchem1")["id"], {"supplierId": None}).ok     # an admin unlinks it: a second start must not undo that
        s.restart()
        assert sheet(s.admin_client(), "Migchem1")["supplierId"] is None


def test_a_new_database_records_the_step_and_has_nothing_to_link():
    with Server() as s:
        assert sql(s, "SELECT 1 FROM app_flags WHERE key='migrated_sds_supplier_links_kw83'")
        assert sql(s, "SELECT COUNT(*) FROM sds_documents WHERE supplier_id IS NOT NULL")[0][0] == 0
        s.restart()
        assert sql(s, "SELECT COUNT(*) FROM sds_documents")[0][0] == 0
