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
        # KW-60: inventory items with suppliers, packs and a safety data sheet link; the four system items carry test values; a deleted tote (its number is never reused)
        sql(s, "UPDATE consumables SET cost_per_unit=5.5, reorder_level=40, organic=1, location='Shelf 9', last_counted_by='Test' WHERE name='Citric Acid'")
        citric = sql(s, "SELECT id FROM consumables WHERE name='Citric Acid'")[0][0]
        sql(s, "INSERT INTO consumables (name,unit,on_hand,reorder_level,reagent_type,cost_per_unit) VALUES ('Real Citric Acid (Supplier B)','kg',10,0,'Citric Acid',4.2)")
        other = sql(s, "SELECT id FROM consumables WHERE name='Real Citric Acid (Supplier B)'")[0][0]
        for cid, name in ((citric, "Acme Chemicals"), (other, "Supplier B Ltd")):
            sql(s, "INSERT INTO consumable_suppliers (consumable_id,name,created_by,created_at) VALUES (?,?,'x','2026-10-01T00:00:00Z')", (cid, name))
            sql(s, "INSERT INTO consumable_supplier_log (consumable_id,supplier,action,user_name,created_at) VALUES (?,?,'added','x','2026-10-01T00:00:00Z')", (cid, name))
            sql(s, "INSERT INTO consumable_packs (consumable_id,name,qty_per_pack,created_at) VALUES (?, '25 kg bag', 25, '2026-10-01T00:00:00Z')", (cid,))
            sql(s, "INSERT INTO consumable_pack_log (consumable_id,event,detail,created_at) VALUES (?, 'added', '25 kg bag', '2026-10-01T00:00:00Z')", (cid,))
        sql(s, "INSERT INTO sds_documents (chemical,supplier,sds_key,updated_at) VALUES ('Citric Acid','Acme','citric acid|acme','2026-10-01T00:00:00Z')")
        for cid in (citric, other):
            sql(s, "INSERT INTO sds_items (sds_id,consumable_id) SELECT id, ? FROM sds_documents WHERE sds_key='citric acid|acme'", (cid,))
        gone = harvest_totes(admin, n=1, date="2026-06-01")[0]
        assert admin.delete("/api/totes/%d" % gone).ok
        assert sql(s, "SELECT COUNT(*) FROM deleted_records")[0][0] >= 1
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


# ---- KW-60: inventory items, the one audit entry, the proof, the dry run, numbering ---------------------------------------------------------------------------------------------------------
def clean_db(source, name, **kw):
    out = str(source["folder"] / (name + ".zip"))
    r = M.make_clean_backup(source["zip"], out, **kw)
    folder = source["folder"] / (name + "_db")
    with zipfile.ZipFile(out) as zf:
        zf.extract("kelp_erp.db", str(folder))
    return out, r, sqlite3.connect(str(folder / "kelp_erp.db"))


def test_only_the_four_system_items_remain_and_they_are_blank_and_everything_that_hung_on_the_other_items_is_gone(source):
    s = source["server"]
    assert sql(s, "SELECT COUNT(*) FROM consumable_suppliers")[0][0] >= 2 and sql(s, "SELECT COUNT(*) FROM sds_items")[0][0] >= 2 and sql(s, "SELECT COUNT(*) FROM consumable_packs")[0][0] >= 2
    out, r, conn = clean_db(source, "items")
    try:
        names = [x[0] for x in conn.execute("SELECT name FROM consumables ORDER BY name")]
        assert names == sorted(M.SYSTEM_ITEMS)
        citric = conn.execute("SELECT on_hand, reorder_level, cost_per_unit, location, organic, last_counted_by, reagent_type, name FROM consumables WHERE name='Citric Acid'").fetchone()
        assert citric == (0, 0, None, None, 0, None, "Citric Acid", "Citric Acid")                      # blank, but the name and the reagent type the code looks for remain
        ibc = conn.execute("SELECT is_container, litres_each FROM consumables WHERE name='New 1,000 L IBC Tote'").fetchone()
        assert ibc == (1, 1000.0)                                                                       # the container flags the packaging relies on remain
        for t in M.ITEM_DEPENDENTS:
            assert conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] == 0, t
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
    assert "Real Citric Acid (Supplier B)" in r["itemsRemoved"] and "FG Label - old Macro" in r["itemsRemoved"] and "Citric Acid" not in r["itemsRemoved"]
    assert {"Acme Chemicals", "Supplier B Ltd"} <= set(r["suppliers"])                                 # the names are listed so they can be entered again


def test_exactly_one_clean_start_entry_is_added_to_the_change_history_and_the_old_history_is_untouched(source):
    before = sqlite3.connect(str(source["folder"] / "audit_src.db"))
    with zipfile.ZipFile(source["zip"]) as zf:
        zf.extract("kelp_erp.db", str(source["folder"] / "audit_src"))
    before = sqlite3.connect(str(source["folder"] / "audit_src" / "kelp_erp.db"))
    old = before.execute("SELECT area,key,field,old_value,new_value,changed_by,changed_at FROM config_log ORDER BY id").fetchall()
    before.close()
    out, r, conn = clean_db(source, "audit", by="N. Wrana")
    try:
        rows = conn.execute("SELECT area,key,field,old_value,new_value,changed_by,changed_at FROM config_log ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows[:-1] == old and len(rows) == len(old) + 1
    area, key, field, old_value, new_value, by, at = rows[-1]
    assert (area, key, field, by) == ("system", "clean_start", "reset", "N. Wrana")
    assert "records cleared" in old_value and "numbering restarted at 001" in old_value and "full.zip" in new_value and "sha256" in new_value
    with zipfile.ZipFile(out) as zf:
        assert json.loads(zf.read("manifest.json"))["cleanStart"]["by"] == "N. Wrana"


def test_the_tool_proves_the_configuration_is_unchanged_and_refuses_a_file_where_it_is_not(source):
    out, r, conn = clean_db(source, "proof")
    conn.close()
    assert all(v == "unchanged" for t, v in r["verified"].items() if t in M.KEEP - M.INTENDED) and r["kept"]["settings"] > 0
    # a doctored result is caught: change one setting in the finished file and run the check again
    with zipfile.ZipFile(source["zip"]) as zf:
        zf.extract("kelp_erp.db", str(source["folder"] / "proof_src"))
    src = sqlite3.connect(str(source["folder"] / "proof_src" / "kelp_erp.db"))
    tables = {x[0] for x in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    before = M.baseline(src, tables)
    src.close()
    doctored = sqlite3.connect(str(source["folder"] / "proof_db" / "kelp_erp.db"))
    doctored.execute("UPDATE settings SET value=value+1 WHERE key='sds_review_years'")
    doctored.commit()
    doctored.close()
    with pytest.raises(M.CleanError) as e:
        M.verify_clean(before, str(source["folder"] / "proof_db" / "kelp_erp.db"), tables)
    assert "settings changed" in str(e.value)


def test_a_dry_run_writes_nothing_and_reports_the_same_numbers_as_the_real_run(source):
    names = sorted(os.listdir(str(source["folder"])))
    dry = M.make_clean_backup(source["zip"], None, dry_run=True)
    assert sorted(os.listdir(str(source["folder"]))) == names and dry["dryRun"] is True and "report" not in dry
    out, real, conn = clean_db(source, "dryreal")
    conn.close()
    assert dry["removed"] == real["removed"] and dry["itemsRemoved"] == real["itemsRemoved"] and dry["kept"] == real["kept"]
    names = sorted(os.listdir(str(source["folder"])))                                                  # (the real run above wrote its own files)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_clean_backup.py"), source["zip"], "--dry-run"], capture_output=True)
    text = p.stdout.decode("ascii")
    assert p.returncode == 0 and "DRY RUN" in text and "nothing was written" in text and "Inventory items removed" in text and "customers" in text
    assert sorted(os.listdir(str(source["folder"]))) == names                                         # still nothing new on disk


def test_the_report_and_the_draft_cut_over_note_are_written_next_to_the_zip(source):
    out, r, conn = clean_db(source, "paper", by="D. Pedde")
    conn.close()
    report, note = open(r["report"], encoding="utf-8").read(), open(r["note"], encoding="utf-8").read()
    assert r["report"] == out[:-4] + ".report.txt" and r["note"] == out[:-4] + ".cut-over-note.md"
    assert "verification report" in report and "Supplier B Ltd" in report and "Real Citric Acid (Supplier B)" in report and "full.zip" in report and "sha256" in report.lower()
    assert "customers" in report and "unchanged" in report and "clean_start" in report and "no passwords" in report
    assert "cut-over note (DRAFT" in note and "QM-001" in note and "D. Pedde" in note and "PR-<date>-001" in note and "KW-64" in note and "Quality Manager: ____" in note
    assert "Real Citric Acid (Supplier B)" in note and "Citric Acid, Potassium Sorbate" in note


def test_a_source_without_a_system_item_is_warned_about_not_stopped(source):
    with zipfile.ZipFile(source["zip"]) as zf:
        zf.extract("kelp_erp.db", str(source["folder"] / "nosys"))
    db = str(source["folder"] / "nosys" / "kelp_erp.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE consumables SET name='Citric Acid (renamed)' WHERE name='Citric Acid'")
    conn.commit()
    conn.close()
    r = M.clean_database(db, str(source["folder"] / "nosys" / "clean.db"))
    assert r["systemItemsMissing"] == ["Citric Acid"] and "Citric Acid (renamed)" in r["itemsRemoved"]


def test_after_the_restore_the_system_items_work_and_every_number_starts_at_001(source):
    out = str(source["folder"] / "restore_numbers.zip")
    M.make_clean_backup(source["zip"], out)
    with Server() as live:
        placed = os.path.join(live.tmp, "restore_numbers.zip")
        os.replace(out, placed)
        live.extra_env.update(KELP_ERP_RESTORE_FROM=placed, KELP_ERP_RESTORE_CONFIRM="replace-live-data")
        live.restart()
        admin = live.admin_client()
        assert sql(live, "SELECT name FROM consumables WHERE name IN ('Citric Acid','Potassium Sorbate','New 1,000 L IBC Tote','Used 1,000 L IBC Tote') ORDER BY name")                 # found by name
        totes = admin.get("/api/totes")                                                                                                                                      # (the totes list is empty)
        assert totes.json["totes"] == []
        tid = harvest_totes(admin, n=1, date="2026-06-01")[0]                                                                                                                # that day's number 001 was deleted before the wipe: it is free again
        assert sql(live, "SELECT lot_number FROM tote_lots WHERE id=?", (tid,))[0][0].endswith("-20260601-001")
        rid = finished_run(admin)                                                                                                                                            # packaged in the (blank) New IBC container; the empty-IBC pool and the reagents are found
        assert sql(live, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0].endswith("-001") and rid == 1
        assert sql(live, "SELECT on_hand FROM consumables WHERE name='Used 1,000 L IBC Tote'")[0][0] >= 1                                                                    # the pool counted the emptied tote
        assert sql(live, "SELECT COUNT(*) FROM config_log WHERE area='system' AND key='clean_start'")[0][0] == 1
        assert admin.get("/api/release/verify").json["ok"] is True
        seq = {r[0] for r in sql(live, "SELECT name FROM sqlite_sequence")}
        assert "preproc_batches" not in seq and "cip_events" not in seq and "lab_requisitions" not in seq                                                                    # BL lot, CIP ref and requisition numbers start again at 001


def test_the_command_line_takes_by_and_prints_where_the_paper_is(source):
    out = str(source["folder"] / "cli2.zip")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_clean_backup.py"), source["zip"], out, "--by", "N. Wrana"], capture_output=True)
    text = p.stdout.decode("ascii")
    assert p.returncode == 0 and "cli2.report.txt" in text and "cli2.cut-over-note.md" in text and "Inventory items removed" in text
    assert os.path.exists(out[:-4] + ".report.txt") and os.path.exists(out[:-4] + ".cut-over-note.md")
    bad = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_clean_backup.py")], capture_output=True)
    assert bad.returncode == 2 and b"--dry-run" in bad.stdout
