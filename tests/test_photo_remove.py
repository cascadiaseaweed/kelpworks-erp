"""KW-02: a feedstock photo of a run that is still being logged can be viewed full size, removed and replaced -- the old file is deleted from the disk -- and it is locked once the run is
finalized (and cannot be pulled out of a finalized log through the Documents list either)."""
import base64
import json
import os
import pathlib

import pytest

from factories import feedstock_detail, finalize_run, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"
IMG = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def stored_path(srv, aid):
    rows = sql(srv, "SELECT stored_name FROM run_attachments WHERE id=?", (aid,))
    return os.path.join(srv.tmp, "uploads", rows[0][0]) if rows else None


def upload(admin, rid, slot="surface", **kw):
    r = admin.post("/api/production/%d/feedstock-photo" % rid, dict({"slot": slot, "filename": "tote.jpg", "contentType": "image/jpeg", "dataB64": IMG}, **kw))
    assert r.status == 200, r.text
    return r.json["attachmentId"]


def draft_with_photos(admin):
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    return rid, totes[0]


def test_a_photo_can_be_removed_from_a_draft_and_its_file_is_deleted(srv, admin):
    rid, tote = draft_with_photos(admin)
    aid = upload(admin, rid)
    fd = dict(feedstock_detail(), surfacePhotoId=aid)
    assert admin.post("/api/production/%d/feedstock/%d/save" % (rid, tote), fd).status == 200          # the photo is now on the tote's run input
    path = stored_path(srv, aid)
    assert os.path.exists(path) and sql(srv, "SELECT surface_photo FROM run_inputs WHERE run_id=?", (rid,))[0][0] == str(aid)
    r = admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, aid))
    assert r.status == 200, r.text
    assert not os.path.exists(path) and not sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (aid,))               # the file is gone from the disk
    assert sql(srv, "SELECT surface_photo FROM run_inputs WHERE run_id=?", (rid,))[0][0] is None                       # and nothing points to it
    log = sql(srv, "SELECT old_value, new_value FROM tote_stability_log WHERE tote_lot_id=? AND field='Surface photo' ORDER BY id DESC LIMIT 1", (tote,))
    assert log == [("tote.jpg", "(removed)")]
    assert admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, aid)).status == 404


def test_replacing_a_photo_deletes_the_old_one_and_points_to_the_new_one(srv, admin):
    rid, tote = draft_with_photos(admin)
    old = upload(admin, rid)
    admin.post("/api/production/%d/feedstock/%d/save" % (rid, tote), dict(feedstock_detail(), surfacePhotoId=old)).raise_for_status()
    old_path = stored_path(srv, old)
    new = upload(admin, rid, replaces=old)
    assert new != old and not os.path.exists(old_path) and not sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (old,))
    assert os.path.exists(stored_path(srv, new))
    assert sql(srv, "SELECT surface_photo FROM run_inputs WHERE run_id=?", (rid,))[0][0] == str(new)
    # a photo that is only in the draft's saved details (the card was not saved yet) is replaced the same way
    other = upload(admin, rid, slot="striation")
    dd = json.loads(sql(srv, "SELECT draft_data FROM production_runs WHERE id=?", (rid,))[0][0])
    dd.setdefault("feedstockDetails", {}).setdefault(str(tote), {})["striationPhotoId"] = other
    sql(srv, "UPDATE production_runs SET draft_data=? WHERE id=?", (json.dumps(dd), rid))
    newer = upload(admin, rid, slot="striation", replaces=other)
    dd = json.loads(sql(srv, "SELECT draft_data FROM production_runs WHERE id=?", (rid,))[0][0])
    assert dd["feedstockDetails"][str(tote)]["striationPhotoId"] == newer and not sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (other,))


def test_a_document_that_is_not_a_feedstock_photo_cannot_be_removed_this_way_or_replaced(srv, admin):
    rid, _ = draft_with_photos(admin)
    doc = admin.post("/api/production/%d/attachments" % rid, {"filename": "notes.pdf", "contentType": "application/pdf", "dataB64": base64.b64encode(b"%PDF-1.4 x").decode()})
    doc_id = max(a["id"] for a in doc.json["attachments"])
    assert admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, doc_id)).status == 409
    upload(admin, rid, replaces=doc_id)                                                            # asking to replace an ordinary document replaces nothing
    assert sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (doc_id,))
    assert admin.delete("/api/production/%d/attachments/%d" % (rid, doc_id)).status == 200         # the Documents list still removes ordinary documents


def test_a_finalized_runs_photos_are_locked(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1))
    assert f.status == 200
    aid = sql(srv, "SELECT surface_photo FROM run_inputs WHERE run_id=?", (rid,))[0][0]
    # the helper's photo ids are placeholders; give the run a real photo attachment so the lock is about a real file
    real = sql(srv, "INSERT INTO run_attachments (run_id,filename,content_type,size,stored_name,uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?,?)",
               (rid, "tote.jpg", "image/jpeg", 10, "locked-test.jpg", "t", "2026-05-04T00:00:00Z"))
    aid = sql(srv, "SELECT MAX(id) FROM run_attachments")[0][0]
    sql(srv, "UPDATE run_inputs SET surface_photo=? WHERE run_id=?", (str(aid), rid))
    r = admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, aid))
    assert r.status == 409 and r.json.get("code") in ("photo_locked", "amendment_required"), r.text      # the production log lock stops it first
    r = admin.delete("/api/production/%d/attachments/%d" % (rid, aid))                              # nor through the Documents list
    assert r.status == 409 and r.json.get("code") == "photo_locked", r.text
    assert sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (aid,))


def test_the_card_offers_view_replace_and_remove_only_while_the_run_is_in_progress():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")
    assert "Click to view full size" in js and "removePhoto: async (slot, attId)" in js and "replaces: replaces || null" in js
    flat = " ".join(js.split())
    assert flat.count("removePhoto: async") == 1            # only the in-progress run's card passes it: the finalized run's card and Feedstock Inventory do not


def test_a_photo_that_was_taken_but_not_yet_saved_can_be_replaced_and_removed_too(srv, admin):
    rid, _ = draft_with_photos(admin)
    first = upload(admin, rid)                                         # just uploaded: nothing points to it yet
    p1 = stored_path(srv, first)
    second = upload(admin, rid, replaces=first)
    assert not os.path.exists(p1) and not sql(srv, "SELECT 1 FROM run_attachments WHERE id=?", (first,))
    p2 = stored_path(srv, second)
    assert admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, second)).status == 200
    assert not os.path.exists(p2)
