# Records archive and nightly backups

KelpWorks keeps two things safe outside the live server:

1. **Nightly backups** of the database and every document (for restoring the system).
2. **A records library**: every finalized production run's documents, filed in folders with predictable names (for people to find things).

Both end up in one SharePoint folder, `KelpWorks-Records`, in the Operations site. The server cannot write to SharePoint itself, so a small script on an office PC copies
them across every night. The script only ever **adds**: nothing in the library is deleted when something changes or is removed in KelpWorks (the one exception is the
backup retention below, which only removes old backup files).

```
KelpWorks server (Render)                      Office PC                         SharePoint
  02:00-3:00 am Pacific: nightly backup  --->  Task Scheduler runs               KelpWorks-Records/
  GET /api/archive/manifest              --->  tools/kelpworks_archive_sync.py -> (synced folder uploads itself)
  GET /api/archive/file, /backup
```

## What the library looks like

```
KelpWorks-Records/
├─ README_Naming-Convention.txt
├─ 01_System-Backups/   daily/ (14)  weekly/ (12)  monthly/ (24)  pre-migrate/ (6)      administrators only
├─ 02_Production-Runs/
│  ├─ _Index/runs_index.csv                         one row per run
│  └─ 2026/2026-10/PR-20261006-053_KELPIVEX_2026-10-06/
│     ├─ 01_Report-and-CoA/   PR-20261006-053_Production-Summary_rev2.pdf   PR-20261006-053_CoA_RELEASED.pdf
│     ├─ 02_Lab/              ..._Requisition_SGS-Canada-Inc_REQ-20261008-007.docx   ..._Lab-Report_SGS-Canada-Inc_VR26-05008-007.pdf
│     ├─ 03_Photos/           ..._Photo_JAM-SL-20260512-010_surface.jpg
│     └─ 04_Other/            ..._Document_<original-name>.<ext>
├─ 03_SOPs/  (Current / Superseded)            kept by Quality; not written by KelpWorks yet
├─ 04_Safety-Data-Sheets/  (Current / Superseded)
├─ 05_Fulfillment/                              later
└─ 06_Receiving/
   ├─ _Index/receiving_index.csv                   one row per delivery that has a document (invoice, PO, order and delivery numbers, carrier)
   └─ 2026/2026-08/2026-08-21_DuBois-Chemicals-Canada-Inc_IN-60133189/
      └─ IN-60133189_Invoice_<original-name>.pdf   IN-60133189_Delivery-Doc_...   IN-60133189_CoA_...   IN-60133189_Freight-Invoice_...
```

- **Which runs:** every *finalized* run (a draft is still changing). A run's folder is named from its lot, product and run date **the first time it is archived and
  never changes afterwards**, even if the run's date or product is amended.
- **Receiving documents:** every delivery that has at least one attached document (Inventory Items > Lots > Documents) gets a folder `06_Receiving/<year>/<year-month>/<received date>_<supplier>_<invoice number>/`
  (`RCPT-<n>` in place of the invoice number when it was not known when the first document was archived). The folder and the file-name key are fixed the first time a document of the delivery is
  archived, so correcting the invoice number or supplier later does not move anything; the corrected details appear in `receiving_index.csv`. A document is archived as uploaded and never changes,
  and a new document is simply a new file. The sync script needs no change to carry these files.
- **File names:** `<processing lot>_<document type>_<detail>.<extension>`: letters, numbers, hyphens and underscores only, the lot first so a search for it finds everything.
  The full convention (also covering SOPs, SDS and shipments) is in `README_Naming-Convention.txt`, which the sync writes into the library.
- **Revisions:** the production summary is named for the production-log revision (`_rev1`, and `_rev2` after an amendment); an earlier revision's file stays. The Certificate of
  Analysis is `_PRELIMINARY` until the run is released, then `_RELEASED` (the preliminary file moves to `_superseded`). If a file changes under the same name (a lab result is added,
  a photo is replaced) the old copy is moved to an `_superseded` folder beside it with the date in its name.
- **Lab requisitions and sample lists** are archived as the Word / Excel files that were sent to the lab (KelpWorks has no Word-to-PDF converter and adds no libraries).
  Photos are archived as the original images.
- **Cannot be recreated:** the first sync can only file what exists *now*, so earlier revisions of an amended run (before this feature) are not in the library; from now on each
  new revision is kept.

## One-time setup

1. **Pick a long secret key** (at least 24 characters) and put it on the live service: Render > the service > Environment > `KELP_ERP_ARCHIVE_KEY`. Create it with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` and do not paste it into chat or e-mail. It can only read the archive and backups; it is not a login.
   Optional settings: `KELP_ERP_BACKUP_HOUR_UTC` (default 10 = about 3 am Pacific), `KELP_ERP_BACKUP_KEEP` (nightly files kept on the server disk, default 2; the disk is small).
2. **Sync the SharePoint folder to the PC that will run the script** (open `KelpWorks-Records` in SharePoint > **Sync**). Note its local path, for example
   `C:\Users\<you>\Cascadia Seaweed Corp\Operations - KelpWorks-Records`. Use a PC that is on at night (or run the script in the morning).
3. **Copy the `tools` folder** of this repository to the PC (for example to `C:\KelpWorks\tools`), save the key in `C:\KelpWorks\tools\archive-key.txt`, and edit the three lines at the
   top of `run_archive_sync.bat` (the service address, the synced folder, the key file). Python 3.8 or newer must be installed; nothing else is.
4. **Try it without changing anything:** `python kelpworks_archive_sync.py --dry-run --url ... --root ... --key-file ...` lists what it would copy. Then run `run_archive_sync.bat` once.
   The first run builds the folder skeleton, writes the README and copies everything; SharePoint uploads it in the background.
5. **Schedule it** (after the server's backup, so about 4:30 am Pacific):
   `schtasks /Create /TN "KelpWorks records sync" /TR "C:\KelpWorks\tools\run_archive_sync.bat" /SC DAILY /ST 04:30`
   The log is `sync.log` next to the script; the last line is `SYNC OK` when everything copied.
6. **Limit access in SharePoint:** `01_System-Backups` to administrators (the backups hold password hashes and all business data); the other folders as you see fit.

Check it any time on **Admin > Records archive & backups**: whether the nightly backup is on, the last backup, the backups on the server disk, and whether the key is set.
A backup that could not be made (for example the disk is nearly full) shows there and in the Render log.

## The server disk is small

Each nightly backup is about the size of the database plus all uploaded documents, and the server keeps the newest 2 on its 1 GB disk next to the live copies. The backup is skipped
(and Admin shows why) when there is not room for it with 100 MB to spare. When documents pass roughly 200 MB, enlarge the disk in Render (or lower `KELP_ERP_BACKUP_KEEP` to 1).

## Photos move to SharePoint (KW-04)

Photos are the files that grow the server disk. Every finalized run's photos (feedstock photos, tote photos, note pictures) are already copied into the library by the script. Once a
photo's copy is **confirmed**, KelpWorks can take its own copy off the disk after `image_archive_days` (default 30) days and keep a link on the record. Nothing is removed unless all of these hold:

1. The script saved the photo in the library and sent KelpWorks its SHA-256 (`POST /api/archive/confirm`); KelpWorks keeps the confirmation only if that checksum equals the one of
   **its own file** and the path is the one it would give the file.
2. The run was finalized more than `image_archive_days` days ago and is not open for amendment.
3. Just before deleting, the file on the server still has the confirmed checksum.
4. An administrator has switched on **Photos: remove archived photos from the server** (`image_purge_enabled`, Calculations page). **It is off by default.** While it is off, nothing is deleted;
   Admin > Records archive & backups > Photo archive shows how many photos are confirmed and what *Check what would be removed* lists.

Every removal is written to the append-only `image_purge_log` (photo, size, checksum, library path, who, when). Documents, lab reports and certificates are never removed, only pictures.
The record stays: where the photo was, KelpWorks shows "Photo moved to SharePoint - open", a link made from the library's web address. Set that address once in Render as
`KELP_ERP_SHAREPOINT_BASE_URL` (for example `https://<tenant>.sharepoint.com/sites/<site>/KelpWorks-Records`, the address of the library folder that holds `00_README...`).

**Switching it on, safely:** let the nightly sync run, open Admin > Photo archive and check that *Copy confirmed in SharePoint* is counting up; open a few of the confirmed photos in the
library; click *Check what would be removed*; then set `image_purge_enabled` to 1. The check runs once a day after that (or *Remove confirmed old photos now*).

**After it is on, the library copy is the only copy** of a moved photo. If the script ever finds a moved photo missing from the library it prints `FAILED ... Restore it from the SharePoint
recycle bin / version history`. Nightly backups no longer contain moved photos; keep the library's own version history and recycle bin enabled.

## Retention (what the script removes)

Daily 14, weekly 12 (the last backup of each week), monthly 24 (the last of each month), snapshots taken before a software update 6. Change them with `--keep-daily`,
`--keep-weekly`, `--keep-monthly`, `--keep-premigrate`. The script removes **only** files named like its own backup files, and never the last one in a folder. Each backup is the
size of the database plus all uploaded documents, so check the SharePoint quota as the documents grow (about 50 backups are kept in total).

## Restoring from a backup

A nightly `.zip` is exactly the "full backup" that **staging** can restore: rehearse on staging first (Admin > Restore from a full backup...). To restore **live**, follow the
runbook in `release-guide.md` ("A migration went wrong at startup"), but take the database from the zip (`kelp_erp.db` inside it) instead of the pre-migrate snapshot, and
copy the `uploads/`, `lab_templates/` and `sop_documents/` folders from the zip as well if documents were lost. Anything entered after the backup is lost, so restore only when
the data itself is damaged.

## Not in this first version

- Shipment documents. (SOPs and safety data sheets flowing from SharePoint **into** KelpWorks are in `controlled-documents.md`.)
- Pushing straight to SharePoint from the server (Microsoft Graph) so no PC is needed. The manifest and file names are the same; only the last step would change.
