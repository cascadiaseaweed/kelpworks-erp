"""KW-102: a production run's feedstock card carries the tote's pictures into the run (the run keeps its OWN COPY) and never starts the measured Weight (kg) from the harvest
average. The tote's newest Surface / Settling-striation picture is offered as a SOURCE marker by GET /api/totes/<id>/ph?forRun=1; the copy is made when the entries are locked in
(the card's Save, Save & close, Finalize), so the autosave stays side-effect free and no draft is created behind anyone's back."""
import base64
import os
from pathlib import Path

import pytest

from factories import feedstock_detail, harvest_totes, sql, stage_payloads
from harness import Server

APP_JS = (Path(__file__).resolve().parent.parent / "public" / "app.js").read_text(encoding="utf-8")
IMG_A = b"\x89PNG-surface-" + b"A" * 2048
IMG_B = b"\x89PNG-striation-" + b"B" * 2048
IMG_C = b"\x89PNG-replacement-" + b"C" * 2048


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def b64(data):
    return base64.b64encode(data).decode()


def tote_photo(client, tote, slot, data, name):
    """A picture taken in Feedstock Inventory's Update window: stored with the tote and logged in its Feedstock Stability log."""
    r = client.post("/api/totes/%d/photo" % tote, {"slot": slot, "filename": name, "contentType": "image/png", "dataB64": b64(data)})
    assert r.status == 200, r.text
    att = r.json["attachmentId"]
    c = client.post("/api/totes/%d/characterize" % tote, {"surfacePhotoId" if slot == "surface" else "striationPhotoId": att, "decision": "accepted"})
    assert c.status == 200, c.text
    return att


def new_draft(client):
    r = client.post("/api/production/drafts", {"sku": "KELPIVEX", "toteIds": [], "feedstockDetails": {}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"})
    assert r.status == 200, r.text
    return r.json["run"]["id"]


def card(**extra):
    """What the run card sends: the usual entries, no run photo yet, and the sources of the tote's pictures."""
    body = dict(feedstock_detail(), surfacePhotoId=None, striationPhotoId=None)
    body.update(extra)
    return body


def lock_in(client, rid, tote, **extra):
    return client.post("/api/production/%d/feedstock/%d/save" % (rid, tote), card(**extra))


def run_photos(srv, rid):
    return sql(srv, "SELECT id, filename, stored_name, purpose FROM run_attachments WHERE run_id=? ORDER BY id", (rid,))


def uploaded(srv, stored):
    return os.path.isfile(os.path.join(srv.tmp, "uploads", stored))


def test_the_run_card_is_offered_the_totes_pictures_but_never_a_weight_or_density(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    assert admin.post("/api/totes/%d/characterize" % t, {"weightKg": 150, "volumeL": 900, "densityKgL": 0.167, "odourIntensity": "Mild", "decision": "accepted"}).status == 200
    assert admin.post("/api/totes/set-bulk", {"ids": [t], "avgWeightKg": 187.5, "note": "weighed"}).status == 200                # the HARVEST average goes in the same 'Weight (kg)' log lines
    plain = admin.get("/api/totes/%d/ph" % t).json["latestCharacterization"]
    for_run = admin.get("/api/totes/%d/ph?forRun=1" % t).json["latestCharacterization"]
    assert plain["weightKg"] == 187.5 and "surfacePhotoFrom" not in plain                                          # Feedstock Inventory's own window is unchanged
    assert "weightKg" not in for_run and "densityKgL" not in for_run                                               # a run's measured weight starts blank
    assert for_run["volumeL"] == 900 and for_run["odourIntensity"] == "Mild"                                       # everything else still carries over
    assert for_run["surfacePhotoFrom"] == "tote:%d" % s and for_run["striationPhotoFrom"] == "tote:%d" % g
    none, = harvest_totes(admin, 1)
    assert "surfacePhotoFrom" not in admin.get("/api/totes/%d/ph?forRun=1" % none).json["latestCharacterization"]      # no picture, nothing offered


def test_feedstock_inventorys_update_window_is_offered_the_totes_pictures_and_everything_else(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    assert admin.post("/api/totes/set-bulk", {"ids": [t], "avgWeightKg": 187.5, "note": "weighed"}).status == 200
    shown = admin.get("/api/totes/%d/ph?photos=1" % t).json["latestCharacterization"]
    assert shown["surfacePhotoFrom"] == "tote:%d" % s and shown["striationPhotoFrom"] == "tote:%d" % g
    assert shown["weightKg"] == 187.5                                                                              # the Update window keeps everything it always showed (the run card is the one that drops weight)
    assert "surfacePhotoFrom" not in admin.get("/api/totes/%d/ph" % t).json["latestCharacterization"]              # and a plain read is unchanged
    # saving the window without choosing a new picture writes no photo line and changes nothing
    before = sql(srv, "SELECT COUNT(*) FROM tote_stability_log WHERE tote_lot_id=? AND field IN ('Surface photo', 'Settling/striation photo')", (t,))
    assert admin.post("/api/totes/%d/characterize" % t, {"decision": "accepted", "surfacePhotoFrom": shown["surfacePhotoFrom"], "striationPhotoFrom": shown["striationPhotoFrom"]}).status == 200
    assert sql(srv, "SELECT COUNT(*) FROM tote_stability_log WHERE tote_lot_id=? AND field IN ('Surface photo', 'Settling/striation photo')", (t,)) == before


def test_the_update_window_and_the_run_card_both_show_the_totes_pictures():
    assert APP_JS.count("fromUrl: marker => photoSourceUrl(t.id, marker)") == 2                                   # the run card and the Update window
    assert "'/ph?photos=1'" in APP_JS and "'/ph?forRun=1'" in APP_JS


def test_locking_in_copies_the_pictures_into_the_run_and_leaves_the_totes_alone(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    rid = new_draft(admin)
    mine = sql(srv, "SELECT id, stored_name FROM tote_attachments WHERE tote_lot_id=? ORDER BY id", (t,))
    r = lock_in(admin, rid, t, surfacePhotoFrom="tote:%d" % s, striationPhotoFrom="tote:%d" % g)
    assert r.status == 200 and r.json["rejected"] is False, r.text
    ids = r.json["photoIds"]
    assert isinstance(ids["surfacePhotoId"], int) and isinstance(ids["striationPhotoId"], int) and ids["surfacePhotoId"] != ids["striationPhotoId"]
    photos = run_photos(srv, rid)
    assert [(p[1], p[3]) for p in photos] == [("surf.png", "feedstock_photo"), ("stri.png", "feedstock_photo")]
    assert all(p[2] not in [m[1] for m in mine] and uploaded(srv, p[2]) for p in photos)                           # the run's own files, not the tote's
    assert sql(srv, "SELECT surface_photo, striation_photo FROM run_inputs WHERE run_id=?", (rid,)) == [(str(ids["surfacePhotoId"]), str(ids["striationPhotoId"]))]
    assert admin.get("/api/production/%d/attachments/%d/download" % (rid, ids["surfacePhotoId"])).body == IMG_A
    assert admin.get("/api/production/%d/attachments/%d/download" % (rid, ids["striationPhotoId"])).body == IMG_B
    assert sql(srv, "SELECT id, stored_name FROM tote_attachments WHERE tote_lot_id=? ORDER BY id", (t,)) == mine      # the tote's own pictures are untouched
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, s)).body == IMG_A
    assert photo_lines(srv, t, rid) == []                                                                           # an unchanged picture is not a change: no history line for the copy
    again = lock_in(admin, rid, t, surfacePhotoFrom="tote:%d" % s, striationPhotoFrom="tote:%d" % g)                # a client that still sends the marker never makes a second copy
    assert again.status == 200 and again.json["photoIds"] == ids
    assert len(run_photos(srv, rid)) == 2


def photo_lines(srv, tote, rid):
    """The history lines the run wrote about this tote's pictures: (field, From, To) of the lines with this run's number."""
    return sql(srv, "SELECT field, old_value, new_value FROM tote_stability_log WHERE tote_lot_id=? AND run_id=? AND field IN ('Surface photo', 'Settling/striation photo') AND attachment_id IS NOT NULL ORDER BY id", (tote, rid))


def test_replacing_only_the_surface_photo_logs_only_that_picture_and_shows_what_it_replaced(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    rid = new_draft(admin)
    up = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "surface-new.png", "contentType": "image/png", "dataB64": b64(IMG_C)})
    assert up.status == 200, up.text
    new = up.json["attachmentId"]
    r = lock_in(admin, rid, t, surfacePhotoId=new, striationPhotoFrom="tote:%d" % g)                               # the card: a new surface picture, the settling picture left as it was
    assert r.status == 200 and r.json["photoIds"]["surfacePhotoId"] == new and isinstance(r.json["photoIds"]["striationPhotoId"], int), r.text
    assert photo_lines(srv, t, rid) == [("Surface photo", "surf.png", "surface-new.png")]                           # one line, and From names the picture it replaced
    again = lock_in(admin, rid, t, surfacePhotoId=new, striationPhotoId=r.json["photoIds"]["striationPhotoId"])      # saving the card again changes nothing
    assert again.status == 200 and photo_lines(srv, t, rid) == [("Surface photo", "surf.png", "surface-new.png")]
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, s)).body == IMG_A                                # the tote's own pictures are untouched
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, g)).body == IMG_B


def test_a_replacement_is_logged_even_when_it_has_the_same_number_as_the_totes_picture():
    """A tote's picture and a run's photo are numbered in separate tables, so the first of each is number 1 on a fresh server: the history must still get its line."""
    with Server() as fresh:
        c = fresh.admin_client()
        t, = harvest_totes(c, 1)
        s = tote_photo(c, t, "surface", IMG_A, "surf.png")
        rid = new_draft(c)
        new = c.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "surface-new.png", "contentType": "image/png", "dataB64": b64(IMG_C)}).json["attachmentId"]
        assert s == new == 1
        assert lock_in(c, rid, t, surfacePhotoId=new).status == 200
        assert photo_lines(fresh, t, rid) == [("Surface photo", "surf.png", "surface-new.png")]


def surface_history(admin, t):
    """The tote's Surface photo history, oldest first, as the screen reads it."""
    return [e for e in reversed(admin.get("/api/totes/%d/ph" % t).json["stabilityLog"]) if e["field"] == "Surface photo"]


def shape(rows):
    return [(e["oldValue"] or None, e["newValue"]) for e in rows]


def test_a_replacement_is_one_row_in_feedstock_inventory_and_in_a_run(srv, admin):
    # in Feedstock Inventory: ONE row for the new picture (To, a link) with the one it replaced (From, a link while that file is kept)
    a, = harvest_totes(admin, 1)
    first = tote_photo(admin, a, "surface", IMG_A, "first.png")
    second = tote_photo(admin, a, "surface", IMG_C, "second.png")
    inv = surface_history(admin, a)
    assert shape(inv) == [(None, "first.png"), ("first.png", "second.png")]                                         # no separate "(replaced)" row
    assert inv[0]["attachmentId"] == first and inv[0]["available"] is True
    assert inv[1]["attachmentId"] == second and inv[1]["available"] is True and inv[1]["oldAttachmentId"] == first and inv[1]["oldAvailable"] is True
    # in a production run, when the card is saved: the same single row
    b, = harvest_totes(admin, 1)
    s = tote_photo(admin, b, "surface", IMG_A, "orig.png")
    rid = new_draft(admin)
    new = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "run-new.png", "contentType": "image/png", "dataB64": b64(IMG_B)}).json["attachmentId"]
    assert lock_in(admin, rid, b, surfacePhotoId=new).status == 200
    run = surface_history(admin, b)
    assert shape(run) == [(None, "orig.png"), ("orig.png", "run-new.png")]
    assert run[1]["attachmentId"] == new and run[1]["available"] is True and run[1]["runId"] == rid
    assert run[1]["oldAttachmentId"] == s and run[1]["oldAvailable"] is True                                        # the tote's original is kept, so From links to it


def test_replacing_a_picture_in_a_draft_run_writes_its_one_row_at_once(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "orig.png")
    rid = new_draft(admin)
    copy = lock_in(admin, rid, t, surfacePhotoFrom="tote:%d" % s).json["photoIds"]["surfacePhotoId"]
    assert shape(surface_history(admin, t)) == [(None, "orig.png")]                                                 # copying the tote's picture is not a change
    up = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "again.png", "contentType": "image/png", "dataB64": b64(IMG_C), "replaces": copy})
    assert up.status == 200, up.text
    rows = surface_history(admin, t)
    assert shape(rows) == [(None, "orig.png"), ("orig.png", "again.png")]                                            # one row, before the card is even saved: From = the run's copy, To = the new picture
    last = rows[-1]
    assert last["attachmentId"] == up.json["attachmentId"] and last["available"] is True and last["runId"] == rid
    assert last["oldAttachmentId"] == s and last["oldRunId"] is None and last["oldAvailable"] is True                # the run's copy was deleted, but it was a copy of the tote's picture: From links to that one
    assert lock_in(admin, rid, t, surfacePhotoId=up.json["attachmentId"]).status == 200                              # saving the card afterwards adds nothing
    assert shape(surface_history(admin, t)) == [(None, "orig.png"), ("orig.png", "again.png")]
    assert admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, up.json["attachmentId"])).status == 200      # removing it is its own row
    assert shape(surface_history(admin, t))[-1] == ("again.png", "(removed)")


def test_a_picture_taken_straight_in_the_run_has_no_link_once_it_is_replaced_but_keeps_its_name(srv, admin):
    t, = harvest_totes(admin, 1)                                                                                    # a tote with no picture of its own
    rid = new_draft(admin)
    first = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "taken-here.png", "contentType": "image/png", "dataB64": b64(IMG_B)}).json["attachmentId"]
    assert lock_in(admin, rid, t, surfacePhotoId=first).status == 200
    second = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "taken-again.png", "contentType": "image/png", "dataB64": b64(IMG_C), "replaces": first})
    assert second.status == 200, second.text
    rows = surface_history(admin, t)
    assert shape(rows) == [(None, "taken-here.png"), ("taken-here.png", "taken-again.png")]                         # one row for the replacement
    assert rows[1]["attachmentId"] == second.json["attachmentId"] and rows[1]["available"] is True                  # To is a link
    assert rows[1]["oldAttachmentId"] == first and rows[1]["oldRunId"] == rid and rows[1]["oldAvailable"] is False  # From: the run's copy is gone and has no twin: the name only


def test_the_photo_buttons_say_what_they_do_and_the_camera_button_opens_the_camera():
    assert "'Upload photo'" in APP_JS and "'📷 Take photo'" in APP_JS and "uploadBtn.textContent = has ? 'Replace'" not in APP_JS
    assert "function takePhotoFile()" in APP_JS and "navigator.mediaDevices.getUserMedia" in APP_JS and "facingMode: { ideal: 'environment' }" in APP_JS
    assert APP_JS.count("const shot = await takePhotoFile();") == 2                                                 # the feedstock card's slots and the Documents window
    assert "if (shot === undefined) cameraInput.click()" in APP_JS                                                  # the file input with capture is only the fallback


def test_a_picture_uploaded_after_the_tote_was_added_is_offered_when_the_card_is_drawn_again():
    assert "const recheck = chosen.filter(t => feedstockState[t.id] && !needsPrefill.includes(t)" in APP_JS
    assert "cur[fromKey] !== st[seenKey]" in APP_JS and "surfacePhotoSeen" in APP_JS                                 # a picture is offered once; one the person removed is not put back, a newer one is


def test_a_picture_replaced_in_feedstock_inventory_shows_the_previous_one_in_from(srv, admin):
    t, = harvest_totes(admin, 1)
    tote_photo(admin, t, "surface", IMG_A, "first.png")
    tote_photo(admin, t, "surface", IMG_C, "second.png")
    lines = sql(srv, "SELECT old_value, new_value FROM tote_stability_log WHERE tote_lot_id=? AND field='Surface photo' AND attachment_id IS NOT NULL ORDER BY id", (t,))
    assert [(old or None, new) for old, new in lines] == [(None, "first.png"), ("first.png", "second.png")]


def test_save_and_close_copies_them_too_and_the_draft_keeps_the_runs_ids_not_the_markers(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    rid = new_draft(admin)
    body = {"sku": "KELPIVEX", "toteIds": [t], "feedstockDetails": {str(t): card(surfacePhotoFrom="tote:%d" % s)}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.put("/api/production/drafts/%d" % rid, body)
    assert r.status == 200, r.text
    ids = r.json["photoIds"][str(t)]
    assert isinstance(ids["surfacePhotoId"], int) and ids["striationPhotoId"] is None                              # the tote has no second picture
    kept = admin.get("/api/production/drafts/%d" % rid).json["run"]["feedstockDetails"][str(t)]
    assert kept["surfacePhotoId"] == ids["surfacePhotoId"] and "surfacePhotoFrom" not in kept


def test_the_autosave_only_remembers_the_marker_and_copies_nothing(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    rid = new_draft(admin)
    r = admin.put("/api/production/drafts/%d/autosave" % rid, {"toteIds": [t], "feedstockDetails": {str(t): card(surfacePhotoFrom="tote:%d" % s)}})
    assert r.status == 200, r.text
    assert run_photos(srv, rid) == []
    assert admin.get("/api/production/drafts/%d" % rid).json["run"]["feedstockDetails"][str(t)]["surfacePhotoFrom"] == "tote:%d" % s


def test_changing_or_removing_the_photo_in_the_run_never_touches_the_totes(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    rid = new_draft(admin)
    ids = lock_in(admin, rid, t, surfacePhotoFrom="tote:%d" % s, striationPhotoFrom="tote:%d" % g).json["photoIds"]
    up = admin.post("/api/production/%d/feedstock-photo" % rid, {"slot": "surface", "filename": "new.png", "contentType": "image/png", "dataB64": b64(IMG_C), "replaces": ids["surfacePhotoId"]})
    assert up.status == 200, up.text
    assert sql(srv, "SELECT COUNT(*) FROM run_attachments WHERE id=?", (ids["surfacePhotoId"],)) == [(0,)]          # the run's old copy is gone ...
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, s)).body == IMG_A                                # ... the tote's picture is not
    assert admin.delete("/api/production/%d/feedstock-photo/%d" % (rid, ids["striationPhotoId"])).status == 200
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, g)).body == IMG_B
    offered = admin.get("/api/totes/%d/ph?forRun=1" % t).json["latestCharacterization"]
    assert offered["surfacePhotoFrom"] in ("tote:%d" % s, "run:%d:%d" % (rid, up.json["attachmentId"]))              # a later run is still offered a picture


def test_a_later_change_to_the_totes_picture_does_not_change_a_finished_run(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    g = tote_photo(admin, t, "striation", IMG_B, "stri.png")
    details = {str(t): card(surfacePhotoFrom="tote:%d" % s, striationPhotoFrom="tote:%d" % g)}
    body = {"sku": "KELPIVEX", "toteIds": [t], "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    rid = admin.post("/api/production/drafts", body).json["run"]["id"]
    for stage, payload in stage_payloads().items():
        admin.put("/api/production/%d/stages/%s" % (rid, stage), payload)
    admin.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": "New 1,000 L IBC Tote", "qty": 1}).raise_for_status()
    done = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert done.status == 200, done.text
    assert len(run_photos(srv, rid)) == 2                                                                           # one copy per picture, however often the marker was sent
    kept = sql(srv, "SELECT surface_photo FROM run_inputs WHERE run_id=?", (rid,))[0][0]
    tote_photo(admin, t, "surface", IMG_C, "surf-new.png")                                                          # the tote's picture is replaced afterwards
    assert admin.get("/api/production/%d/attachments/%s/download" % (rid, kept)).body == IMG_A                      # the finished run still shows its own copy


def test_a_marker_for_another_totes_picture_or_one_that_is_gone_copies_nothing(srv, admin):
    a, b = harvest_totes(admin, 2)
    sa = tote_photo(admin, a, "surface", IMG_A, "surf.png")
    rid = new_draft(admin)
    r = lock_in(admin, rid, b, surfacePhotoFrom="tote:%d" % sa, striationPhotoFrom="run:99999:1")
    assert r.status == 200 and r.json["photoIds"] == {"surfacePhotoId": None, "striationPhotoId": None}, r.text
    sql(srv, "DELETE FROM tote_attachments WHERE id=?", (sa,))
    rid2 = new_draft(admin)
    r = lock_in(admin, rid2, a, surfacePhotoFrom="tote:%d" % sa)
    assert r.status == 200 and r.json["photoIds"]["surfacePhotoId"] is None
    assert run_photos(srv, rid) == [] and run_photos(srv, rid2) == []


def test_a_picture_that_was_taken_away_is_not_offered_again(srv, admin):
    t, = harvest_totes(admin, 1)
    tote_photo(admin, t, "surface", IMG_A, "surf.png")
    assert "surfacePhotoFrom" in admin.get("/api/totes/%d/ph?forRun=1" % t).json["latestCharacterization"]
    sql(srv, "INSERT INTO tote_stability_log (tote_lot_id,user_name,field,old_value,new_value,note,created_at) VALUES (?,?,?,?,?,?,?)",
        (t, "Tester", "Surface photo", "surf.png", "(removed)", "taken away", "2026-11-19T12:00:00Z"))
    assert "surfacePhotoFrom" not in admin.get("/api/totes/%d/ph?forRun=1" % t).json["latestCharacterization"]


def test_discarding_the_draft_removes_the_copies_but_not_the_totes_pictures(srv, admin):
    t, = harvest_totes(admin, 1)
    s = tote_photo(admin, t, "surface", IMG_A, "surf.png")
    rid = new_draft(admin)
    lock_in(admin, rid, t, surfacePhotoFrom="tote:%d" % s)
    copies = run_photos(srv, rid)
    assert len(copies) == 1 and uploaded(srv, copies[0][2])
    assert admin.delete("/api/production/drafts/%d" % rid).status == 200
    assert run_photos(srv, rid) == [] and not uploaded(srv, copies[0][2])
    assert admin.get("/api/totes/%d/attachments/%d/download" % (t, s)).body == IMG_A


def test_a_copy_is_not_a_persons_upload_for_the_daily_quota(srv, admin):
    big = b"\x89PNG-big-" + b"Z" * (700 * 1024)
    person, _ = srv.make_user(role="user")
    assert admin.put("/api/settings/upload_user_daily_mb", {"value": 1}).status == 200
    try:
        t, = harvest_totes(admin, 1)
        s = tote_photo(person, t, "surface", big, "big.png")                                                        # 700 KB of the person's 1 MB day
        second = person.post("/api/totes/%d/photo" % t, {"slot": "striation", "filename": "two.png", "contentType": "image/png", "dataB64": b64(big)})
        assert second.status == 429 and second.json["code"] == "upload_quota"                                       # the limit is really in force
        rid = new_draft(person)
        r = lock_in(person, rid, t, surfacePhotoFrom="tote:%d" % s)
        assert r.status == 200 and isinstance(r.json["photoIds"]["surfacePhotoId"], int), r.text                    # but the run's copy of the same picture goes through
    finally:
        admin.put("/api/settings/upload_user_daily_mb", {"value": 300})
