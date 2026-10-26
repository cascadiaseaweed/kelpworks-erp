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

1. **Put the zip on the disk.** Which way depends on where the file is. (The live service runs on a minimal image: the Render Shell has Python but **no `curl` or `wget`**,
   and it cannot upload files.)
   - **From a nightly backup already on the disk** (in the Render dashboard open the service > **Shell**):
     ```bash
     cp /var/data/backups/nightly/kelp_erp_2026-10-11.zip /var/data/restore_me.zip
     ```
   - **From your PC (a downloaded backup, or a clean backup): copy it over SSH.** This is the normal way and needs no sharing link.
     1. One-time: make an SSH key in PowerShell with `ssh-keygen -t ed25519` (press Enter at each question to accept the defaults; if it says a key already exists, answer `n` and use
        that one). Show the public key with `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`, copy all of it, and add it in Render > Account Settings > **SSH Public Keys**.
     2. Get the service's address: Render > the **live** service > **Connect** > **SSH**. Copy the part after `ssh `, for example `srv-abc123xyz@ssh.oregon.render.com`
        (an address to type into a command, not a web page; no `https://`). If the SSH tab is missing, step 1 has not been done.
     3. Copy the file, from PowerShell, with the full path of the file in quotes:
        ```powershell
        scp "C:\Users\nwran\OneDrive - Cascadia Seaweed Corp\Documents\KelpERP\tools\kelpworks-CLEAN.zip" srv-abc123xyz@ssh.oregon.render.com:/var/data/restore_me.zip
        ```
        Answer `yes` if it asks about the host's authenticity. It prints a progress line that ends at 100%. `No such file or directory` for the local file means PowerShell is not in the
        folder you think (`.\` means "this folder"): use the full path.
   - **A sharing link does not work** if your organisation blocks "anyone with the link" (the server would download Microsoft's sign-in page, about 58 KB, instead of the zip). Use SSH.
2. **Check the file** arrived whole. The size must equal the file on your PC (`Get-Item <file>` shows `Length`) and it must open as a zip. **Run these on the server, not in PowerShell**
   (`/var/data` does not exist on Windows, so PowerShell answers `ObjectNotFound`): in the Render **Shell** tab, or from PowerShell through SSH:
   ```bash
   ls -l /var/data/restore_me.zip
   python -c "import zipfile;print(zipfile.ZipFile('/var/data/restore_me.zip').namelist()[:5])"
   ```
   ```powershell
   ssh srv-abc123xyz@ssh.oregon.render.com "ls -l /var/data/restore_me.zip"
   ```
   The list should show `kelp_erp.db` and `manifest.json`. `File is not a zip file` means the copy is not your backup: check what it is with `head -c 300 /var/data/restore_me.zip`
   (`<!DOCTYPE html>` = a web page, see above), delete it with `rm /var/data/restore_me.zip` and copy again.
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
- Photos that were **moved to SharePoint** (`docs/records-archive.md`, "Photos move to SharePoint") are not in the backup: the records keep their link, and the library holds the file. A restore brings back
  the database and the photos that were still on the disk when the backup was taken; a photo the database says was moved but whose file is back from an older backup is simply served from the disk.

## If the disk itself is missing

If the service ever starts without its disk, it would create a **new empty database**. Set `KELP_ERP_REQUIRE_MOUNT=/var/data` on the service (check first in the
Render shell that `python -c "import os;print(os.path.ismount('/var/data'))"` prints `True`) and the server refuses to start instead, with a message in the log saying
the disk is not mounted.

## Wipe everything and start fresh

The order of work, who does what and the records to keep are in `docs/clean-cut-over.md` (the KW-60 reset of 2026-10-23); the details are below.

To clear ALL records from live (totes, runs, finished goods, shipments, customers, samples, lab results, documents, the audit history) and keep only the
configuration, restore a **clean backup** instead of a normal one. The same steps as above apply; only the file differs, so this doubles as a rehearsal.

**Kept:** users and their roles and passwords, species, sites, products, locations, settings (including the application rate), Certificate of Analysis specifications,
labs + analyses + requisition templates + contact details, quality-chart limits, the SOP and safety-data-sheet register with its links and files, and the history of changes
to those (the tool proves, row for row, that every one of these tables is identical afterwards). **Cleared:** every record, every uploaded document and photo, and the release /
audit history. **Inventory items (KW-60):** every reagent, packaging and finished-good-label item is removed, with its suppliers, packs, safety-data-sheet links and history, so it
can be added again with real quantities and documents; only the four items the program finds by their stable key (shown here by their standard names; a renamed one is still kept) (Citric Acid, Potassium Sorbate, New 1,000 L IBC Tote, Used 1,000 L
IBC Tote) stay, as blank items (stock 0, no cost, no suppliers). The server recreates its other standard items (sodium benzoate, sample containers, CIP agents, finished-good labels) at
every start. **One entry** (`system / clean_start`) is added to the change history of the clean database: who made the file, from which backup, how much was cleared.

1. **Archive the old data.** Download a full backup (Admin > Download full backup) and keep it, named for what it is (for example `pre-wipe-2026-11-01`). The SharePoint
   archive and its backups stay as they are; do not restore an older nightly backup by mistake afterwards.
2. **Make the clean file** (on any PC with Python; it only reads and writes files). First look at what it would do (writes nothing):
   ```bash
   py tools/make_clean_backup.py pre-wipe-2026-11-01.zip --dry-run
   py tools/make_clean_backup.py pre-wipe-2026-11-01.zip kelpworks-CLEAN.zip --by "Your Name"
   ```
   It prints what it cleared (rows per table, the inventory items removed) and what it kept, and checks its own result before writing it. It refuses a backup with a table it does not
   recognise (so no record is kept by mistake) and never overwrites a file. Next to the zip it writes `kelpworks-CLEAN.report.txt` (what changed, the proof that nothing else did, the supplier
   names that were on the removed items) and `kelpworks-CLEAN.cut-over-note.md` (a draft cut-over note: give it to the Quality Manager, who completes and signs it as the QM-001 record).
   The full backup of step 1, named `PRE-GO-LIVE ARCHIVE <date>`, is the controlled record of the old data: restore-test it on staging and store it OUTSIDE `01_System-Backups` (for example in
   `00_Pre-Go-Live`) so no retention cleanup ever touches it.
3. **Rehearse on staging:** Admin > Restore with `kelpworks-CLEAN.zip` (staging resets every password to the staging one). Check it opens empty, the people and settings are
   there, and a test harvest, run and shipment work.
4. **Before the restore, clear the old records archive** so the restarted numbers cannot collide with old folders (the sync only adds, and skips a file whose path and version it already has).
   In the SharePoint library `KelpWorks-Records` delete the contents of `02_Production-Runs`, `06_Receiving` and `01_System-Backups`, and delete `_sync-state.json` in the library root (it is only the sync
   script's memory of what it copied: the files and backup days). **Do not** delete `03_SOPs` or `04_Safety-Data-Sheets` (the controlled masters KelpWorks mirrors), the pre-go-live backup, or anything
   outside this library: the change log lives in `QAQC > KelpWorks ERP > Change_Control` and `C:\KelpWorks\ChangeControl` and is not involved. The first sync after the reset rebuilds the indexes.
5. **Restore on live** with the steps above, using `kelpworks-CLEAN.zip`. Checks afterwards: Product Release lists no runs, Verify audit trail says the chain is intact, the
   Calculations page still shows your settings, the change history shows the `clean_start` entry, and the first harvest check-in works. Then add the inventory items again (KW-64).
6. **Tidy up:** delete `kelpworks-CLEAN.zip` and the temporary SharePoint link (both contain every user's password hash), and remove the two restore variables from Render.

Lot and shipment numbers start again from 001 (for example `PR-<date>-001`). The old records remain in `pre-wipe-...zip` and in the SharePoint archive, so reusing a
number does not overwrite anything there, but keep that in mind when looking things up by number.
