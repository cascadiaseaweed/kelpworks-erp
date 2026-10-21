"""KW-04: photos of finalized runs move from the server disk to the SharePoint records library. The sync script CONFIRMS each photo it saved (path + SHA-256); KelpWorks keeps the
confirmation only if the checksum is the one of its own file; a daily job (off by default) takes confirmed photos off the disk once their run is old enough, writes an append-only log, and
leaves the record with a link. Pictures only; nothing is removed without a verified copy."""
import base64
import hashlib
import http.client
import os
import subprocess
import sys

import pytest

from factories import finalize_run, harvest_totes, sql
from harness import ROOT, Server

KEY = "archive-key-for-tests-0123456789abcdef"
LIBRARY = "https://example.sharepoint.com/sites/KelpWorks/KelpWorks-Records"
TOOL = os.path.join(ROOT, "tools", "kelpworks_archive_sync.py")


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY, "KELP_ERP_SHAREPOINT_BASE_URL": LIBRARY}) as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user()
    return {"admin": srv.admin_client(), "plain": plain}


def b64(data):
    return base64.b64encode(data).decode()


def add_photo(admin, rid, name, data):
    r = admin.post("/api/production/%d/attachments" % rid, {"filename": name, "contentType": "image/jpeg", "dataB64": b64(data)})
    assert r.status == 200, r.text
    return max(a["id"] for a in r.json["attachments"])


def stored(srv, table, aid):
    return os.path.join(srv.tmp, "uploads", sql(srv, "SELECT stored_name FROM %s WHERE id=?" % table, (aid,))[0][0])


def photo_entry(admin, src):
    files = admin.get("/api/archive/manifest").json["files"]
    return [f for f in files if f.get("src") == src][0]


def confirm(admin, entries_and_data):
    files = [{"path": e["path"], "src": e["src"], "runId": e["runId"], "sha256": hashlib.sha256(d).hexdigest()} for e, d in entries_and_data]
    return admin.post("/api/archive/confirm", {"files": files})


def settings(srv, days, enabled):
    sql(srv, "UPDATE settings SET value=? WHERE key='image_archive_days'", (days,))
    sql(srv, "UPDATE settings SET value=? WHERE key='image_purge_enabled'", (enabled,))


def purge(admin, dry=False):
    r = admin.post("/api/archive/images/purge", {"dryRun": dry})
    assert r.status == 200, r.text
    return r.json


def new_run(admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1))
    assert f.status == 200
    return rid


def test_nothing_is_removed_by_default(srv, people):
    admin = people["admin"]
    assert sql(srv, "SELECT value FROM settings WHERE key='image_purge_enabled'") == [(0,)]
    assert sql(srv, "SELECT value FROM settings WHERE key='image_archive_days'") == [(30,)]
    rid = new_run(admin)
    data = b"\xff\xd8 a photo"
    aid = add_photo(admin, rid, "tote.jpg", data)
    assert confirm(admin, [(photo_entry(admin, "att:%d" % aid), data)]).json["confirmed"] == 1
    settings(srv, 0, 0)
    res = purge(admin)
    assert res["enabled"] is False and res["purged"] == 0 and os.path.exists(stored(srv, "run_attachments", aid))       # switched off: not even a confirmed, old photo goes
    assert purge(admin, dry=True)["wouldPurge"] >= 1                                                                    # a dry run only lists


def test_a_confirmation_is_kept_only_for_a_matching_picture_of_a_finalized_run(srv, people):
    admin, plain = people["admin"], people["plain"]
    rid = new_run(admin)
    data = b"\xff\xd8 picture one"
    aid = add_photo(admin, rid, "one.jpg", data)
    doc = add_photo(admin, rid, "report.pdf", b"%PDF-1.4 not a picture")
    e = photo_entry(admin, "att:%d" % aid)
    assert e["confirmed"] is False and e["purged"] is False
    assert "confirmed" not in photo_entry(admin, "att:%d" % doc)                                    # only pictures carry the state
    r = confirm(admin, [(e, b"some other bytes")])
    assert r.json["confirmed"] == 0 and "not the same" in r.json["rejected"][0]["reason"]           # the copy is not the server's file
    bad = dict(e, path=e["path"].replace("Photo", "Phot0"))
    assert confirm(admin, [(bad, data)]).json["confirmed"] == 0                                      # not the path KelpWorks would give it
    assert confirm(admin, [(photo_entry(admin, "att:%d" % doc), b"%PDF-1.4 not a picture")]).json["confirmed"] == 0     # a document is not confirmed
    assert plain.post("/api/archive/confirm", {"files": []}).status == 403                           # administrators and the sync key only
    keyed = srv.client().post("/api/archive/confirm", {"files": [{"path": e["path"], "src": e["src"], "runId": e["runId"], "sha256": hashlib.sha256(data).hexdigest()}]},
                              headers={"X-Archive-Key": KEY})
    assert keyed.status == 200 and keyed.json["confirmed"] == 1
    assert photo_entry(admin, "att:%d" % aid)["confirmed"] is True


def test_a_confirmed_old_photo_is_removed_logged_and_leaves_a_link(srv, people):
    admin = people["admin"]
    rid = new_run(admin)
    data = b"\xff\xd8 to be archived"
    aid = add_photo(admin, rid, "away.jpg", data)
    kept = add_photo(admin, rid, "kept.jpg", b"\xff\xd8 never confirmed")
    doc = add_photo(admin, rid, "keep.pdf", b"%PDF-1.4 document")
    e = photo_entry(admin, "att:%d" % aid)
    confirm(admin, [(e, data)]).raise_for_status()
    settings(srv, 30, 1)
    assert purge(admin)["purged"] == 0                                                              # the run is not 30 days old
    settings(srv, 0, 1)
    path = stored(srv, "run_attachments", aid)
    res = purge(admin)
    assert res["purged"] >= 1 and any(f["id"] == aid for f in res["files"])
    assert not os.path.exists(path) and sql(srv, "SELECT purged_at IS NOT NULL FROM run_attachments WHERE id=?", (aid,)) == [(1,)]
    assert os.path.exists(stored(srv, "run_attachments", kept)) and os.path.exists(stored(srv, "run_attachments", doc))      # unconfirmed picture and documents stay
    log = sql(srv, "SELECT source_table, attachment_id, sha256, archived_path FROM image_purge_log WHERE attachment_id=?", (aid,))
    assert log == [("run_attachments", aid, hashlib.sha256(data).hexdigest(), e["path"])]
    # the archive keeps listing it (no "missing file" problem) and says it was removed
    m = admin.get("/api/archive/manifest").json
    assert photo_entry(admin, "att:%d" % aid)["purged"] is True
    assert not [p for p in m["problems"] if "away" in str(p)]
    # opening it: a browser is sent to the library, a script gets 410 with the link
    url = "/api/production/%d/attachments/%d/download" % (rid, aid)
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)           # (not the test client: it would follow the redirect to SharePoint)
    conn.request("GET", url, headers={"Accept": "text/html", "Authorization": "Bearer " + admin.token})
    resp = conn.getresponse()
    assert resp.status == 302 and resp.getheader("Location").startswith(LIBRARY + "/")
    conn.close()
    r = admin.get(url)
    assert r.status == 410 and r.json["archived"] is True and r.json["archiveUrl"].startswith(LIBRARY + "/") and r.json["archivedPath"] == e["path"]
    assert purge(admin)["purged"] == 0 or all(f["id"] != aid for f in purge(admin)["files"])        # nothing is removed twice
    assert sql(srv, "SELECT COUNT(*) FROM image_purge_log WHERE attachment_id=?", (aid,)) == [(1,)]


def test_a_photo_that_changed_after_it_was_confirmed_is_left_alone(srv, people):
    admin = people["admin"]
    rid = new_run(admin)
    data = b"\xff\xd8 original"
    aid = add_photo(admin, rid, "changed.jpg", data)
    confirm(admin, [(photo_entry(admin, "att:%d" % aid), data)]).raise_for_status()
    with open(stored(srv, "run_attachments", aid), "wb") as f:
        f.write(b"\xff\xd8 something else")
    settings(srv, 0, 1)
    res = purge(admin)
    assert any(s["file"] == "changed.jpg" and "changed" in s["reason"] for s in res["skipped"])
    assert os.path.exists(stored(srv, "run_attachments", aid))


def test_a_tote_photo_of_a_finalized_run_is_archived_too(srv, people):
    admin = people["admin"]
    totes = harvest_totes(admin, 1)
    rid, f = finalize_run(admin, totes)
    assert f.status == 200
    data = b"\xff\xd8 a tote photo"
    r = admin.post("/api/totes/%d/photo" % totes[0], {"slot": "surface", "filename": "tote-side.jpg", "contentType": "image/jpeg", "dataB64": b64(data)})
    assert r.status == 200, r.text
    aid = r.json["attachmentId"]
    e = photo_entry(admin, "tote_att:%d" % aid)
    assert confirm(admin, [(e, data)]).json["confirmed"] == 1
    settings(srv, 0, 1)
    path = stored(srv, "tote_attachments", aid)
    purge(admin)
    assert not os.path.exists(path)
    assert admin.get("/api/totes/%d/attachments/%d/download" % (totes[0], aid)).status == 410


def test_the_status_counts_what_moved_away(srv, people):
    admin = people["admin"]
    rid = new_run(admin)
    data = b"\xff\xd8 " + b"x" * 5000
    aid = add_photo(admin, rid, "big.jpg", data)
    confirm(admin, [(photo_entry(admin, "att:%d" % aid), data)]).raise_for_status()
    settings(srv, 0, 1)
    purge(admin)
    status = admin.get("/api/archive/status").json["images"]
    assert status["purged"] >= 1 and status["enabled"] is True and status["libraryUrlSet"] is True


def test_the_sync_script_confirms_the_photos_it_saved_and_warns_if_a_moved_photo_goes_missing(tmp_path):
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:                               # its own server: the shared one has photos that already moved away
        admin = s.admin_client()
        rid = new_run(admin)
        data = bytes([255, 216]) + b" saved by the script"
        aid = add_photo(admin, rid, "scripted.jpg", data)
        env = dict(os.environ, KELPWORKS_ARCHIVE_KEY=KEY, PYTHONIOENCODING="utf-8")
        run = lambda *extra: subprocess.run([sys.executable, TOOL, "--url", s.url, "--root", str(tmp_path / "lib"), "--skip-documents", "--skip-backups"] + list(extra),
                                            capture_output=True, text=True, env=env, timeout=300)
        p = run("--init")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "1 photos confirmed" in p.stdout
        assert photo_entry(admin, "att:%d" % aid)["confirmed"] is True
        again = run()
        assert again.returncode == 0 and "0 photos confirmed" in again.stdout              # nothing left to confirm the second time
        settings(s, 0, 1)
        purge(admin)
        assert not os.path.exists(stored(s, "run_attachments", aid))
        for folder, _d, files in os.walk(str(tmp_path / "lib")):                          # someone deletes the library copy: the ONLY copy is now gone
            for f in files:
                if f == "%s_Photo_scripted.jpg" % sql(s, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]:
                    os.remove(os.path.join(folder, f))
        lost = run()
        assert lost.returncode == 1 and "KelpWorks no longer holds it" in lost.stdout
