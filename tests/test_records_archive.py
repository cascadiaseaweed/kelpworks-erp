"""Records archive and nightly backups (batch 9): what the ERP offers to the records library, how it names it, who may read it, the backups on
the server disk, and the sync script that copies it all into the synced SharePoint folder."""
import base64
import datetime
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile

import pytest

import kelp_erp_server as K
from factories import finalize_run, finished_run, harvest_totes, release_run, sql
from harness import ROOT, Server

KEY = "archive-key-for-tests-0123456789abcdef"
TOOL = os.path.join(ROOT, "tools", "kelpworks_archive_sync.py")
NAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def load_tool():
    spec = importlib.util.spec_from_file_location("kelpworks_archive_sync", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(isProductionManager=True)
    qm, qm_info = srv.make_user(isQualityManager=True)
    plain, _ = srv.make_user()
    return {"admin": srv.admin_client(), "pm": pm, "qm": qm, "plain": plain, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"]}


def keyed(srv, path):
    return srv.client().get(path, headers={"X-Archive-Key": KEY})


def manifest(srv, **kw):
    r = keyed(srv, "/api/archive/manifest")
    assert r.status == 200, r.text
    return r.json


def entry(m, suffix):
    found = [f for f in m["files"] if f["path"].endswith(suffix)]
    assert len(found) == 1, (suffix, [f["path"] for f in m["files"]])
    return found[0]


def upload(client, rid, filename, data=b"file-bytes", ctype="application/octet-stream"):
    r = client.post("/api/production/%d/attachments" % rid, {"filename": filename, "contentType": ctype, "dataB64": base64.b64encode(data).decode()})
    assert r.status == 200, r.text
    return max(a["id"] for a in r.json["attachments"])


def run_lot(srv, rid):
    return sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]


# ---- who may read it ----

def test_the_archive_is_for_administrators_and_the_sync_key_only(srv, people):
    anon = srv.client()
    assert anon.get("/api/archive/manifest").status == 401
    assert people["plain"].get("/api/archive/manifest").status == 403
    assert anon.get("/api/archive/manifest", headers={"X-Archive-Key": "wrong-key-wrong-key-wrong-key"}).status == 403
    assert keyed(srv, "/api/archive/manifest").status == 200
    assert people["admin"].get("/api/archive/manifest").status == 200
    assert people["plain"].post("/api/archive/backup").status == 403
    assert anon.get("/api/archive/status").status == 401


def test_a_key_that_is_too_short_is_never_accepted():
    short = "short-key"
    with Server(env={"KELP_ERP_ARCHIVE_KEY": short}) as s:
        assert s.client().get("/api/archive/manifest", headers={"X-Archive-Key": short}).status == 403
        assert "shorter than" in s.log_tail(80)
        assert s.admin_client().get("/api/archive/status").json["archiveKeyTooShort"] is True


def test_repeated_wrong_keys_are_throttled():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as own:                                   # its own server: the throttle also holds the right key back from this address
        codes = [own.client().get("/api/archive/manifest", headers={"X-Archive-Key": "x" * 30}).status for _ in range(12)]
        assert codes[0] == 403 and 429 in codes
        assert own.client().get("/api/archive/manifest", headers={"X-Archive-Key": KEY}).status == 429


# ---- what is archived and how it is named ----

def test_the_manifest_lists_the_agreed_tree_for_finalized_runs_only(srv, people):
    admin = people["admin"]
    draft, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)               # a draft run is not archived
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    m = manifest(srv)
    assert m["skeleton"] == ["01_System-Backups/daily", "01_System-Backups/weekly", "01_System-Backups/monthly", "01_System-Backups/pre-migrate",
                             "02_Production-Runs/_Index", "03_SOPs/Current", "03_SOPs/Superseded", "04_Safety-Data-Sheets/Current",
                             "04_Safety-Data-Sheets/Superseded", "05_Fulfillment"]
    assert entry(m, "README_Naming-Convention.txt")["kind"] == "readme" and entry(m, "_Index/runs_index.csv")["kind"] == "index"
    folder = "02_Production-Runs/2026/2026-05/%s_KELPIVEX_2026-05-04" % lot
    assert entry(m, "%s_Production-Summary_rev1.pdf" % lot)["path"] == folder + "/01_Report-and-CoA/%s_Production-Summary_rev1.pdf" % lot
    assert entry(m, "%s_CoA_PRELIMINARY.pdf" % lot)["kind"] == "coa"
    assert not [f for f in m["files"] if run_lot(srv, draft) in f["path"]]
    for f in m["files"]:
        assert NAME_OK.match(f["path"].replace("/", "_")), f["path"]                       # letters, numbers, . _ - only: no spaces or odd characters
        assert len(f["path"].split("/")[-1]) <= 120 and len(f["path"]) <= 200
    assert m["problems"] == []


def test_documents_are_filed_by_what_they_are(srv, people):
    admin, qm = people["admin"], people["qm"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    photo = upload(admin, rid, "Site Photo (north).JPG", b"jpeg-bytes", "image/jpeg")
    plain_doc = upload(admin, rid, "Batch notes - FINAL.pdf", b"%PDF-1.4 notes")
    twin_a = upload(admin, rid, "scan.png", b"png-a")
    twin_b = upload(admin, rid, "scan.png", b"png-b")                                       # same name as the one before
    report = upload(admin, rid, "SGS report.pdf", b"%PDF-1.4 report")
    req_doc = upload(admin, rid, "anything.docx", b"docx")
    req_sheet = upload(admin, rid, "anything.xlsx", b"xlsx")
    surface = upload(admin, rid, "tote surface.jpg", b"surface")
    assert qm.post("/api/production/%d/lab-results" % rid, {"labName": "SGS Canada", "reportNumber": "CA-2026/77", "attachmentId": report,
                                                            "results": [{"specCode": "salm", "value": "Negative"}]}).status == 200
    sql(srv, "INSERT INTO lab_requisitions (req_number, run_id, lab_name, attachment_id, sheet_attachment_id, created_at) VALUES ('REQ-20261008-002', ?, 'SGS Canada', ?, ?, 'now')",
        (rid, req_doc, req_sheet))
    tote_lot, input_id = sql(srv, "SELECT t.lot_number, i.id FROM run_inputs i JOIN tote_lots t ON t.id=i.tote_lot_id WHERE i.run_id=?", (rid,))[0]
    sql(srv, "UPDATE run_inputs SET surface_photo=? WHERE id=?", (str(surface), input_id))
    tote_id = sql(srv, "SELECT tote_lot_id FROM run_inputs WHERE id=?", (input_id,))[0][0]
    os.makedirs(os.path.join(srv.tmp, "uploads"), exist_ok=True)
    with open(os.path.join(srv.tmp, "uploads", "tote_photo_on_disk.jpg"), "wb") as f:
        f.write(b"tote-bytes")
    sql(srv, "INSERT INTO tote_attachments (tote_lot_id, filename, content_type, size, stored_name, uploaded_at) VALUES (?, 'Cold Room.jpg', 'image/jpeg', 10, 'tote_photo_on_disk.jpg', 'now')", (tote_id,))

    paths = {f["path"].split("/", 4)[-1]: f for f in manifest(srv)["files"] if lot in f["path"]}
    for name, kind in (("01_Report-and-CoA/%s_Production-Summary_rev1.pdf" % lot, "summary"),
                       ("02_Lab/%s_Requisition_SGS-Canada_REQ-20261008-002.docx" % lot, "requisition"),
                       ("02_Lab/%s_Requisition-Samples_SGS-Canada_REQ-20261008-002.xlsx" % lot, "sample-list"),
                       ("02_Lab/%s_Lab-Report_SGS-Canada_CA-2026-77.pdf" % lot, "lab-report"),
                       ("03_Photos/%s_Photo_%s_surface.jpg" % (lot, tote_lot), "photo"),
                       ("03_Photos/%s_Photo_Site-Photo-north.jpg" % lot, "photo"),
                       ("03_Photos/%s_Photo_scan.png" % lot, "photo"),
                       ("03_Photos/%s_Photo_scan_%d.png" % (lot, twin_b), "photo"),            # the second file of the same name gets its id, the first keeps the plain name
                       ("03_Photos/%s_Tote-Photo_%s_Cold-Room.jpg" % (lot, tote_lot), "photo"),
                       ("04_Other/%s_Document_Batch-notes-FINAL.pdf" % lot, "document")):
        assert name in paths, (name, sorted(paths))
        assert paths[name]["kind"] == kind, name
    # adding another file later never renames what is already filed
    upload(admin, rid, "scan.png", b"png-c")
    again = {f["path"] for f in manifest(srv)["files"]}
    assert all(any(p.endswith(n) for p in again) for n in ("%s_Photo_scan.png" % lot, "%s_Photo_scan_%d.png" % (lot, twin_b)))


def test_the_run_folder_never_moves_when_the_run_is_amended(srv, people):
    rid = finished_run(people["admin"])
    lot = run_lot(srv, rid)
    before = entry(manifest(srv), "%s_Production-Summary_rev1.pdf" % lot)["path"]
    sql(srv, "UPDATE production_runs SET run_date='2027-01-15', sku_code='KELPIVEX' WHERE id=?", (rid,))      # the run's date is amended later
    assert entry(manifest(srv), "%s_Production-Summary_rev1.pdf" % lot)["path"] == before
    assert "2026-05-04" in before


def test_a_file_that_is_missing_from_disk_is_reported_not_listed(srv, people):
    rid = finished_run(people["admin"])
    lot = run_lot(srv, rid)
    att = upload(people["admin"], rid, "ghost.pdf")
    stored = sql(srv, "SELECT stored_name FROM run_attachments WHERE id=?", (att,))[0][0]
    os.remove(os.path.join(srv.tmp, "uploads", stored))
    m = manifest(srv)
    assert not [f for f in m["files"] if f["path"].endswith("%s_Document_ghost.pdf" % lot)]
    assert any(p["run"] == lot and "ghost.pdf" in p["what"] for p in m["problems"])


# ---- versions: what changes when the content does ----

def test_versions_are_stable_until_the_content_changes(srv, people):
    admin, qm = people["admin"], people["qm"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    v1 = {f["path"].split("/")[-1]: f["version"] for f in manifest(srv)["files"] if lot in f["path"]}
    srv.restart()                                                                          # nothing changed: the same versions, however often it is asked
    for client in people.values():
        if hasattr(client, "base_url"):
            client.base_url = srv.url                                                      # (a restart picks a new port)
    v2 = {f["path"].split("/")[-1]: f["version"] for f in manifest(srv)["files"] if lot in f["path"]}
    assert v1 == v2
    assert qm.post("/api/production/%d/lab-results" % rid, {"labName": "Lab", "reportNumber": "R1", "results": [{"specCode": "salm", "value": "Negative"}]}).status == 200
    v3 = {f["path"].split("/")[-1]: f["version"] for f in manifest(srv)["files"] if lot in f["path"]}
    assert v3["%s_CoA_PRELIMINARY.pdf" % lot] != v1["%s_CoA_PRELIMINARY.pdf" % lot]       # a lab result changes the certificate


def test_the_certificate_becomes_released_and_supersedes_the_preliminary(srv, people):
    rid = finished_run(people["admin"])
    lot = run_lot(srv, rid)
    release_run(srv, rid, people["pm"], people["pm_pw"], people["qm"], people["qm_pw"])
    m = manifest(srv)
    released = entry(m, "%s_CoA_RELEASED.pdf" % lot)
    assert released["supersedes"] == [released["path"].replace("RELEASED", "PRELIMINARY")]
    assert not [f for f in m["files"] if f["path"].endswith("%s_CoA_PRELIMINARY.pdf" % lot)]


# ---- the files themselves ----

def test_files_are_served_exactly(srv, people):
    admin = people["admin"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    upload(admin, rid, "evidence.png", b"\x89PNG-evidence-bytes")
    m = manifest(srv)
    for suffix in ("%s_Production-Summary_rev1.pdf" % lot, "%s_CoA_PRELIMINARY.pdf" % lot, "%s_Photo_evidence.png" % lot):
        f = entry(m, suffix)
        r = srv.client().get("/api/archive/file?run=%d&path=%s" % (rid, f["path"]), headers={"X-Archive-Key": KEY})
        assert r.status == 200, (suffix, r.text[:200])
        assert r.headers["X-Archive-Sha256"] == __import__("hashlib").sha256(r.body).hexdigest()
        if suffix.endswith(".pdf"):
            assert r.body.startswith(b"%PDF") and r.headers["Content-Type"] == "application/pdf"
        else:
            assert r.body == b"\x89PNG-evidence-bytes" and r.headers["X-Archive-Version"] == f["version"]
    for bad in ("/api/archive/file?run=%d&path=../kelp_erp.db" % rid, "/api/archive/file?run=%d&path=/etc/passwd" % rid, "/api/archive/file?run=abc&path=x",
                "/api/archive/file?path=02_Production-Runs/nothing.pdf", "/api/archive/file?run=%d&path=01_Report-and-CoA/not-there.pdf" % rid):
        assert srv.client().get(bad, headers={"X-Archive-Key": KEY}).status in (400, 404), bad
    index = srv.client().get("/api/archive/file?path=02_Production-Runs/_Index/runs_index.csv", headers={"X-Archive-Key": KEY})
    assert index.status == 200 and index.body.startswith(b"\xef\xbb\xbfprocessing_lot,") and lot.encode() in index.body


# ---- backups on the server disk ----

@pytest.fixture
def in_process(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "DB_PATH", str(tmp_path / "kelp.db"))
    monkeypatch.setattr(K, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(K, "SOP_DIR", str(tmp_path / "sop_documents"))
    monkeypatch.setattr(K, "LAB_DIR", str(tmp_path / "lab_templates"))
    monkeypatch.setattr(K, "ADMIN_PASSWORD", "test-admin-pass-1")
    monkeypatch.setattr(K, "INITIAL_USER_PASSWORD", "initial-pass-123")
    K.init_db()
    return tmp_path


def with_conn(fn):
    conn = K.db()
    try:
        out = fn(conn)
        conn.commit()
        return out
    finally:
        conn.close()


def test_the_nightly_backup_writes_a_complete_zip_once_a_day(in_process):
    assert with_conn(lambda c: K.run_nightly_backup(c, today="2026-10-10")) == {"created": "kelp_erp_2026-10-10.zip"}
    path = os.path.join(K.nightly_dir(), "kelp_erp_2026-10-10.zip")
    with zipfile.ZipFile(path) as z:
        assert {"kelp_erp.db", "manifest.json"} <= set(z.namelist())
        z.extract("kelp_erp.db", str(in_process / "check"))
    assert sqlite3.connect(str(in_process / "check" / "kelp_erp.db")).execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    assert with_conn(lambda c: K.run_nightly_backup(c, today="2026-10-10")) == {"skipped": "today's backup already exists"}
    assert with_conn(lambda c: K.run_nightly_backup(c, today="2026-10-10", force=True)) == {"created": "kelp_erp_2026-10-10.zip"}      # "back up now" replaces it
    assert not [n for n in os.listdir(K.nightly_dir()) if n.endswith(".tmp")]
    assert with_conn(lambda c: K.archive_status(c))["lastBackup"].split()[1] == "kelp_erp_2026-10-10.zip"


def test_only_the_newest_nightly_backups_are_kept(in_process):
    os.makedirs(K.nightly_dir(), exist_ok=True)
    with open(os.path.join(K.nightly_dir(), "kelp_erp_2026-09-30.zip.tmp"), "wb") as f:                # left by a process that was stopped mid-backup
        f.write(b"half")
    for day in range(1, 6):
        with_conn(lambda c, day=day: K.run_nightly_backup(c, today="2026-10-%02d" % day, keep=3))
    assert sorted(os.listdir(K.nightly_dir())) == ["kelp_erp_2026-10-03.zip", "kelp_erp_2026-10-04.zip", "kelp_erp_2026-10-05.zip"]


def test_a_backup_is_skipped_when_the_disk_is_nearly_full(in_process, monkeypatch):
    monkeypatch.setattr(K.shutil, "disk_usage", lambda p: type("U", (), {"free": 1024})())
    res = with_conn(lambda c: K.run_nightly_backup(c, today="2026-10-10"))
    assert "not enough free disk space" in res["skipped"] and not os.path.exists(os.path.join(K.nightly_dir(), "kelp_erp_2026-10-10.zip"))
    assert "not enough free disk space" in with_conn(lambda c: K.archive_status(c))["lastBackupError"]


def test_the_backup_is_due_after_the_chosen_hour_until_todays_file_exists(in_process, monkeypatch):
    monkeypatch.setattr(K, "NIGHTLY_BACKUP", True)
    monkeypatch.setattr(K, "BACKUP_HOUR_UTC", 10)
    assert not K.backup_due(datetime.datetime(2026, 10, 10, 9, 59))
    assert K.backup_due(datetime.datetime(2026, 10, 10, 10, 0))
    with_conn(lambda c: K.run_nightly_backup(c, today="2026-10-10"))
    assert not K.backup_due(datetime.datetime(2026, 10, 10, 23, 0))
    assert K.backup_due(datetime.datetime(2026, 10, 11, 10, 5))                                # tomorrow it is due again
    monkeypatch.setattr(K, "NIGHTLY_BACKUP", False)
    assert not K.backup_due(datetime.datetime(2026, 10, 11, 12, 0))


def test_the_scheduler_thread_makes_the_backup_on_its_own():
    env = {"KELP_ERP_NIGHTLY_BACKUP": "1", "KELP_ERP_BACKUP_HOUR_UTC": "0", "KELP_ERP_BACKUP_FIRST_CHECK_SECONDS": "1"}
    with Server(env=env) as s:
        folder = os.path.join(s.tmp, "backups", "nightly")
        deadline = time.time() + 30
        while time.time() < deadline and not (os.path.isdir(folder) and [n for n in os.listdir(folder) if n.endswith(".zip")]):
            time.sleep(0.5)
        made = [n for n in os.listdir(folder) if n.endswith(".zip")]
        assert len(made) == 1 and K.NIGHTLY_RE.match(made[0])
        assert "Nightly backup is on" in s.log_tail(80)


def test_backups_can_be_listed_made_and_downloaded(srv, people):
    admin = people["admin"]
    made = admin.post("/api/archive/backup")
    assert made.status == 200 and made.json["created"].startswith("kelp_erp_")
    listing = keyed(srv, "/api/archive/backups").json["backups"]
    mine = [b for b in listing if b["name"] == made.json["created"]][0]
    assert mine["kind"] == "nightly" and mine["size"] > 0 and "path" not in mine
    got = srv.client().get("/api/archive/backup?name=" + mine["name"], headers={"X-Archive-Key": KEY})
    assert got.status == 200 and len(got.body) == mine["size"] and got.body[:2] == b"PK"
    for bad in ("../kelp_erp.db", "kelp_erp.db", "kelp_erp_1999-01-01.zip", ""):
        assert srv.client().get("/api/archive/backup?name=" + bad, headers={"X-Archive-Key": KEY}).status == 404
    status = admin.get("/api/archive/status").json
    assert status["archiveKeyConfigured"] is True and status["lastBackup"] and status["backups"]


# ---- the sync script ----

def sync(srv, root, *extra, key=KEY):
    env = dict(os.environ, KELPWORKS_ARCHIVE_KEY=key, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, TOOL, "--url", srv.url, "--root", str(root)] + list(extra), capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout + p.stderr


def tree(root):
    out = set()
    for folder, _dirs, files in os.walk(str(root)):
        for f in files:
            out.add(os.path.relpath(os.path.join(folder, f), str(root)).replace(os.sep, "/"))
    return out


def test_the_sync_script_builds_the_library_and_is_idempotent(srv, people, tmp_path):
    admin = people["admin"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    upload(admin, rid, "bench photo.jpg", b"bench-bytes", "image/jpeg")
    root = tmp_path / "KelpWorks-Records"
    code, out = sync(srv, root)
    assert code == 2 and "does not exist" in out                                               # a mistyped / unsynced path is an error, not a new folder
    code, out = sync(srv, root, "--init", "--skip-backups")
    assert code == 0 and "SYNC OK" in out, out
    for d in K.ARCHIVE_SKELETON:
        assert (root / d).is_dir(), d
    files = tree(root)
    assert "README_Naming-Convention.txt" in files and "02_Production-Runs/_Index/runs_index.csv" in files
    run_files = sorted(f for f in files if lot in f and f.startswith("02_Production-Runs/2026/2026-05/"))
    assert any(f.endswith("01_Report-and-CoA/%s_Production-Summary_rev1.pdf" % lot) for f in run_files)
    assert any(f.endswith("03_Photos/%s_Photo_bench-photo.jpg" % lot) for f in run_files)
    photo = [f for f in run_files if f.endswith("bench-photo.jpg")][0]
    assert (root / photo).read_bytes() == b"bench-bytes"
    assert (root / [f for f in run_files if f.endswith("Summary_rev1.pdf")][0]).read_bytes().startswith(b"%PDF")
    again_code, again = sync(srv, root, "--skip-backups")
    assert again_code == 0 and " 0 downloaded" in again and "0 earlier copies" in again, again          # a second pass changes nothing
    assert tree(root) == files | {"_sync-state.json"} or tree(root) == files


def test_a_changed_document_replaces_the_file_and_keeps_the_old_one(srv, people, tmp_path):
    admin, qm = people["admin"], people["qm"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    root = tmp_path / "lib"
    assert sync(srv, root, "--init", "--skip-backups")[0] == 0
    coa = [f for f in tree(root) if f.endswith("%s_CoA_PRELIMINARY.pdf" % lot)][0]
    assert qm.post("/api/production/%d/lab-results" % rid, {"labName": "Lab", "reportNumber": "R9", "results": [{"specCode": "salm", "value": "Negative"}]}).status == 200
    code, out = sync(srv, root, "--skip-backups")
    assert code == 0 and "1 earlier copies kept" in out or "earlier copies kept in _superseded" in out
    kept = [f for f in tree(root) if "/_superseded/" in f and "%s_CoA_PRELIMINARY_old-" % lot in f]
    assert len(kept) == 1 and (root / coa).is_file()                                           # the new certificate is at the usual name, the old one is beside it
    # releasing the run: RELEASED appears, the PRELIMINARY file moves to _superseded
    release_run(srv, rid, people["pm"], people["pm_pw"], qm, people["qm_pw"])
    code, out = sync(srv, root, "--skip-backups")
    assert code == 0, out
    names = tree(root)
    assert any(f.endswith("%s_CoA_RELEASED.pdf" % lot) for f in names) and coa not in names
    assert len([f for f in names if "/_superseded/" in f and "%s_CoA_PRELIMINARY_old-" % lot in f]) == 2


def test_a_record_removed_in_the_erp_stays_in_the_archive(srv, people, tmp_path):
    admin = people["admin"]
    rid = finished_run(admin)
    lot = run_lot(srv, rid)
    att = upload(admin, rid, "keep me.pdf", b"%PDF-1.4 record")
    root = tmp_path / "lib"
    assert sync(srv, root, "--init", "--skip-backups")[0] == 0
    name = [f for f in tree(root) if f.endswith("%s_Document_keep-me.pdf" % lot)][0]
    assert admin.delete("/api/production/%d/attachments/%d" % (rid, att)).status == 200
    code, out = sync(srv, root, "--skip-backups")
    assert code == 0, out
    assert (root / name).read_bytes() == b"%PDF-1.4 record"


def test_a_dry_run_changes_nothing_and_a_wrong_key_stops(srv, people, tmp_path):
    finished_run(people["admin"])
    root = tmp_path / "lib"
    root.mkdir()
    code, out = sync(srv, root, "--dry-run")
    assert code == 0 and "would download" in out and tree(root) == set()
    code, out = sync(srv, root, key="a-wrong-key-a-wrong-key-a-wrong-key")
    assert code == 2 and "STOPPED" in out and "403" in out and tree(root) <= {"_sync-state.json"}


def test_the_script_refuses_an_unsafe_path_from_the_server():
    tool = load_tool()
    for bad in ("../outside.txt", "/etc/passwd", "C:/Windows/x", "a/../../b"):
        with pytest.raises(tool.SyncError):
            tool.safe_join("/library", bad)
    assert tool.safe_join("/library", "02_Production-Runs/x.pdf").replace("\\", "/").endswith("/library/02_Production-Runs/x.pdf")


# ---- backup tiers and retention (the sync script) ----

def test_backup_names_for_the_daily_weekly_and_monthly_tiers():
    tool = load_tool()
    assert tool.tier_names(datetime.date(2026, 10, 10)) == ("kelp_erp_2026-10-10.zip", "kelp_erp_2026-W41.zip", "kelp_erp_2026-10.zip")
    assert tool.tier_names(datetime.date(2026, 1, 1))[1] == "kelp_erp_2026-W01.zip"
    assert tool.tier_names(datetime.date(2027, 1, 1))[1] == "kelp_erp_2026-W53.zip"             # ISO week year, not calendar year


def test_retention_removes_only_old_files_it_recognises(tmp_path):
    tool = load_tool()
    for n in ("kelp_erp_2026-10-01.zip", "kelp_erp_2026-10-02.zip", "kelp_erp_2026-10-03.zip", "my-own-notes.txt", "kelp_erp_2026-10-04.zip.part"):
        (tmp_path / n).write_bytes(b"x")
    assert tool.prune(str(tmp_path), tool.NIGHTLY, 2) == ["kelp_erp_2026-10-01.zip"]
    assert sorted(os.listdir(str(tmp_path))) == ["kelp_erp_2026-10-02.zip", "kelp_erp_2026-10-03.zip", "kelp_erp_2026-10-04.zip.part", "my-own-notes.txt"]
    assert tool.prune(str(tmp_path), tool.NIGHTLY, 0) == ["kelp_erp_2026-10-02.zip"]            # it never removes the last one


def test_backups_are_copied_into_daily_weekly_and_monthly_folders(tmp_path):
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        admin = s.admin_client()
        assert admin.post("/api/archive/backup").status == 200
        nightly = os.path.join(s.tmp, "backups", "nightly")
        today = [n for n in os.listdir(nightly)][0]
        os.makedirs(nightly, exist_ok=True)
        for older in ("kelp_erp_2026-08-30.zip", "kelp_erp_2026-09-29.zip", "kelp_erp_2026-09-30.zip"):    # earlier nights the server still holds
            shutil.copyfile(os.path.join(nightly, today), os.path.join(nightly, older))
        os.makedirs(os.path.join(s.tmp, "backups"), exist_ok=True)
        with open(os.path.join(s.tmp, "backups", "pre-migrate-20261001-010101.db.gz"), "wb") as f:
            f.write(b"\x1f\x8bsnapshot")
        root = tmp_path / "lib"
        code, out = sync(s, root, "--init", "--skip-runs", "--keep-daily", "3")
        assert code == 0 and "SYNC OK" in out, out
        base = root / "01_System-Backups"
        assert sorted(os.listdir(str(base / "daily"))) == sorted(["kelp_erp_2026-09-29.zip", "kelp_erp_2026-09-30.zip", today])          # 3 kept, the oldest pruned
        assert (base / "weekly" / "kelp_erp_2026-W35.zip").is_file() and (base / "weekly" / "kelp_erp_2026-W40.zip").is_file()      # 2026-08-30 / 2026-09-30
        assert (base / "monthly" / "kelp_erp_2026-09.zip").is_file() and (base / "monthly" / "kelp_erp_2026-08.zip").is_file()
        assert zipfile.is_zipfile(str(base / "monthly" / "kelp_erp_2026-09.zip"))
        assert (base / "pre-migrate" / "pre-migrate-20261001-010101.db.gz").is_file()
        code, out = sync(s, root, "--init", "--skip-runs", "--keep-daily", "3")
        assert code == 0 and "Backups: 0 downloaded" in out, out                                           # nothing new: nothing copied
