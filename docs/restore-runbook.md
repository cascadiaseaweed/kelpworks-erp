# Restoring the live data from a backup

Use this only when the live data is damaged or wrong and going back to a backup is the right answer. **Anything entered after the backup was made is lost** (the
records archive in SharePoint keeps run documents, but not rows).

The live site cannot be restored from inside the app (the Restore button exists only on staging). Instead the server restores **at start-up**, while it is not
serving anyone, when you ask it to with two environment variables. A copy of the data being replaced is kept first.

## Before you start (5 minutes)

1. **Decide which backup.** Nightly backups are in `/var/data/backups/nightly/kelp_erp_<date>.zip` (newest 3) and, longer-term, in the SharePoint library
   `KelpWorks-Records` > `01_System-Backups` (daily / weekly / monthly folders). Pick the newest one made **before** the damage.
2. **Take a full backup of the current state anyway** (Admin > Download full backup). If the restore turns out to be the wrong choice, restoring that file the
   same way brings you back.
3. **Rehearse on staging if there is time.** Staging has a Restore box (Admin) that takes the same zip: confirm the backup opens and looks right
   (`docs/staging.md`).
4. Tell the people using the system; the service restarts and is unavailable for a minute or two.

## Steps

1. **Put the zip on the disk.** In the Render dashboard open the service > **Shell**.
   - From a nightly backup already on the disk:
     ```bash
     cp /var/data/backups/nightly/kelp_erp_2026-10-11.zip /var/data/restore_me.zip
     ```
   - From the SharePoint copy: create a temporary download link for the file (OneDrive/SharePoint > Share > a link that works for people with the link,
     expiring tomorrow), then in the shell `curl -L -o /var/data/restore_me.zip "<the link>"`. Delete the link afterwards.
2. **Check the file** is a real zip of a sensible size: `ls -l /var/data/restore_me.zip` and `python -c "import zipfile;print(zipfile.ZipFile('/var/data/restore_me.zip').namelist()[:5])"`
   (it should list `kelp_erp.db` and `manifest.json`).
3. **Ask for the restore.** Dashboard > the service > **Environment**: add
   - `KELP_ERP_RESTORE_FROM` = `/var/data/restore_me.zip`
   - `KELP_ERP_RESTORE_CONFIRM` = `replace-live-data`

   Save; Render redeploys.
4. **Read the log** (Dashboard > Logs). You should see a line starting `RESTORED from` with the backup's date, the number of users and the files restored.
   - `RESTORE FAILED, nothing was replaced: ...` means the file was rejected before anything changed (damaged, wrong kind, not enough disk for the safety copy).
     Fix the cause or pick another file. The server does not start while the variables are set, so the old data stays as it is.
   - `KELP_ERP_RESTORE_CONFIRM=replace-live-data is not set` means only one of the two variables was added.
5. **Remove both variables** (Environment > delete > save). This redeploys once more. (The zip was renamed to `restore_me.zip.restored-<date>`, so a start with the
   variables still set does not restore again, but leave nothing armed.)
6. **Check the result:** sign in; Product Release > **Verify audit trail** (it must say the chain is intact); Admin > **Data integrity check**; open a recent run and a
   recent shipment; Admin > Records archive & backups should show the nightly backup **on**.

## What the restore does and does not do

- It replaces the database and the folders `uploads/`, `lab_templates/`, `sop_documents/` with the backup's, then migrates the data to the current version of the code.
- Passwords are **not** reset (the staging restore resets them; the live one must not).
- The data it replaced is kept as `/var/data/backups/pre-restore-<date>.db.gz` (database only; the last three are kept). Documents added after the backup are not in it.
  To undo a restore completely, restore the full backup you took in "Before you start".
- A backup made by an **older** version of the code is migrated forward like any other upgrade. A backup from a **newer** version than the running code is not
  supported: deploy the matching version first.
- Disk snapshots in Render are a separate, coarser safety net (they restore the whole disk): check in the dashboard that they are enabled.

## If the disk itself is missing

If the service ever starts without its disk, it would create a **new empty database**. Set `KELP_ERP_REQUIRE_MOUNT=/var/data` on the service (check first in the
Render shell that `python -c "import os;print(os.path.ismount('/var/data'))"` prints `True`) and the server refuses to start instead, with a message in the log saying
the disk is not mounted.

## Wipe everything and start fresh

To clear ALL records from live (totes, runs, finished goods, shipments, customers, samples, lab results, documents, the audit history) and keep only the
configuration, restore a **clean backup** instead of a normal one. The same steps as above apply; only the file differs, so this doubles as a rehearsal.

**Kept:** users and their roles and passwords, species, sites, products, locations, settings (including the application rate), Certificate of Analysis specifications,
labs + analyses + requisition templates + contact details, quality-chart limits, the SOP and safety-data-sheet register with its links and files, inventory item
definitions (stock set to 0), and the history of changes to those. **Cleared:** every record, every uploaded document and photo, and the release / audit history.

1. **Archive the old data.** Download a full backup (Admin > Download full backup) and keep it, named for what it is (for example `pre-wipe-2026-11-01`). The SharePoint
   archive and its backups stay as they are; do not restore an older nightly backup by mistake afterwards.
2. **Make the clean file** (on any PC with Python; it only reads and writes files):
   ```bash
   py tools/make_clean_backup.py pre-wipe-2026-11-01.zip kelpworks-CLEAN.zip
   ```
   It prints what it cleared and what it kept. It refuses a backup with a table it does not recognise (so no record is kept by mistake) and never overwrites a file.
3. **Rehearse on staging:** Admin > Restore with `kelpworks-CLEAN.zip` (staging resets every password to the staging one). Check it opens empty, the people and settings are
   there, and a test harvest, run and shipment work.
4. **Restore on live** with the steps above, using `kelpworks-CLEAN.zip`. Checks afterwards: Product Release lists no runs, Verify audit trail says the chain is intact, the
   Calculations page still shows your settings, and the first harvest check-in works.
5. **Tidy up:** delete `kelpworks-CLEAN.zip` and the temporary SharePoint link (both contain every user's password hash), and remove the two restore variables from Render.

Lot and shipment numbers start again from 001 (for example `PR-<date>-001`). The old records remain in `pre-wipe-...zip` and in the SharePoint archive, so reusing a
number does not overwrite anything there, but keep that in mind when looking things up by number.
