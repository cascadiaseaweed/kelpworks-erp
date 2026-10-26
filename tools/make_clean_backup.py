"""Make a CLEAN KelpWorks backup: the configuration of a full backup without its records.

    py tools/make_clean_backup.py  kelpworks-full-backup.zip  kelpworks-CLEAN.zip  [--by "Your Name"]
    py tools/make_clean_backup.py  kelpworks-full-backup.zip  --dry-run            (shows what would happen, writes nothing)

Reads a full backup (Admin > Download full backup, or a nightly backup .zip) and writes a new one that keeps the CONFIGURATION and has NO RECORDS. The
result is restored like any backup (staging: the Restore box; live: docs/restore-runbook.md), which wipes the data and starts fresh in one step. This tool
only reads the file you give it and writes new files: it never touches a running system.

KEPT  users and their roles / permissions, species, sites, products (SKUs), locations, settings, Certificate of Analysis specifications, labs + analyses +
      requisition templates + contact details, quality-chart limits, the SOP and safety-data-sheet register, its links and files, and the history of changes
      to those (settings, specifications, permissions, SOPs, chart limits). Every one of these tables is checked, row for row, to be identical afterwards.
CLEARED  every record: totes, pre-processing, production runs, finished goods, shipments and CUSTOMERS, disposals, samples, requisitions, lab results, the CIP
      log, stock movements, documents and photos, and the release / audit history (which starts again from nothing, so its hash chain stays valid). Every
      number (run, lot, tote, batch, CIP, requisition) starts again at 001.
INVENTORY ITEMS (KW-60)  every reagent, packaging and finished-good-label item is REMOVED (with its suppliers, packs, safety-data-sheet links and history) so
      it can be added again with real quantities and documents -- except the four items the program finds by their exact name (SYSTEM_ITEMS), which stay as
      BLANK items (stock 0, no cost, no reorder level, no suppliers / packs / links). The server recreates the other standard items at every start.
ONE AUDIT ENTRY  a row in the change history (config_log, area 'system', key 'clean_start') records who made the clean file, from which backup (name and
      checksum) and how much was cleared. Next to the zip the tool writes <name>.report.txt (what changed, with a proof that nothing else did) and
      <name>.cut-over-note.md (a draft cut-over note for the Quality Manager).

Standard library only. Output is ASCII (the Windows console is cp1252).
"""
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

KEEP = {
    "app_flags", "coa_specs", "config_log", "consumable_name_history", "consumable_pack_log", "consumable_packs", "consumable_supplier_log", "consumable_suppliers", "consumables", "container_units", "document_edits", "document_revisions", "feedstock_grade_rules", "fg_sku_species", "fg_skus",
    "lab_analyses", "labs", "locations", "qc_chart_limit_log", "qc_chart_limits", "requisition_contact", "sample_label_names", "sds_documents",
    "sds_items", "settings", "sites", "sop_document_edits", "sop_documents", "sop_links", "species", "supplier_log", "suppliers", "user_permission_log", "users",
}
CLEAR = {
    "image_purge_log", "archive_receipts", "archive_runs", "cip_event_chemicals", "cip_events", "consumable_lot_log", "consumable_lots", "consumable_txns", "customers", "deleted_records", "disposals", "document_reads", "fg_lots", "inventory_receipts", "lab_requisitions",
    "lab_results", "library_inbox", "library_scans", "location_moves", "lot_uses", "preproc_batches", "preproc_inputs", "preproc_packaging", "production_runs",
    "qc_field_log", "qc_logs", "receipt_attachments", "receipt_log", "release_events", "requisition_samples", "run_amendments", "run_attachments", "run_dilution_passes", "run_dilutions",
    "run_edits", "run_inputs", "run_label_commits", "run_note_images", "run_notes", "run_packaging_commits", "run_packaging_entries", "run_reagent_commits", "run_revisions",
    "run_sample_points", "run_separation_solids", "sample_cart", "sample_events", "samples", "shipment_lines", "shipments", "supplier_documents", "tote_attachments", "tote_lots",
    "tote_ph_log", "tote_stability_log",
}
# folders inside a backup: configuration files are kept, uploaded documents and photos belong to the records
KEEP_FOLDERS = ("lab_templates", "sop_documents", "sds_documents")
CLEAR_FOLDERS = ("uploads",)
# bookkeeping about the backups of the OLD data
STALE_FLAGS = ("last_backup", "last_backup_error", "staging_restored_at")
# KW-60: the four inventory items the program finds by their exact name (reagent types Citric Acid / Potassium Sorbate, the packaging container, the empty-IBC pool): they stay, blank
# KW-89: they are found by their stable key (system_key), so a renamed one is still kept; a backup made before the keys existed is read by the standard names
SYSTEM_KEYS = (("citric_acid", "Citric Acid"), ("potassium_sorbate", "Potassium Sorbate"), ("ibc_new", "New 1,000 L IBC Tote"), ("ibc_used", "Used 1,000 L IBC Tote"))
SYSTEM_ITEMS = tuple(n for _k, n in SYSTEM_KEYS)                          # the standard names (the fallback, and what the reports call them when nothing was renamed)
# what hangs on an inventory item, children first (the rows of every item go: the four blank items have none)
ITEM_DEPENDENTS = ("consumable_pack_log", "consumable_supplier_log", "consumable_packs", "consumable_suppliers", "sds_items", "consumable_name_history")
BLANK_ITEM = {"on_hand": 0, "reorder_level": 0, "cost_per_unit": None, "location": None, "organic": 0, "last_counted_at": None, "last_counted_by": None}
# KEEP tables that are MEANT to differ afterwards (everything else in KEEP must be identical, row for row)
INTENDED = {"consumables", "app_flags", "config_log"} | set(ITEM_DEPENDENTS)


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


def ascii_safe(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def table_digest(conn, table, where="1=1", params=()):
    """(row count, SHA-256 of every row in rowid order) of a table, so two copies can be compared row for row."""
    h, n = hashlib.sha256(), 0
    for row in conn.execute('SELECT * FROM "%s" WHERE %s ORDER BY rowid' % (table, where), params):
        h.update(repr(tuple(row)).encode("utf-8", "replace"))
        n += 1
    return n, h.hexdigest()


def baseline(conn, tables):
    """What every KEEP table looks like before anything is removed."""
    base = {t: table_digest(conn, t) for t in sorted(KEEP & tables)}
    stale = ",".join("?" * len(STALE_FLAGS))
    base["app_flags (without the old-backup flags)"] = table_digest(conn, "app_flags", "key NOT IN (%s)" % stale, STALE_FLAGS) if "app_flags" in tables else (0, "")
    base["_config_log_max_rowid"] = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM config_log").fetchone()[0]
    return base


def find_system_items(conn):
    """(the items to keep as [(id, current name)], the standard names that were not found). By key; by standard name for a backup that predates the keys (KW-89)."""
    has_key = "system_key" in {r[1] for r in conn.execute("PRAGMA table_info(consumables)")}
    kept, missing = [], []
    for key, name in SYSTEM_KEYS:
        row = conn.execute("SELECT id, name FROM consumables WHERE system_key=?", (key,)).fetchone() if has_key else None
        row = row or conn.execute("SELECT id, name FROM consumables WHERE name=?", (name,)).fetchone()
        if row and row[0] not in [k[0] for k in kept]:
            kept.append((row[0], row[1]))
        else:
            missing.append(name)
    return kept, missing


def verify_clean(before, dst_path, tables, kept_names=None):
    """Open the finished clean file and prove it: every record table is empty, every KEEP table is unchanged except the intended ones, the intended ones changed
    only as planned. -> {table: 'unchanged' | description}. Raises CleanError."""
    conn = sqlite3.connect(dst_path)
    try:
        problems, verdict = [], {}
        for t in sorted(CLEAR & tables):
            if conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]:
                problems.append("%s still has rows" % t)
            if conn.execute("SELECT COUNT(*) FROM sqlite_sequence WHERE name=?", (t,)).fetchone()[0] if "sqlite_sequence" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")} else 0:
                problems.append("%s still has a number sequence" % t)
        for t in sorted((KEEP & tables) - INTENDED):
            after = table_digest(conn, t)
            if after != before[t]:
                problems.append("%s changed (%d rows before, %d after)" % (t, before[t][0], after[0]))
            verdict[t] = "unchanged"
        names = {r[0] for r in conn.execute("SELECT name FROM consumables")}
        allowed = set(kept_names) if kept_names is not None else set(SYSTEM_ITEMS)
        if names - allowed:
            problems.append("inventory items other than the system items remain: %s" % ", ".join(sorted(names - allowed)[:5]))
        for t in [d for d in ITEM_DEPENDENTS if d in tables]:
            if conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]:
                problems.append("%s still has rows" % t)
        for name in names:
            row = conn.execute("SELECT on_hand, reorder_level, cost_per_unit FROM consumables WHERE name=?", (name,)).fetchone()
            if row[0] or row[1] or row[2] is not None:
                problems.append("system item %s is not blank" % name)
        stale = ",".join("?" * len(STALE_FLAGS))
        if table_digest(conn, "app_flags", "key NOT IN (%s)" % stale, STALE_FLAGS) != before["app_flags (without the old-backup flags)"]:
            problems.append("app_flags changed beyond the old-backup flags")
        old_n = before["config_log"][0]
        if table_digest(conn, "config_log", "rowid<=?", (before["_config_log_max_rowid"],)) != before["config_log"] or conn.execute("SELECT COUNT(*) FROM config_log").fetchone()[0] != old_n + 1:
            problems.append("config_log is not the old history plus exactly one new row")
        if problems:
            raise CleanError("The clean file failed its own check (%s); nothing was written." % "; ".join(problems))
        verdict["consumables"] = "only the %d system items remain, blank" % len(names)
        verdict["inventory-item dependents"] = "emptied (suppliers, packs, safety-data-sheet links, their logs)"
        verdict["app_flags"] = "only the old-backup flags removed (the one-time migration flags stay)"
        verdict["config_log"] = "the old history unchanged, plus the one clean_start row"
        return verdict
    finally:
        conn.close()


def clean_database(src_path, dst_path, source_name="", source_sha="", by=""):
    """Copy the database to dst_path with the records removed and prove the result. Returns a dict (see make_clean_backup)."""
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
        if "consumables" not in tables or "config_log" not in tables:
            raise CleanError("This backup is from a much older release (it has no inventory or no change history table). Restore it on staging first, or take a fresh full backup, "
                             "and make the clean file from that; nothing was written.")
        deps = [t for t in ITEM_DEPENDENTS if t in tables]                    # an older release may not have every one of them yet
        before = baseline(work, tables)
        has_seq = "sqlite_sequence" in {r[0] for r in work.execute("SELECT name FROM sqlite_master")}
        kept_items, missing_system = find_system_items(work)
        kept_ids = [i for i, _n in kept_items]
        items_removed = [n for i, n in work.execute("SELECT id, name FROM consumables ORDER BY name") if i not in kept_ids]
        suppliers = sorted({r[0] for r in work.execute("SELECT name FROM consumable_suppliers")}, key=str.lower) if "consumable_suppliers" in tables else []
        removed = {}
        work.execute("PRAGMA foreign_keys=OFF")
        work.execute("BEGIN")
        for t in sorted(CLEAR & tables):
            removed[t] = work.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            work.execute('DELETE FROM "%s"' % t)
        for t in deps:                                                         # children first
            removed[t] = work.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            work.execute('DELETE FROM "%s"' % t)
        removed["consumables (items removed)"] = len(items_removed)
        if kept_ids:
            work.execute("DELETE FROM consumables WHERE id NOT IN (%s)" % ",".join("?" * len(kept_ids)), kept_ids)
        else:
            work.execute("DELETE FROM consumables")
        cols = {r[1] for r in work.execute("PRAGMA table_info(consumables)")}
        blank = {c: v for c, v in BLANK_ITEM.items() if c in cols}
        work.execute("UPDATE consumables SET %s" % ", ".join("%s=?" % c for c in blank), tuple(blank.values()))
        if has_seq:
            for t in sorted((CLEAR & tables) | set(deps)):                      # numbering starts again from 1 for the cleared tables
                work.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
        work.execute("DELETE FROM app_flags WHERE key IN (%s)" % ",".join("?" * len(STALE_FLAGS)), STALE_FLAGS)
        total = sum(n for t, n in removed.items() if t in CLEAR)
        entry = ("system", "clean_start", "reset",
                 "%d records cleared from %d tables; %d inventory items removed; numbering restarted at 001" % (total, sum(1 for t in CLEAR if removed.get(t)), len(items_removed)),
                 "clean file made from %s (sha256 %s)" % (source_name or "a full backup", (source_sha or "unknown")[:16]),
                 by or "tools/make_clean_backup.py", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        work.execute("INSERT INTO config_log (area,key,field,old_value,new_value,changed_by,changed_at) VALUES (?,?,?,?,?,?,?)", entry)
        problems = work.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise CleanError("Clearing the records would leave %d broken reference(s) (first: %s); nothing was written." % (len(problems), tuple(problems[0])))
        work.commit()
        if os.path.exists(dst_path):
            os.remove(dst_path)
        work.execute("VACUUM INTO ?", (dst_path,))                            # a fresh file: the removed records are not left behind in free pages
    finally:
        work.close()
    try:
        verified = verify_clean(before, dst_path, tables, [n for _i, n in kept_items])
    except CleanError:
        if os.path.exists(dst_path):
            os.remove(dst_path)
        raise
    check = sqlite3.connect(dst_path)
    try:
        kept = {t: check.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in sorted(KEEP & tables)}
    finally:
        check.close()
    return {"removed": removed, "kept": kept, "before": {t: v[0] for t, v in before.items() if isinstance(v, tuple)}, "verified": verified, "itemsRemoved": items_removed,
            "systemItems": [n for _i, n in kept_items], "systemItemsMissing": missing_system, "suppliers": suppliers, "entry": dict(zip(("area", "key", "field", "old", "new", "by", "at"), entry))}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report(res, meta):
    r = res
    L = ["KelpWorks clean start - verification report (KW-60)", "=" * 52, "",
         "Source backup : %s" % meta["source"], "  sha256      : %s" % meta["sha"], "  created at  : %s" % meta.get("created", "unknown"),
         "Clean file    : %s" % meta.get("out", "(dry run: nothing written)"), "Made by       : %s on %s" % (meta["by"] or "unknown", meta["when"]), "",
         "RECORDS CLEARED (%d rows in %d tables; numbering restarts at 001)" % (sum(n for t, n in r["removed"].items() if t in CLEAR), sum(1 for t in CLEAR if r["removed"].get(t)))]
    for t in sorted(CLEAR):
        if r["removed"].get(t) is not None:
            L.append("  %-28s %6d" % (t, r["removed"][t]))
    L += ["", "INVENTORY ITEMS REMOVED (%d); the %d system items stay as blank items: %s" % (len(r["itemsRemoved"]), len(r["systemItems"]), ", ".join(r["systemItems"]))]
    L += ["  " + n for n in r["itemsRemoved"]] or ["  (none)"]
    if r["systemItemsMissing"]:
        L += ["", "WARNING: the source has no inventory item for: %s. The program looks these up by key (or by this name in an older backup); add them in Inventory Items after the restore." % ", ".join(r["systemItemsMissing"])]
    for t in ITEM_DEPENDENTS:
        L.append("  %-28s %6d rows removed with the items" % (t, r["removed"].get(t, 0)))
    L += ["", "Approved supplier names that were on the removed items (re-enter them when the items are added again):"]
    L += ["  " + n for n in r["suppliers"]] or ["  (none)"]
    L += ["", "CONFIGURATION KEPT, CHECKED ROW FOR ROW (rows in the source -> rows in the clean file)"]
    for t in sorted(r["kept"]):
        L.append("  %-28s %6s -> %-6s %s" % (t, r["before"].get(t, "?"), r["kept"][t], r["verified"].get(t, "")))
    L += ["", "Intended differences: %s; %s; %s; %s." % (r["verified"]["consumables"], r["verified"]["inventory-item dependents"], r["verified"]["app_flags"], r["verified"]["config_log"]),
          "", "AUDIT ENTRY written to the change history (config_log):", "  area=%(area)s key=%(key)s field=%(field)s" % r["entry"], "  %(old)s" % r["entry"], "  %(new)s" % r["entry"],
          "  by %(by)s at %(at)s" % r["entry"], "", "This report proves the clean file was checked before it was written. It contains no passwords."]
    return "\n".join(L) + "\n"


def build_cut_over_note(res, meta):
    r = res
    cleared = sum(n for t, n in r["removed"].items() if t in CLEAR)
    L = ["# KelpWorks clean start - cut-over note (DRAFT for the Quality Manager)", "",
         "Record for QM-001 (change KW-60: clean start of the live production database, wipe only). Fill in the blanks, sign, and file it with the pre-go-live backup.", "",
         "| | |", "|---|---|", "| Prepared by | %s |" % (meta["by"] or "____________"), "| Date prepared | %s |" % meta["when"], "| Source backup | %s |" % meta["source"],
         "| Source checksum (sha256) | %s |" % meta["sha"], "| Source backup made at | %s |" % meta.get("created", "unknown"), "| Clean file | %s |" % meta.get("out", "(dry run)"),
         "| Restored on live by / on | ____________ / ____________ |", "", "## Why", "",
         "The live database held pre-go-live test data. All records were removed and the configuration kept so that real production can start from a clean database. Rebuilding history (received stock, historical runs, lots 1-9) is a separate change (KW-64).", "",
         "## What was removed", "", "%d record rows from %d tables:" % (cleared, sum(1 for t in CLEAR if r["removed"].get(t))), "", "| Table | Rows |", "|---|---|"]
    L += ["| %s | %d |" % (t, r["removed"][t]) for t in sorted(CLEAR) if r["removed"].get(t)]
    L += ["", "Uploaded documents and photos of those records were dropped with them. They remain only in the pre-go-live backup above.", "",
          "**Inventory items removed (%d):** %s" % (len(r["itemsRemoved"]), ", ".join(r["itemsRemoved"]) or "none"),
          "", "They are added again, with real quantities, suppliers and documents, under KW-64. The four items the program finds by key (%s) were kept as blank items: stock 0, no cost, no suppliers, no links." % ", ".join(r["systemItems"]), "",
          "## What was kept", "", "Users, roles and passwords; settings; products and specifications; Certificate of Analysis specifications; the lab catalogue and requisition templates; the SOP and safety-data-sheet register and files; quality-chart limits; "
          "species, sites and locations; and the history of changes to those. The tool compared every one of these tables row for row before and after: all identical (details in the verification report).", "",
          "## Numbering", "", "Run (processing lot), tote, pre-processing batch, CIP and requisition numbers start again at 001 (for example the next production run is PR-<date>-001). Old numbers are not reused anywhere else: the old records exist only in the pre-go-live backup.", "",
          "## Audit entry", "", "One entry was written to the change history of the clean database: `%(area)s / %(key)s`: %(old)s; %(new)s; by %(by)s at %(at)s." % r["entry"], "",
          "## Outside this database", "", "- SharePoint library KelpWorks-Records: the old run, receiving and system-backup folders and `_sync-state.json` were deleted on ____________ by ____________ (the SOP and safety-data-sheet folders and the change log were not touched).",
          "- Pre-go-live backup stored at: ____________________ (restore-tested on ____________).", "",
          "## Verification after the restore (to be completed)", "", "- [ ] Every record table is empty; the next run / lot / tote / batch number is 001",
          "- [ ] Settings, SOP links, product specifications, lab catalogue and users unchanged", "- [ ] Verify audit trail reports the chain intact", "- [ ] The `clean_start` entry is present in the change history", "",
          "Prepared by: ____________  Quality Manager: ____________  Date: ____________", ""]
    return "\n".join(L)


def make_clean_backup(in_zip, out_zip=None, by="", dry_run=False):
    """-> {"removed", "kept", "before", "verified", "itemsRemoved", "suppliers", "files", "droppedFiles", "entry", ...; unless dry_run: "report", "note"}"""
    if not dry_run:
        if not out_zip:
            raise CleanError("Give the name of the clean file to write (or use --dry-run).")
        if os.path.exists(out_zip):
            raise CleanError("%s already exists; choose another name (nothing was overwritten)." % out_zip)
    try:
        zf = zipfile.ZipFile(in_zip)
    except (zipfile.BadZipFile, OSError):
        raise CleanError("%s is not a KelpWorks full backup (.zip)." % in_zip)
    sha = sha256_file(in_zip)
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
            when = datetime.date.today().isoformat()
            res = clean_database(src_db, dst_db, os.path.basename(in_zip), sha, by)
            files, dropped = {f: 0 for f in KEEP_FOLDERS}, 0
            for info in members:
                top = info.filename.replace("\\", "/").split("/")[0]
                if top in CLEAR_FOLDERS:
                    dropped += 1
                elif info.filename not in ("kelp_erp.db", "manifest.json"):
                    files[top] += 1
            res.update(files=files, droppedFiles=dropped)
            meta = {"source": os.path.basename(in_zip), "sha": sha, "created": manifest.get("createdAt"), "by": by, "when": when, "out": os.path.basename(out_zip) if out_zip else None}
            if dry_run:
                res["dryRun"] = True
                return res
            part = out_zip + ".partial"
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
                out.write(dst_db, "kelp_erp.db")
                for info in members:
                    name = info.filename.replace("\\", "/")
                    if name in ("kelp_erp.db", "manifest.json") or name.split("/")[0] in CLEAR_FOLDERS:
                        continue
                    with zf.open(info) as s, out.open(zipfile.ZipInfo(name, date_time=info.date_time), "w") as d:
                        shutil.copyfileobj(s, d, 1 << 20)
                manifest = dict(manifest, files=dict(files, uploads=0), cleaned=True, cleanedFrom=manifest.get("createdAt"),
                                cleanStart={"by": by, "date": when, "sourceFile": meta["source"], "sourceSha256": sha})
                out.writestr("manifest.json", json.dumps(manifest, indent=2))
            os.replace(part, out_zip)
        base = out_zip[:-4] if out_zip.lower().endswith(".zip") else out_zip
        res["report"], res["note"] = base + ".report.txt", base + ".cut-over-note.md"
        with open(res["report"], "w", encoding="utf-8") as f:
            f.write(build_report(res, meta))
        with open(res["note"], "w", encoding="utf-8") as f:
            f.write(build_cut_over_note(res, meta))
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if out_zip and os.path.exists(out_zip + ".partial"):
            os.remove(out_zip + ".partial")


def main(argv):
    args, by, dry = [], "", False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--dry-run":
            dry = True
        elif a == "--by" and i + 1 < len(argv):
            i += 1
            by = argv[i]
        elif a.startswith("--"):
            args = None
            break
        else:
            args.append(a)
        i += 1
    if args is None or not (len(args) == 2 or (dry and len(args) in (1, 2))):
        print("Usage: py tools/make_clean_backup.py <full-backup.zip> <clean-backup.zip> [--by \"Your Name\"]")
        print("       py tools/make_clean_backup.py <full-backup.zip> --dry-run      (shows what would happen, writes nothing)")
        return 2
    by = by or os.environ.get("USERNAME") or os.environ.get("USER") or ""
    try:
        r = make_clean_backup(args[0], None if dry else args[1], by, dry)
    except CleanError as e:
        print("STOPPED: " + ascii_safe(e))
        return 1
    print("DRY RUN: nothing was written. This is what would happen:" if dry else "CLEAN BACKUP WRITTEN: %s" % args[1])
    print("")
    cleared = sum(n for t, n in r["removed"].items() if t in CLEAR)
    print("Records cleared (%d rows in %d tables), documents and photos dropped: %d" % (cleared, sum(1 for t in CLEAR if r["removed"].get(t)), r["droppedFiles"]))
    for t in sorted(CLEAR):
        if r["removed"].get(t):
            print("  %-26s %d" % (t, r["removed"][t]))
    print("Inventory items removed: %d; kept as blank items: %s" % (len(r["itemsRemoved"]), ", ".join(ascii_safe(n) for n in r["systemItems"])))
    for n in r["itemsRemoved"][:40]:
        print("  " + ascii_safe(n))
    if len(r["itemsRemoved"]) > 40:
        print("  ... and %d more (the report lists them all)" % (len(r["itemsRemoved"]) - 40))
    if r["systemItemsMissing"]:
        print("WARNING: no inventory item called %s in the source: add it in Inventory Items after the restore." % ", ".join(ascii_safe(n) for n in r["systemItemsMissing"]))
    print("Configuration kept (checked row for row; the rows of the removed items' suppliers, packs and links are gone):")
    for t, n in sorted(r["kept"].items()):
        print("  %-26s %d" % (t, n))
    print("Files kept: %s" % ", ".join("%s %d" % kv for kv in sorted(r["files"].items())))
    print("Proof: every other configuration table is identical to the source, row for row; one 'clean_start' entry was added to the change history.")
    if not dry:
        print("")
        print("Written next to the zip: %s (what changed and the proof) and %s (a draft cut-over note for the Quality Manager)." % (os.path.basename(r["report"]), os.path.basename(r["note"])))
        print("This file still holds every user's password hash: keep it private, and delete it when you are done.")
        print("Next: restore it on STAGING first (Admin > Restore), then on live with docs/restore-runbook.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
