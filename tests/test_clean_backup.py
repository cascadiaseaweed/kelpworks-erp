"""The clean-backup tool (tools/make_clean_backup.py): configuration kept, every record cleared, restorable like any backup."""
import base64
import glob
import json
import os
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from factories import finished_run, harvest_totes, sql
from harness import ROOT, Server

sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_clean_backup as M  # noqa: E402

MARK = "Wipe Test Customer 7731"


def full_backup(server, path):
    r = server.admin_client().get("/api/admin/backup?full=1")
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.body)
    return path


def tables(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A server with real records and configuration, and a full backup of it."""
    with Server() as s:
        admin = s.admin_client()
        admin.post("/api/customers", {"name": MARK}).raise_for_status()
        rid = finished_run(admin)
        body = {"filename": "photo.png", "contentType": "image/png", "dataB64": base64.b64encode(b"PNGDATA").decode()}
        admin.post("/api/production/%d/attachments" % rid, body).raise_for_status()
        admin.put("/api/settings/coa_application_rate_kg_ha", {"value": 1234}).raise_for_status()
        keeper, info = s.make_user(role="user", isQualityManager=True)
        sql(s, "UPDATE consumables SET on_hand=55 WHERE id=(SELECT MIN(id) FROM consumables)")
        # the source is written the way a database from before the "no labels" change was: old product name, inactive, with a label item
        sql(s, "UPDATE fg_skus SET name='Macrocystis Liquid Kelp Extract', active=0, uses_labels=1 WHERE code='MACRO-LKE'")
        sql(s, "INSERT INTO consumables (name,unit,on_hand,reorder_level,label_sku_code,label_package) VALUES ('FG Label - old Macro', 'ea', 9, 0, 'MACRO-LKE', 'New 1,000 L IBC Tote')")
        sql(s, "DELETE FROM app_flags WHERE key='migrated_remove_labels_of_label_free_skus'")
        folder = tmp_path_factory.mktemp("clean")
        yield {"server": s, "zip": full_backup(s, str(folder / "full.zip")), "folder": folder, "keeper": info}


def test_every_table_is_either_kept_or_cleared(source):
    with zipfile.ZipFile(source["zip"]) as zf:
        zf.extract("kelp_erp.db", str(source["folder"]))
    found = tables(str(source["folder"] / "kelp_erp.db"))
    assert not (found - M.KEEP - M.CLEAR), "a new table needs a decision in tools/make_clean_backup.py (KEEP or CLEAR): %s" % sorted(found - M.KEEP - M.CLEAR)
    assert not (M.KEEP & M.CLEAR)
    assert not ((M.KEEP | M.CLEAR) - found), "the tool lists a table that no longer exists: %s" % sorted((M.KEEP | M.CLEAR) - found)


def test_the_clean_backup_has_no_records_and_keeps_the_configuration(source):
    out = str(source["folder"] / "clean.zip")
    r = M.make_clean_backup(source["zip"], out)
    assert r["removed"]["customers"] == 1 and r["removed"]["production_runs"] == 1 and r["removed"]["tote_lots"] > 0 and r["droppedFiles"] >= 1
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert not [n for n in names if n.startswith("uploads/")]
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["cleaned"] is True and manifest["files"]["uploads"] == 0
        zf.extract("kelp_erp.db", str(source["folder"] / "out"))
    conn = sqlite3.connect(str(source["folder"] / "out" / "kelp_erp.db"))
    try:
        for t in M.CLEAR:
            assert conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] == 0, t
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 5
        assert conn.execute("SELECT COUNT(*) FROM species").fetchone()[0] > 0 and conn.execute("SELECT COUNT(*) FROM coa_specs").fetchone()[0] > 0
        assert conn.execute("SELECT value FROM settings WHERE key='coa_application_rate_kg_ha'").fetchone()[0] == 1234
        assert conn.execute("SELECT COUNT(*) FROM consumables").fetchone()[0] > 0 and conn.execute("SELECT SUM(on_hand) FROM consumables").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    with open(str(source["folder"] / "out" / "kelp_erp.db"), "rb") as f:
        assert MARK.encode() not in f.read()                                              # not even left behind in free pages


def test_it_refuses_a_table_it_does_not_know_and_an_existing_output(source):
    with zipfile.ZipFile(source["zip"]) as zf:
        zf.extract("kelp_erp.db", str(source["folder"] / "odd"))
    db = str(source["folder"] / "odd" / "kelp_erp.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE brand_new_records (id INTEGER)")
    conn.commit()
    conn.close()
    target = str(source["folder"] / "odd" / "clean.db")
    with pytest.raises(M.CleanError) as e:
        M.clean_database(db, target)
    assert "brand_new_records" in str(e.value) and not os.path.exists(target)
    with pytest.raises(M.CleanError) as e:
        M.make_clean_backup(source["zip"], source["zip"])
    assert "already exists" in str(e.value)
    bad = str(source["folder"] / "notzip.zip")
    open(bad, "wb").write(b"nope")
    with pytest.raises(M.CleanError):
        M.make_clean_backup(bad, str(source["folder"] / "never.zip"))


def test_restoring_the_clean_backup_wipes_the_data_and_keeps_people_and_settings(source):
    out = str(source["folder"] / "restore_me.zip")
    M.make_clean_backup(source["zip"], out)
    with Server() as live:
        live.admin_client().post("/api/customers", {"name": "Live Only"}).raise_for_status()
        harvest_totes(live.admin_client(), n=1)
        placed = os.path.join(live.tmp, "restore_me.zip")
        os.replace(out, placed)
        live.extra_env.update(KELP_ERP_RESTORE_FROM=placed, KELP_ERP_RESTORE_CONFIRM="replace-live-data")
        live.restart()
        admin = live.admin_client()
        assert admin.get("/api/customers").json["customers"] == []
        assert admin.get("/api/totes").json["totes"] == []
        assert admin.get("/api/release").json["runs"] == []
        assert admin.get("/api/release/verify").json["ok"] is True
        assert admin.get("/api/settings").json["settings"]
        keeper = live.client()
        assert keeper.login(source["keeper"]["email"], source["keeper"]["password"]).status == 200      # the same people, the same passwords
        assert glob.glob(os.path.join(live.tmp, "backups", "pre-restore-*.db.gz"))
        skus = {x["code"]: x for x in admin.get("/api/refdata").json["skus"]}                          # the older data was migrated forward when it was restored
        assert skus["MACRO-LKE"]["name"] == "Macrocystis Kelp Extract" and skus["MACRO-LKE"]["active"]
        assert sql(live, "SELECT COUNT(*) FROM consumables WHERE label_sku_code='MACRO-LKE'")[0][0] == 0
        assert sql(live, "SELECT COUNT(*) FROM consumables WHERE label_sku_code='KELPIVEX'")[0][0] == 2
        # and it works as a fresh system: harvest, run, ship
        rid = finished_run(admin)
        assert rid >= 1
        assert admin.post("/api/customers", {"name": "First Real Customer"}).status == 200


def test_the_command_line_prints_plain_ascii_and_a_summary(source):
    out = str(source["folder"] / "cli.zip")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_clean_backup.py"), source["zip"], out], capture_output=True)
    text = p.stdout.decode("ascii")                                                         # raises if anything is not ASCII
    assert p.returncode == 0 and "CLEAN BACKUP WRITTEN" in text and "customers" in text and "password hash" in text
    again = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_clean_backup.py"), source["zip"], out], capture_output=True)
    assert again.returncode == 1 and b"already exists" in again.stdout
