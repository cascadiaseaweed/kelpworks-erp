"""SOP and safety data sheet sync from the SharePoint folders (batch 10): reading the file names, the inbox an administrator reviews, the SOP / SDS registers,
revision history, who may do what, and the sync script's new direction (folder -> KelpWorks).

The tests share one server and one simulated SharePoint folder per kind (`FOLDERS`): a scan always reports the WHOLE folder, as the real sync does."""
import datetime
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pytest

import kelp_erp_server as K
from harness import ROOT, Server
from factories import sql

KEY = "library-key-for-tests-0123456789abcdef"
TOOL = os.path.join(ROOT, "tools", "kelpworks_archive_sync.py")
DIRS = {"sop": "03_SOPs/Current", "sds": "04_Safety-Data-Sheets/Current"}
FOLDERS = {"sop": {}, "sds": {}}                       # kind -> {file name: bytes}: what SharePoint holds right now


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def plain(srv):
    return srv.make_user()[0]


def entry(kind, name, data):
    return {"path": "%s/%s" % (DIRS[kind], name), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def scan_files(srv, kind, files, key=KEY, client=None):
    r = (client or srv.client()).post("/api/library/scan", {"kind": kind, "files": files}, headers={"X-Archive-Key": key})
    assert r.status == 200, r.text
    return r.json


def report(srv, kind):
    """Report the whole simulated folder (what the sync does first). Returns the reply; the files it asks for are NOT sent yet."""
    return scan_files(srv, kind, [entry(kind, n, d) for n, d in FOLDERS[kind].items()])


def send(srv, kind, reply):
    for need in reply["need"]:
        name = need["path"].rsplit("/", 1)[1]
        assert upload(srv, kind, need["path"], FOLDERS[kind][name])[0] == 200


def sync(srv, kind):
    reply = report(srv, kind)
    send(srv, kind, reply)
    return reply


def put(srv, kind, name, data):
    FOLDERS[kind][name] = data
    return sync(srv, kind)


def remove(srv, kind, name):
    FOLDERS[kind].pop(name)
    return report(srv, kind)


def upload(srv, kind, path, data, sha=None, key=KEY):
    url = "%s/api/library/upload?kind=%s&path=%s&sha256=%s" % (srv.url, kind, urllib.parse.quote(path), sha or hashlib.sha256(data).hexdigest())
    req = urllib.request.Request(url, data=data, method="POST", headers={"X-Archive-Key": key, "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def inbox(admin):
    r = admin.get("/api/library/inbox")
    assert r.status == 200, r.text
    return r.json


def pending(admin, kind=None):
    return [i for i in inbox(admin)["pending"] if kind in (None, i["kind"])]


def waiting(admin, kind, **match):
    return [i for i in pending(admin, kind) if all(i.get(k) == v for k, v in match.items())]


def docs(client):
    return client.get("/api/documents").json


def accept(admin, item, **body):
    return admin.post("/api/library/inbox/%d/accept" % item["id"], body)


def add_sop(srv, admin, number, name, rev, data, title="Procedure", **body):
    """Put an SOP file in the folder and accept it as a new document."""
    fname = "SOP-%03d_%s_rev%s.pdf" % (number, title, rev)
    put(srv, "sop", fname, data)
    r = accept(admin, waiting(admin, "sop", identity="SOP-%03d" % number, change="new")[0], name=name, **body)
    assert r.status == 200, r.text
    return fname


# ---- reading the file names ----

@pytest.mark.parametrize("name,expect", [
    ("SOP-012_Homogenization_rev3.pdf", ("SOP-012", "Homogenization", "3")),
    ("SOP002_Solid_Liquid_Ratio_&_Density_Analysis-compressed.pdf", ("SOP-002", "Solid Liquid Ratio & Density Analysis compressed", None)),
    ("sop 7 Cleaning Of Tanks r2.docx", ("SOP-007", "Cleaning Of Tanks", "2")),
    ("SOP-100_Bottle-Rinse.pdf", ("SOP-100", "Bottle Rinse", None)),               # "Rinse" is not a revision
    ("SOP-005_Labelling_Rev-B.pdf", ("SOP-005", "Labelling", "B")),
    ("SOP-1000_Long-Number_v12.pdf", ("SOP-1000", "Long Number", "12")),
    ("SOP-020.pdf", ("SOP-020", "", None)),
])
def test_sop_file_names_are_read(name, expect):
    p = K.parse_sop_filename(name)
    assert (p["docNumber"], p["title"], p["revision"]) == expect


@pytest.mark.parametrize("name", ["Homogenization.pdf", "SOPS_overview.pdf", "SOP_Homogenization.pdf", "Copy of SOP-001.pdf"])
def test_sop_names_that_cannot_be_read_say_so(name):
    with pytest.raises(ValueError, match="SOP-"):
        K.parse_sop_filename(name)


def test_sds_file_names_are_read():
    p = K.parse_sds_filename("SDS_Citric-Acid_Univar_2025-03-14.pdf")
    assert (p["chemical"], p["supplier"], p["issueDate"], p["sdsKey"]) == ("Citric Acid", "Univar", "2025-03-14", "citric-acid|univar")
    assert K.parse_sds_filename("SDS_Sodium-Benzoate_Fisher-Scientific_20240131.pdf")["issueDate"] == "2024-01-31"
    for bad in ("SDS_Citric-Acid_Univar.pdf", "SDS Citric Acid Univar 2025-03-14.pdf", "SDS_Citric-Acid_Univar_2025-13-45.pdf", "SDS_Citric-Acid_Univar_2025-03-14 (1).pdf"):
        with pytest.raises(ValueError):
            K.parse_sds_filename(bad)


def test_the_review_date_is_the_issue_date_plus_the_interval():
    today = datetime.date(2026, 10, 10)
    assert K.sds_review("2023-10-10", 3, 60, today) == ("2026-10-10", "soon")
    assert K.sds_review("2023-11-20", 3, 60, today) == ("2026-11-20", "soon")
    assert K.sds_review("2024-03-01", 3, 60, today) == ("2027-03-01", "ok")
    assert K.sds_review("2023-10-09", 3, 60, today) == ("2026-10-09", "overdue")
    assert K.sds_review(None, 3, 60, today) == (None, None)
    assert K.sds_review("2024-02-29", 1, 0, today) == ("2025-02-28", "overdue")           # a leap day falls back to the month's last day


# ---- who may report and read ----

def test_the_sync_needs_the_key_or_an_administrator(srv, plain):
    body = {"kind": "sop", "files": []}
    assert srv.client().post("/api/library/scan", body).status == 401
    assert srv.client().post("/api/library/scan", body, headers={"X-Archive-Key": "wrong-wrong-wrong-wrong-wrong"}).status == 403
    assert plain.post("/api/library/scan", body).status == 403
    assert srv.client().post("/api/library/scan", {"kind": "nope", "files": []}, headers={"X-Archive-Key": KEY}).status == 400
    assert srv.client().post("/api/library/scan", {"kind": "sop", "files": "x"}, headers={"X-Archive-Key": KEY}).status == 400


def test_only_administrators_review_but_everyone_can_read(srv, admin, plain):
    put(srv, "sop", "SOP-300_Access-Check_rev1.pdf", b"%PDF access")
    item = waiting(admin, "sop", identity="SOP-300")[0]
    assert plain.get("/api/library/inbox").status == 403
    assert accept(plain, item).status == 403
    assert plain.post("/api/library/inbox/%d/ignore" % item["id"], {}).status == 403
    assert plain.get("/api/documents").status == 200 and srv.client().get("/api/documents").status == 401
    assert plain.get("/api/library/inbox/%d/file" % item["id"]).status == 403
    assert accept(admin, item, name="Access Check").status == 200


# ---- a new SOP ----

def test_a_new_sop_is_reviewed_then_added(srv, admin, plain):
    data = b"%PDF-1.4 homogenization rev3"
    name = "SOP-310_Homogenization_rev3.pdf"
    FOLDERS["sop"][name] = data
    reply = report(srv, "sop")
    assert [n["path"] for n in reply["need"]] == ["%s/%s" % (DIRS["sop"], name)] and reply["pending"] >= 1
    item = waiting(admin, "sop", identity="SOP-310")[0]
    assert item["change"] == "new" and item["parsed"]["docNumber"] == "SOP-310" and not item["hasFile"]
    r = accept(admin, item)                                                                  # the file has not arrived yet
    assert r.status == 409 and r.json["code"] == "file_pending"
    assert upload(srv, "sop", reply["need"][0]["path"], data) == (200, {"ok": True, "stored": True})
    assert upload(srv, "sop", reply["need"][0]["path"], data) == (200, {"ok": True, "stored": False})     # sending it twice is harmless
    assert waiting(admin, "sop", identity="SOP-310")[0]["hasFile"]
    assert admin.get("/api/library/inbox/%d/file" % item["id"]).body == data                 # the administrator can look at it before deciding
    r = accept(admin, item, name="Homogenization SOP")
    assert r.status == 200, r.text
    sop = [s for s in docs(plain)["sops"] if s["docNumber"] == "SOP-310"][0]
    assert (sop["name"], sop["revision"], sop["status"], sop["hasFile"]) == ("Homogenization SOP", "3", "current", True)
    assert plain.get("/api/sop-documents/%d/download" % sop["id"]).body == data
    assert not waiting(admin, "sop", id=item["id"])
    assert report(srv, "sop")["need"] == [] and not waiting(admin, "sop", identity="SOP-310")  # the next night: nothing to say
    assert sql(srv, "SELECT COUNT(*) FROM sop_document_edits WHERE sop_id=? AND field LIKE 'Added from%'", (sop["id"],))[0][0] == 1
    assert sql(srv, "SELECT stored_name FROM library_inbox WHERE id=?", (item["id"],))[0][0] is None


def test_a_name_that_is_already_taken_is_refused(srv, admin):
    put(srv, "sop", "SOP-311_Twin_rev1.pdf", b"%PDF twin")
    item = waiting(admin, "sop", identity="SOP-311")[0]
    assert accept(admin, item, name="Homogenization SOP").status == 409                      # used by the previous test's document
    assert accept(admin, item, name="Twin Procedure").status == 200


def test_a_file_can_be_linked_to_an_sop_that_already_exists(srv, admin, plain):
    wet = [s for s in docs(admin)["sops"] if s["key"] == "wet_solids_sop"][0]
    assert wet["docNumber"] is None
    data = b"%PDF the controlled copy"
    put(srv, "sop", "SOP002_Solid_Liquid_Ratio_&_Density_Analysis-compressed.pdf", data)
    item = waiting(admin, "sop", identity="SOP-002")[0]
    assert {"id": wet["id"], "name": wet["name"]} in item["candidates"]
    assert accept(admin, item, linkTo=wet["id"]).status == 200
    now = [s for s in docs(plain)["sops"] if s["id"] == wet["id"]][0]
    assert (now["docNumber"], now["key"], now["name"], now["hasFile"]) == ("SOP-002", "wet_solids_sop", "Determining % Wet Solids SOP", True)   # the QC link keeps working
    refdata = {s["key"]: s for s in plain.get("/api/refdata").json["sops"] if s["key"]}
    assert refdata["wet_solids_sop"]["hasFile"] and refdata["wet_solids_sop"]["docNumber"] == "SOP-002"
    assert plain.get("/api/sop-documents/%d/download" % wet["id"]).body == data


# ---- a new revision, a removed file, a returning file ----

def test_a_new_revision_replaces_the_file_and_keeps_the_old_one(srv, admin, plain):
    old, new = b"%PDF-1.4 revision one", b"%PDF-1.4 revision two, longer"
    add_sop(srv, admin, 320, "Extraction SOP", 1, old, "Extraction")
    doc = [s for s in docs(plain)["sops"] if s["docNumber"] == "SOP-320"][0]
    FOLDERS["sop"].pop("SOP-320_Extraction_rev1.pdf")                                        # QA moves rev1 to Superseded and drops rev2 in Current
    reply = put(srv, "sop", "SOP-320_Extraction_rev2.pdf", new)
    assert reply["need"]
    item = waiting(admin, "sop", identity="SOP-320")[0]
    assert item["change"] == "revision" and item["targetId"] == doc["id"] and item["targetName"] == "Extraction SOP"
    assert not waiting(admin, "sop", identity="SOP-320", change="missing")                   # (the old name leaving is not a removal: its replacement is waiting)
    assert plain.get("/api/sop-documents/%d/download" % doc["id"]).body == old               # nothing changes until it is accepted
    assert accept(admin, item).status == 200
    cur = [s for s in docs(plain)["sops"] if s["id"] == doc["id"]][0]
    assert cur["revision"] == "2" and plain.get("/api/sop-documents/%d/download" % doc["id"]).body == new
    hist = plain.get("/api/documents/sop/%d/history" % doc["id"]).json
    assert len(hist["revisions"]) == 1 and hist["revisions"][0]["revision"] == "1" and hist["revisions"][0]["supersededBy"]
    assert plain.get("/api/doc-revisions/%d/download" % hist["revisions"][0]["id"]).body == old
    assert {e["field"] for e in hist["edits"]} >= {"File", "Revision"}


def test_a_removed_file_withdraws_the_document_and_it_can_come_back(srv, admin, plain):
    b = b"%PDF remove me"
    add_sop(srv, admin, 330, "Stays procedure", 1, b"%PDF keep me", "Stays")
    gone_name = add_sop(srv, admin, 331, "Goes procedure", 1, b, "Goes")
    reply = remove(srv, "sop", gone_name)                                                    # SOP-331's file is gone from the folder
    gone = waiting(admin, "sop", change="missing")
    assert [g["identity"] for g in gone] == ["SOP-331"] and reply["pending"] == 1
    assert accept(admin, gone[0]).status == 200
    doc = [s for s in docs(plain)["sops"] if s["docNumber"] == "SOP-331"][0]
    assert doc["status"] == "withdrawn" and doc["hasFile"]                                   # kept, and marked
    assert plain.get("/api/sop-documents/%d/download" % doc["id"]).body == b                # (its last controlled copy is still readable)
    assert [s for s in plain.get("/api/refdata").json["sops"] if s["id"] == doc["id"]][0]["status"] == "withdrawn"
    put(srv, "sop", gone_name, b)                                                            # the file returns
    item = waiting(admin, "sop", identity="SOP-331")[0]
    assert item["change"] == "revision" and "withdrawn" in item["detail"]
    assert accept(admin, item).status == 200
    assert [s for s in docs(plain)["sops"] if s["docNumber"] == "SOP-331"][0]["status"] == "current"


def test_an_empty_folder_is_never_read_as_everything_removed(srv, admin):
    saved = dict(FOLDERS["sop"])
    FOLDERS["sop"].clear()
    reply = report(srv, "sop")
    assert any("looks empty" in w for w in reply["warnings"])
    assert not waiting(admin, "sop", change="missing")
    FOLDERS["sop"].update(saved)
    sync(srv, "sop")


def test_the_same_file_under_a_new_name_is_recorded_quietly(srv, admin, plain):
    data = b"%PDF renamed only"
    old_name = add_sop(srv, admin, 340, "Rename procedure", 1, data, "Rename")
    n = len(pending(admin))
    FOLDERS["sop"].pop(old_name)
    reply = put(srv, "sop", "SOP-340_Rename_rev2.pdf", data)
    assert reply["need"] == [] and len(pending(admin)) == n                                  # same content: no review needed
    doc = [s for s in docs(plain)["sops"] if s["docNumber"] == "SOP-340"][0]
    assert doc["revision"] == "2" and doc["sourcePath"].endswith("SOP-340_Rename_rev2.pdf")


# ---- files KelpWorks cannot read, ignored items, files that vanish ----

def test_an_unreadable_name_can_only_be_ignored_and_stays_ignored(srv, admin):
    name, data = "Procedure for tanks (final).pdf", b"%PDF oddly named"
    put(srv, "sop", name, data)
    item = waiting(admin, "sop", change="unreadable")[0]
    assert "SOP-" in item["detail"]
    r = accept(admin, item)
    assert r.status == 400 and "Rename it" in r.json["error"]
    assert admin.post("/api/library/inbox/%d/ignore" % item["id"], {"reason": "a draft"}).status == 200
    sync(srv, "sop")
    assert not waiting(admin, "sop", change="unreadable")                                    # the same file does not come back
    put(srv, "sop", name, data + b" changed")
    assert waiting(admin, "sop", change="unreadable")                                        # a changed file does
    assert admin.post("/api/library/inbox/%d/ignore" % item["id"], {}).status == 409         # already dealt with
    FOLDERS["sop"].pop(name)
    report(srv, "sop")
    assert not waiting(admin, "sop", change="unreadable")                                    # and it goes when the file does


def test_a_waiting_file_that_leaves_the_folder_is_dropped(srv, admin):
    name = "SOP-350_Visitor_rev1.pdf"
    put(srv, "sop", name, b"%PDF brief visitor")
    item = waiting(admin, "sop", identity="SOP-350")[0]
    stored = sql(srv, "SELECT stored_name FROM library_inbox WHERE id=?", (item["id"],))[0][0]
    path = os.path.join(srv.tmp, "library_inbox", stored)
    assert os.path.isfile(path)
    remove(srv, "sop", name)
    assert not waiting(admin, "sop", identity="SOP-350")
    assert not os.path.exists(path)                                                          # its waiting copy is deleted
    assert sql(srv, "SELECT state FROM library_inbox WHERE id=?", (item["id"],))[0][0] == "gone"


def test_uploads_are_checked(srv, admin):
    data = b"%PDF checked"
    name = "SOP-360_Checks_rev1.pdf"
    FOLDERS["sop"][name] = data
    report(srv, "sop")
    path = "%s/%s" % (DIRS["sop"], name)
    assert upload(srv, "sop", path, data, sha="0" * 64)[0] == 400                            # damaged on the way
    assert upload(srv, "sop", path, data + b"x")[0] == 404                                   # not a file KelpWorks is waiting for
    assert upload(srv, "sop", path, data, key="wrong-wrong-wrong-wrong-wrong")[0] == 403
    try:                                                                                     # too big: refused from the size alone, before the body is read
        assert upload(srv, "sop", path, b"x" * (K.MAX_UPLOAD_BYTES + 1))[0] == 413
    except (ConnectionError, urllib.error.URLError):                                         # (the server may hang up while the client is still sending)
        pass
    assert upload(srv, "sop", path, data)[0] == 200
    FOLDERS["sop"].pop(name)
    report(srv, "sop")


# ---- safety data sheets ----

def test_a_safety_data_sheet_is_added_linked_to_items_and_reviewed(srv, admin, plain):
    data = b"%PDF-1.4 citric acid sheet"
    year = datetime.date.today().year
    old_name = "SDS_Citric-Acid_Univar_%d-01-15.pdf" % (year - 2)
    put(srv, "sds", old_name, data)
    item = waiting(admin, "sds", identity="citric-acid|univar")[0]
    assert item["parsed"]["chemical"] == "Citric Acid" and item["parsed"]["issueDate"] == "%d-01-15" % (year - 2)
    ids = [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] in ("Citric Acid", "CIP Acid")]
    assert len(ids) == 2
    assert accept(admin, item, itemIds=ids).status == 200
    sds = [s for s in docs(plain)["sds"] if s["chemical"] == "Citric Acid"][0]
    assert sds["supplier"] == "Univar" and sds["status"] == "current" and sorted(i["id"] for i in sds["items"]) == sorted(ids)
    assert sds["reviewDue"] == "%d-01-15" % (year + 1) and sds["reviewStatus"] in ("ok", "soon")
    assert plain.get("/api/sds-documents/%d/download" % sds["id"]).body == data
    linked = {c["name"]: c["sds"] for c in plain.get("/api/consumables").json["consumables"]}
    assert linked["Citric Acid"] == [{"id": sds["id"], "chemical": "Citric Acid", "supplier": "Univar"}] and linked["Sodium Benzoate"] == []
    # a newer issue of the same sheet is a revision: the issue date moves, the item links stay, the old file is kept
    new = b"%PDF-1.4 citric acid sheet, newer issue"
    FOLDERS["sds"].pop(old_name)
    put(srv, "sds", "SDS_Citric-Acid_Univar_%d-06-01.pdf" % year, new)
    rev = waiting(admin, "sds", identity="citric-acid|univar")[0]
    assert rev["change"] == "revision" and rev["targetId"] == sds["id"] and accept(admin, rev).status == 200
    sds2 = [s for s in docs(plain)["sds"] if s["id"] == sds["id"]][0]
    assert sds2["issueDate"] == "%d-06-01" % year and len(sds2["items"]) == 2 and plain.get("/api/sds-documents/%d/download" % sds["id"]).body == new
    hist = plain.get("/api/documents/sds/%d/history" % sds["id"]).json
    assert len(hist["revisions"]) == 1 and plain.get("/api/doc-revisions/%d/download" % hist["revisions"][0]["id"]).body == data
    assert {e["field"] for e in hist["edits"]} >= {"Issue date", "Added from the SharePoint folder"}


def test_an_old_sheet_is_overdue_and_items_can_be_relinked_by_administrators_only(srv, admin, plain):
    put(srv, "sds", "SDS_Potassium-Sorbate_Univar_2019-05-20.pdf", b"%PDF old sheet")
    assert accept(admin, waiting(admin, "sds", identity="potassium-sorbate|univar")[0]).status == 200
    sds = [s for s in docs(plain)["sds"] if s["chemical"] == "Potassium Sorbate"][0]
    assert sds["reviewStatus"] == "overdue" and sds["reviewDue"] == "2022-05-20" and sds["items"] == []
    item_id = [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == "Potassium Sorbate"][0]
    assert plain.put("/api/sds-documents/%d" % sds["id"], {"itemIds": [item_id]}).status == 403
    r = admin.put("/api/sds-documents/%d" % sds["id"], {"itemIds": [item_id], "notes": "Reviewed by QA"})
    assert r.status == 200 and [i["id"] for i in r.json["sds"]["items"]] == [item_id] and r.json["sds"]["notes"] == "Reviewed by QA"
    hist = admin.get("/api/documents/sds/%d/history" % sds["id"]).json["edits"]
    assert {"Used for (inventory items)", "Notes"} <= {e["field"] for e in hist}


def test_a_withdrawn_sheet_is_no_longer_due_for_review(srv, admin, plain):
    name = "SDS_Acetone_Fisher_2018-01-01.pdf"
    put(srv, "sds", name, b"%PDF acetone")
    assert accept(admin, waiting(admin, "sds", identity="acetone|fisher")[0]).status == 200
    assert [s for s in docs(plain)["sds"] if s["chemical"] == "Acetone"][0]["reviewStatus"] == "overdue"
    remove(srv, "sds", name)
    assert accept(admin, waiting(admin, "sds", change="missing")[0]).status == 200
    sheet = [s for s in docs(plain)["sds"] if s["chemical"] == "Acetone"][0]
    assert sheet["status"] == "withdrawn" and sheet["reviewStatus"] is None and sheet["hasFile"]


def test_two_issues_of_one_sheet_in_the_folder_are_flagged(srv, admin):
    put(srv, "sds", "SDS_Sodium-Benzoate_Univar_2024-02-01.pdf", b"%PDF sb 1")
    assert accept(admin, waiting(admin, "sds", identity="sodium-benzoate|univar")[0]).status == 200
    put(srv, "sds", "SDS_Sodium-Benzoate_Univar_2025-02-01.pdf", b"%PDF sb 2")
    items = waiting(admin, "sds", identity="sodium-benzoate|univar", change="revision")
    assert len(items) == 1
    FOLDERS["sds"]["SDS_Sodium-Benzoate_Univar_2024-02-01.pdf"] = b"%PDF sb 1 edited"       # QA edits the old file instead of moving it: two files, one sheet
    report(srv, "sds")
    flagged = waiting(admin, "sds", identity="sodium-benzoate|univar", change="revision")
    assert len(flagged) == 2 and all("Another file" in (i["detail"] or "") for i in flagged)
    for i in flagged:
        assert admin.post("/api/library/inbox/%d/ignore" % i["id"], {}).status == 200


# ---- the page's status, backups, guards ----

def test_the_folders_report_when_they_were_last_checked(srv, admin):
    info = inbox(admin)
    assert info["scans"]["sop"]["at"] and info["scans"]["sop"]["stale"] is False and info["staleDays"] == 3
    sql(srv, "UPDATE library_scans SET scanned_at='2020-01-01T00:00:00Z' WHERE kind='sds'")
    assert inbox(admin)["scans"]["sds"]["stale"] is True
    sync(srv, "sds")
    assert inbox(admin)["scans"]["sds"]["stale"] is False


def test_safety_data_sheets_and_revisions_are_in_the_full_backup(srv, admin):
    full = admin.get("/api/admin/backup?full=1")
    assert full.status == 200
    z = zipfile.ZipFile(io.BytesIO(full.body))
    manifest = json.loads(z.read("manifest.json").decode())
    assert manifest["files"]["sds_documents"] >= 2 and manifest["files"]["sop_documents"] >= 3
    assert all(n.startswith(("kelp_erp.db", "manifest.json", "uploads/", "lab_templates/", "sop_documents/", "sds_documents/")) for n in z.namelist())


def test_an_unknown_document_status_is_refused_by_the_database(srv):
    with pytest.raises(sqlite3.IntegrityError, match="Not allowed"):
        sql(srv, "UPDATE sds_documents SET status='lost'")
    with pytest.raises(sqlite3.IntegrityError, match="Not allowed"):
        sql(srv, "UPDATE sop_documents SET status='lost'")


def test_deleting_an_sop_removes_its_kept_revisions_too(srv, admin):
    add_sop(srv, admin, 370, "Delete me", 1, b"%PDF first", "Delete")
    doc = [s for s in docs(admin)["sops"] if s["docNumber"] == "SOP-370"][0]
    FOLDERS["sop"].pop("SOP-370_Delete_rev1.pdf")
    put(srv, "sop", "SOP-370_Delete_rev2.pdf", b"%PDF second")
    accept(admin, waiting(admin, "sop", identity="SOP-370")[0])
    kept = sql(srv, "SELECT stored_name FROM document_revisions WHERE kind='sop' AND doc_id=?", (doc["id"],))[0][0]
    assert os.path.isfile(os.path.join(srv.tmp, "sop_documents", kept))
    assert admin.delete("/api/sop-documents/%d" % doc["id"]).status == 200
    assert not os.path.exists(os.path.join(srv.tmp, "sop_documents", kept))
    assert sql(srv, "SELECT COUNT(*) FROM document_revisions WHERE kind='sop' AND doc_id=?", (doc["id"],))[0][0] == 0
    FOLDERS["sop"].pop("SOP-370_Delete_rev2.pdf")
    report(srv, "sop")


# ---- the sync script, folder -> KelpWorks ----

def run_tool(server, root, *extra, key=KEY, skip=("--skip-runs", "--skip-backups")):
    env = dict(os.environ, KELPWORKS_ARCHIVE_KEY=key, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, TOOL, "--url", server.url, "--root", str(root)] + list(skip) + list(extra), capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout + p.stderr


def test_the_script_reports_both_folders_and_uploads_what_is_new(tmp_path):
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as own:                                   # its own server: its folder is the only one this server knows
        admin = own.admin_client()
        root = tmp_path / "lib"
        (root / DIRS["sop"]).mkdir(parents=True)
        (root / DIRS["sds"]).mkdir(parents=True)
        (root / DIRS["sop"] / "SOP-400_From-The-PC_rev1.pdf").write_bytes(b"%PDF from the pc")
        (root / DIRS["sop"] / "~$SOP-400_From-The-PC_rev1.pdf").write_bytes(b"lock file")      # Word's temporary file is not a document
        (root / DIRS["sop"] / "notes.txt.tmp").write_bytes(b"x")
        (root / DIRS["sds"] / "SDS_Acetone_Fisher_2025-04-01.pdf").write_bytes(b"%PDF acetone")
        (root / DIRS["sds"] / "random name.pdf").write_bytes(b"%PDF unreadable")
        code, out = run_tool(own, root, "--dry-run")
        assert code == 0 and "would report 1 file(s)" in out and "would report 2 file(s)" in out
        assert pending(admin) == []                                                          # a dry run reports nothing
        code, out = run_tool(own, root)
        assert code == 0 and "SYNC OK" in out and "SOPs: 1 file(s) in the folder, 1 sent" in out, out
        got = pending(admin)
        assert sorted((i["kind"], i["change"], i["hasFile"]) for i in got) == [("sds", "new", True), ("sds", "unreadable", False), ("sop", "new", True)]
        code, out = run_tool(own, root)
        assert code == 0 and "0 sent" in out, out                                            # the second night uploads nothing again
        assert accept(admin, [i for i in got if i["kind"] == "sop"][0], name="From the PC").status == 200
        (root / DIRS["sop"] / "SOP-400_From-The-PC_rev1.pdf").write_bytes(b"%PDF from the pc, edited")
        code, out = run_tool(own, root)
        assert code == 0 and "1 sent" in out                                                 # an edited file becomes a revision
        assert waiting(admin, "sop", identity="SOP-400", change="revision")


def test_the_script_warns_about_a_missing_folder_and_a_wrong_key(srv, tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    code, out = run_tool(srv, root)
    assert code == 0 and "does not exist here" in out
    (root / DIRS["sop"]).mkdir(parents=True)
    code, out = run_tool(srv, root, key="wrong-wrong-wrong-wrong-wrong")
    assert code == 1 and "FAILED" in out and "403" in out
