"""KW-01: notes on a production run. Each carries the log section it belongs to, can have pictures, is part of the signed log, and (like every log entry) can be added, changed
or removed while the log is open (a draft, or an open amendment) but not once the run is finalized."""
import base64

import pytest

import kelp_erp_server as K
from factories import feedstock_detail, finalize_run, finished_run, harvest_totes, sql
from harness import Server

PNG = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6300010000050001") + b"\x00" * 20).decode()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def draft(admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    return rid


def notes(admin, rid):
    return admin.get("/api/production/%d/notes" % rid).json["notes"]


def test_a_note_is_saved_with_its_section_and_listed(admin):
    rid = draft(admin)
    r = admin.post("/api/production/%d/notes" % rid, {"section": "homogenization", "body": "  Homogenizer tripped twice at 10:15, restarted  "})
    assert r.status == 200, r.text
    n = notes(admin, rid)
    assert len(n) == 1 and n[0]["section"] == "homogenization" and n[0]["sectionLabel"] == "Homogenization"
    assert n[0]["body"] == "Homogenizer tripped twice at 10:15, restarted" and n[0]["by"] and n[0]["images"] == []
    assert [s["key"] for s in admin.get("/api/production/%d/notes" % rid).json["sections"]] == [k for k, _l in K.NOTE_SECTIONS]


def test_bad_notes_are_refused(admin):
    rid = draft(admin)
    url = "/api/production/%d/notes" % rid
    assert admin.post(url, {"section": "nowhere", "body": "x"}).status == 400
    assert admin.post(url, {"section": "general", "body": "   "}).status == 400
    assert admin.post(url, {"section": "general", "body": "x" * (K.NOTE_MAX_CHARS + 1)}).status == 400
    assert admin.post("/api/production/999999/notes", {"section": "general", "body": "x"}).status == 404


def test_a_note_can_be_edited_and_removed_while_the_log_is_open(admin):
    rid = draft(admin)
    nid = admin.post("/api/production/%d/notes" % rid, {"section": "general", "body": "first"}).json["id"]
    r = admin.put("/api/production/%d/notes/%d" % (rid, nid), {"section": "extraction", "body": "second"})
    assert r.status == 200
    n = notes(admin, rid)[0]
    assert (n["section"], n["body"]) == ("extraction", "second") and n["editedBy"] and n["editedAt"]
    assert admin.delete("/api/production/%d/notes/%d" % (rid, nid)).status == 200
    assert notes(admin, rid) == []


def test_pictures_attach_to_a_note_and_go_with_it(srv, admin):
    rid = draft(admin)
    nid = admin.post("/api/production/%d/notes" % rid, {"section": "separation", "body": "screen torn"}).json["id"]
    img = "/api/production/%d/notes/%d/images" % (rid, nid)
    assert admin.post(img, {"filename": "screen.png", "contentType": "image/png", "dataB64": PNG}).status == 200
    assert admin.post(img, {"filename": "log.pdf", "contentType": "application/pdf", "dataB64": PNG}).status == 400            # pictures only
    n = notes(admin, rid)[0]
    assert [i["filename"] for i in n["images"]] == ["screen.png"]
    aid = n["images"][0]["id"]
    dl = admin.get("/api/production/%d/attachments/%d/download" % (rid, aid))
    assert dl.status == 200
    assert admin.delete("/api/production/%d/notes/%d/images/%d" % (rid, nid, aid)).status == 200
    assert notes(admin, rid)[0]["images"] == [] and sql(srv, "SELECT COUNT(*) FROM run_attachments WHERE id=?", (aid,))[0][0] == 0
    admin.post(img, {"filename": "again.png", "contentType": "image/png", "dataB64": PNG}).raise_for_status()
    aid2 = notes(admin, rid)[0]["images"][0]["id"]
    admin.delete("/api/production/%d/notes/%d" % (rid, nid)).raise_for_status()                                                 # removing the note removes its pictures
    assert sql(srv, "SELECT COUNT(*) FROM run_attachments WHERE id=?", (aid2,))[0][0] == 0


def test_a_picture_limit_per_note(admin):
    rid = draft(admin)
    nid = admin.post("/api/production/%d/notes" % rid, {"section": "general", "body": "many"}).json["id"]
    img = "/api/production/%d/notes/%d/images" % (rid, nid)
    for i in range(K.NOTE_MAX_IMAGES):
        assert admin.post(img, {"filename": "p%d.png" % i, "dataB64": PNG}).status == 200
    assert admin.post(img, {"filename": "one-too-many.png", "dataB64": PNG}).status == 400


def test_notes_are_part_of_the_signed_log_only_when_there_are_any(srv, admin):
    plain = finished_run(admin)
    run = [r for r in admin.get("/api/production").json["runs"] if r["id"] == plain][0]
    assert "logNotes" not in run                                                                     # nothing new in a log without notes
    rid = draft(admin)
    nid = admin.post("/api/production/%d/notes" % rid, {"section": "packaging", "body": "label jam"}).json["id"]
    admin.post("/api/production/%d/notes/%d/images" % (rid, nid), {"filename": "jam.png", "dataB64": PNG}).raise_for_status()
    totes = [t for t in admin.get("/api/production/drafts").json["drafts"] if t["id"] == rid][0]["toteIds"]
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).ok
    run = [r for r in admin.get("/api/production").json["runs"] if r["id"] == rid][0]
    assert [n["body"] for n in run["logNotes"]] == ["label jam"] and "images" not in run["logNotes"][0] and run["logNoteImages"][str(nid)][0]["filename"] == "jam.png"
    assert "logNoteImages" in K.Handler._RELEASE_HASH_SKIP and "logNotes" not in K.Handler._RELEASE_HASH_SKIP


def test_a_finalized_run_needs_an_amendment_to_change_its_notes(srv, admin):
    rid = finished_run(admin)
    url = "/api/production/%d/notes" % rid
    r = admin.post(url, {"section": "general", "body": "late thought"})
    assert r.status == 409 and r.json.get("code") == "amendment_required"
    am, info = srv.make_user(role="user", canAmendLog=True)
    assert am.post("/api/production/%d/amendments" % rid, {"category": "late_entry", "reason": "add a missing note", "password": info["password"]}).status == 200
    assert am.post(url, {"section": "extraction", "body": "flow cell blocked, cleared"}).status == 200
    opened = am.get("/api/production/%d/amendments" % rid).json["open"]
    pv = am.get("/api/production/%d/amendments/%d/preview" % (rid, opened["id"])).json
    assert any("Note" in c["field"] for c in pv["changes"]), pv["changes"]                           # the new note is a recorded change
    assert [n["body"] for n in notes(am, rid)] == ["flow cell blocked, cleared"]


def test_the_production_summary_lists_the_notes(srv, admin):
    rid = draft(admin)
    admin.post("/api/production/%d/notes" % rid, {"section": "extraction", "body": "Pump seal replaced at 11:20"}).raise_for_status()
    totes = [t for t in admin.get("/api/production/drafts").json["drafts"] if t["id"] == rid][0]["toteIds"]
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).ok
    pdf = admin.get("/api/production/%d/summary.pdf" % rid)
    assert pdf.status == 200
    text = " ".join(" ".join(str(x) for x in (l if isinstance(l, (list, tuple)) else [l])) for l in K.pdf_text_lines(pdf.body))
    assert "Pump seal replaced at 11:20" in text and "Extraction" in text, text[:1500]
