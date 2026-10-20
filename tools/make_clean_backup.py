"""Make a CLEAN KelpWorks backup: the configuration of a full backup without its records.

    py tools/make_clean_backup.py  kelpworks-full-backup.zip  kelpworks-CLEAN.zip

Reads a full backup (Admin > Download full backup, or a nightly backup .zip) and writes a new one that keeps the CONFIGURATION and has NO RECORDS. The
result is restored like any backup (staging: the Restore box; live: docs/restore-runbook.md), which wipes the data and starts fresh in one step. This tool
only reads the file you give it and writes a new one: it never touches a running system.

KEPT  users and their roles / permissions, species, sites, products (SKUs), locations, settings, Certificate of Analysis specifications, labs + analyses +
      requisition templates + contact details, quality-chart limits, the SOP and safety-data-sheet register, its links and files, inventory item definitions
      (their stock is set to 0), and the history of changes to those (settings, specifications, permissions, SOPs, chart limits).
CLEARED  every record: totes, pre-processing, production runs, finished goods, shipments and CUSTOMERS, disposals, samples, requisitions, lab results, the CIP
      log, stock movements, documents and photos, and the release / audit history (which starts again from nothing, so its hash chain stays valid).

Standard library only. Output is ASCII (the Windows console is cp1252).
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile

KEEP = {
    "app_flags", "coa_specs", "config_log", "consumable_pack_log", "consumable_packs", "consumable_supplier_log", "consumable_suppliers", "consumables", "container_units", "document_edits", "document_revisions", "feedstock_grade_rules", "fg_sku_species", "fg_skus",
    "lab_analyses", "labs", "locations", "qc_chart_limit_log", "qc_chart_limits", "requisition_contact", "sample_label_names", "sds_documents",
    "sds_items", "settings", "sites", "sop_document_edits", "sop_documents", "sop_links", "species", "user_permission_log", "users",
}
CLEAR = {
    "archive_receipts", "archive_runs", "cip_event_chemicals", "cip_events", "consumable_lot_log", "consumable_lots", "consumable_txns", "customers", "deleted_records", "disposals", "fg_lots", "inventory_receipts", "lab_requisitions",
    "lab_results", "library_inbox", "library_scans", "location_moves", "lot_uses", "preproc_batches", "preproc_inputs", "preproc_packaging", "production_runs",
    "qc_field_log", "qc_logs", "receipt_attachments", "receipt_log", "release_events", "requisition_samples", "run_amendments", "run_attachments", "run_dilution_passes", "run_dilutions",
    "run_edits", "run_inputs", "run_label_commits", "run_note_images", "run_notes", "run_packaging_commits", "run_packaging_entries", "run_reagent_commits", "run_revisions",
    "run_sample_points", "run_separation_solids", "sample_cart", "sample_events", "samples", "shipment_lines", "shipments", "tote_attachments", "tote_lots",
    "tote_ph_log", "tote_stability_log",
}
# folders inside a backup: configuration files are kept, uploaded documents and photos belong to the records
KEEP_FOLDERS = ("lab_templates", "sop_documents", "sds_documents")
CLEAR_FOLDERS = ("uploads",)
# bookkeeping about the backups of the OLD data
STALE_FLAGS = ("last_backup", "last_backup_error", "staging_restored_at")


class CleanError(Exception):
    pass


def safe_member(name):
    n = name.replace("\\", "/")
    parts = n.split("/")
    if n.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
        return None
    if n in ("kelp_erp.db", "manifest.json"):
        return n
    return n if parts[0] in KEEP_FOLDERS + CLEAR_FOLDERS and len(parts) > 1 and parts[-1] else None


def clean_database(src_path, dst_path):
    """Copy the database to dst_path with the records removed. Returns {table: rows removed}."""
    work = sqlite3.connect(src_path)
    try:
        if work.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CleanError("The database inside the backup is damaged; nothing was written.")
        tables = {r[0] for r in work.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if "users" not in tables:
            raise CleanError("That is not a KelpWorks database; nothing was written.")
        unknown = sorted(tables - KEEP - CLEAR)
        if unknown:
            raise CleanError("This backup has tables this tool does not know how to treat: %s. Update tools/make_clean_backup.py (add each to KEEP or CLEAR) "
                             "so no record is kept by mistake; nothing was written." % ", ".join(unknown))
        removed = {}
        work.execute("PRAGMA foreign_keys=OFF")
        work.execute("BEGIN")
        for t in sorted(CLEAR & tables):
            removed[t] = work.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            work.execute('DELETE FROM "%s"' % t)
        if "sqlite_sequence" in {r[0] for r in work.execute("SELECT name FROM sqlite_master")}:
            for t in sorted(CLEAR & tables):                                  # numbering starts again from 1 for the cleared tables
                work.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
        work.execute("UPDATE consumables SET on_hand=0")
        work.execute("DELETE FROM app_flags WHERE key IN (%s)" % ",".join("?" * len(STALE_FLAGS)), STALE_FLAGS)
        problems = work.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise CleanError("Clearing the records would leave %d broken reference(s) (first: %s); nothing was written." % (len(problems), tuple(problems[0])))
        work.commit()
        if os.path.exists(dst_path):
            os.remove(dst_path)
        work.execute("VACUUM INTO ?", (dst_path,))                            # a fresh file: the removed records are not left behind in free pages
        return removed
    finally:
        work.close()


def make_clean_backup(in_zip, out_zip):
    """-> {"removed": {table: n}, "kept": {table: n}, "files": {folder: n}, "droppedFiles": n}"""
    if os.path.exists(out_zip):
        raise CleanError("%s already exists; choose another name (nothing was overwritten)." % out_zip)
    try:
        zf = zipfile.ZipFile(in_zip)
    except (zipfile.BadZipFile, OSError):
        raise CleanError("%s is not a KelpWorks full backup (.zip)." % in_zip)
    tmp = tempfile.mkdtemp(prefix="kelp_clean_")
    try:
        with zf:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (KeyError, ValueError):
                raise CleanError("That zip has no manifest: use 'Download full backup' in the app.")
            if manifest.get("format") != "kelpworks-backup" or manifest.get("version") != 1:
                raise CleanError("That backup has a format this tool cannot read.")
            members = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if safe_member(info.filename) is None:
                    raise CleanError("The backup contains an unexpected file (%s); nothing was written." % info.filename[:80])
                members.append(info)
            if "kelp_erp.db" not in [m.filename for m in members]:
                raise CleanError("The backup has no database; nothing was written.")
            src_db, dst_db = os.path.join(tmp, "in.db"), os.path.join(tmp, "clean.db")
            with zf.open("kelp_erp.db") as s, open(src_db, "wb") as d:
                shutil.copyfileobj(s, d, 1 << 20)
            removed = clean_database(src_db, dst_db)
            check = sqlite3.connect(dst_db)
            try:
                kept = {t: check.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in sorted(KEEP) if check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()}
            finally:
                check.close()
            files, dropped = {f: 0 for f in KEEP_FOLDERS}, 0
            part = out_zip + ".partial"
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
                out.write(dst_db, "kelp_erp.db")
                for info in members:
                    name = info.filename.replace("\\", "/")
                    top = name.split("/")[0]
                    if name in ("kelp_erp.db", "manifest.json"):
                        continue
                    if top in CLEAR_FOLDERS:
                        dropped += 1
                        continue
                    with zf.open(info) as s, out.open(zipfile.ZipInfo(name, date_time=info.date_time), "w") as d:
                        shutil.copyfileobj(s, d, 1 << 20)
                    files[top] += 1
                manifest = dict(manifest, files=dict(files, uploads=0), cleaned=True, cleanedFrom=manifest.get("createdAt"))
                out.writestr("manifest.json", json.dumps(manifest, indent=2))
            os.replace(part, out_zip)
        return {"removed": removed, "kept": kept, "files": files, "droppedFiles": dropped}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(out_zip + ".partial"):
            os.remove(out_zip + ".partial")


def main(argv):
    if len(argv) != 3:
        print("Usage: py tools/make_clean_backup.py <full-backup.zip> <clean-backup.zip>")
        return 2
    try:
        r = make_clean_backup(argv[1], argv[2])
    except CleanError as e:
        print("STOPPED: " + str(e))
        return 1
    print("CLEAN BACKUP WRITTEN: %s" % argv[2])
    print("")
    print("Records cleared (%d rows in %d tables), documents and photos dropped: %d" % (sum(r["removed"].values()), sum(1 for n in r["removed"].values() if n), r["droppedFiles"]))
    for t, n in sorted(r["removed"].items()):
        if n:
            print("  %-26s %d" % (t, n))
    print("Configuration kept:")
    for t, n in sorted(r["kept"].items()):
        print("  %-26s %d" % (t, n))
    print("Files kept: %s" % ", ".join("%s %d" % kv for kv in sorted(r["files"].items())))
    print("")
    print("This file still holds every user's password hash: keep it private, and delete it when you are done.")
    print("Next: restore it on STAGING first (Admin > Restore), then on live with docs/restore-runbook.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
