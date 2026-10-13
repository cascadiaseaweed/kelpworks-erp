"""Small hardening batch: a per-user upload quota, a record of deleted totes, the rebaseline event keeping the hash it replaced, and restoring the LIVE data
from a full backup at boot (docs/restore-runbook.md). Download links carrying a short-lived token are tested in tests/test_secrets_sessions.py.
"""
import base64
import glob
import json
import os
import sqlite3

import pytest

from factories import finished_run, harvest_totes, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def upload(client, rid, size, name="doc.pdf"):
    body = {"filename": name, "contentType": "application/pdf", "dataB64": base64.b64encode(b"x" * size).decode()}
    return client.post("/api/production/%d/attachments" % rid, body)


# ---- upload quota ----

def test_a_person_who_has_uploaded_their_daily_limit_is_stopped_and_others_are_not(srv, admin):
    rid = finished_run(admin)
    other, _ = srv.make_user(role="user")
    assert admin.put("/api/settings/upload_user_daily_mb", {"value": 1}).status == 200
    try:
        assert upload(admin, rid, 600 * 1024).status == 200
        refused = upload(admin, rid, 600 * 1024)
        assert refused.status == 429 and refused.json["code"] == "upload_quota"
        assert upload(other, rid, 600 * 1024).status == 200                                  # someone else has their own allowance
        assert admin.put("/api/settings/upload_user_daily_mb", {"value": 0}).status == 200   # 0 = no limit
        assert upload(admin, rid, 600 * 1024).status == 200
    finally:
        admin.put("/api/settings/upload_user_daily_mb", {"value": 300})


def test_old_uploads_do_not_count_against_today(srv, admin):
    rid = finished_run(admin)
    sql(srv, "UPDATE run_attachments SET uploaded_at='2020-01-01T00:00:00Z'")                # start from an empty day
    assert admin.put("/api/settings/upload_user_daily_mb", {"value": 1}).status == 200
    try:
        assert upload(admin, rid, 800 * 1024).status == 200
        assert upload(admin, rid, 800 * 1024).status == 429
        sql(srv, "UPDATE run_attachments SET uploaded_at='2020-01-01T00:00:00Z' WHERE run_id=?", (rid,))
        assert upload(admin, rid, 800 * 1024).status == 200
    finally:
        admin.put("/api/settings/upload_user_daily_mb", {"value": 300})


# ---- deleting a tote leaves a record ----

def test_deleting_a_tote_records_what_it_was_and_who_removed_it(srv, admin):
    tote = harvest_totes(admin, n=1, site="JAM", species="SL")[0]
    assert admin.put("/api/totes/%d" % tote, {"ph": 3.9}).status == 200                      # a line in its stability log
    lot = sql(srv, "SELECT lot_number FROM tote_lots WHERE id=?", (tote,))[0][0]
    assert admin.delete("/api/totes/%d" % tote).status == 200
    assert sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE id=?", (tote,))[0][0] == 0
    rows = sql(srv, "SELECT ref, snapshot, deleted_by FROM deleted_records WHERE entity_type='tote' AND ref=?", (lot,))
    assert len(rows) == 1 and rows[0][2]
    snap = json.loads(rows[0][1])
    assert snap["tote"]["lot_number"] == lot and any(e["field"] == "pH" for e in snap["stabilityLog"])


# ---- rebaseline keeps the hash it replaced ----

def test_the_rebaseline_event_names_the_hash_it_replaced(srv, admin):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    rid = finished_run(admin)
    pm.post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": pm_info["password"]}).raise_for_status()
    old_hash = sql(srv, "SELECT release_review_hash FROM production_runs WHERE id=?", (rid,))[0][0]
    assert old_hash
    conn = sqlite3.connect(srv.db_path)
    conn.execute("PRAGMA user_version=4")                                                     # as if the signed hashes were made by the previous snapshot format
    conn.commit()
    conn.close()
    srv.restart()
    admin.base_url = srv.url                                                                   # (a restart picks a new port)
    ev = sql(srv, "SELECT detail FROM release_events WHERE run_id=? AND event_type='rebaseline'", (rid,))
    assert len(ev) == 1
    detail = json.loads(ev[0][0])
    assert detail["previousHash"] == old_hash and detail["previousVersion"] == 4
    assert admin.get("/api/release/verify").json["ok"] is True


# ---- restoring the live data from a full backup ----

def full_backup(server, path):
    r = server.admin_client().get("/api/admin/backup?full=1")
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.body)
    return path


def customers(server):
    return sorted(c["name"] for c in server.admin_client().get("/api/customers").json["customers"])


def test_live_data_can_be_restored_from_a_full_backup_at_boot_without_resetting_passwords():
    with Server() as a, Server() as b:
        a.admin_client().post("/api/customers", {"name": "Only In Backup"}).raise_for_status()
        keeper, keeper_info = a.make_user(role="user")                                     # a person with their own password
        b.admin_client().post("/api/customers", {"name": "Only In Live"}).raise_for_status()
        zip_path = full_backup(a, os.path.join(b.tmp, "restore_me.zip"))
        b.extra_env.update(KELP_ERP_RESTORE_FROM=zip_path, KELP_ERP_RESTORE_CONFIRM="replace-live-data")
        b.restart()
        assert customers(b) == ["Only In Backup"]                                          # the backup replaced the live data
        assert glob.glob(os.path.join(b.tmp, "backups", "pre-restore-*.db.gz"))           # and what was there is kept
        assert not os.path.exists(zip_path) and glob.glob(zip_path + ".restored-*")       # so the next start does not restore again
        again = b.client()
        assert again.login(keeper_info["email"], keeper_info["password"]).status == 200   # no password was reset
        b.restart()                                                                       # the variables are still set: nothing happens the second time
        assert customers(b) == ["Only In Backup"]


def test_a_restore_without_the_confirmation_or_with_a_bad_file_changes_nothing():
    with Server() as live:
        live.admin_client().post("/api/customers", {"name": "Precious"}).raise_for_status()
        bad = os.path.join(live.tmp, "restore_me.zip")
        with open(bad, "wb") as f:
            f.write(b"this is not a zip file")
        live.extra_env.update(KELP_ERP_RESTORE_FROM=bad)
        with pytest.raises(RuntimeError) as e:                                            # no confirmation: the server refuses to start
            live.restart()
        assert "KELP_ERP_RESTORE_CONFIRM" in str(e.value)
    with Server() as live:
        live.admin_client().post("/api/customers", {"name": "Precious"}).raise_for_status()
        bad = os.path.join(live.tmp, "restore_me.zip")
        with open(bad, "wb") as f:
            f.write(b"this is not a zip file")
        live.extra_env.update(KELP_ERP_RESTORE_FROM=bad, KELP_ERP_RESTORE_CONFIRM="replace-live-data")
        with pytest.raises(RuntimeError) as e:                                            # confirmed but damaged: it stops, the data is untouched
            live.restart()
        assert "RESTORE FAILED" in str(e.value)


# ---- a missing disk must not produce a fresh empty database ----

def test_the_server_refuses_to_start_when_the_required_volume_is_not_mounted():
    with pytest.raises(RuntimeError) as e:
        Server(env={"KELP_ERP_REQUIRE_MOUNT": os.path.join(os.path.dirname(os.path.abspath(__file__)), "not_a_mount_point")}).start()
    assert "not a mounted volume" in str(e.value)


def test_the_server_starts_when_the_required_path_is_a_mount_point():
    with Server(env={"KELP_ERP_REQUIRE_MOUNT": os.path.abspath(os.sep)}) as s:                  # the root of the file system is always a mount point
        assert s.admin_client().get("/api/me").status == 200


# ---- no deprecated clock calls (the log showed a DeprecationWarning for datetime.utcnow) ----

def test_the_server_uses_no_deprecated_utcnow():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "kelp_erp_server.py"), encoding="utf-8").read()
    assert "datetime.datetime.utcnow(" not in src
    import subprocess, sys
    r = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import kelp_erp_server as k; print(k.now_iso()[-1], k.utcnow().year > 2000)"],
                       cwd=root, capture_output=True, env=dict(os.environ, KELP_ERP_DB=os.path.join(root, "tests", "_warn_check.db"), KELP_ERP_SECRET="x" * 32))
    assert r.returncode == 0 and r.stdout.decode().split() == ["Z", "True"], r.stderr.decode()[-400:]
