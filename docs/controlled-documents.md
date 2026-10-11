# Controlled documents: SOPs and safety data sheets

SharePoint is the master. Quality keeps the controlled files in two folders of the `KelpWorks-Records` library:

```
KelpWorks-Records/
├─ 03_SOPs/
│  ├─ Current/        what is in force
│  └─ Superseded/     older revisions (KelpWorks does not read this folder)
└─ 04_Safety-Data-Sheets/
   ├─ Current/
   └─ Superseded/
```

Every night the same sync that copies the production-run archive (see `records-archive.md`) also **reports what is in the two `Current` folders** to KelpWorks and uploads the files
KelpWorks does not have yet. KelpWorks then shows the differences on the **Documents** tab. **Nothing changes in KelpWorks by itself:** an administrator accepts or ignores each
difference, with one click. Everyone can read the lists and open the files; only administrators review.

## File names (what Quality does)

| Document | Name | Example |
|---|---|---|
| SOP | `SOP-<number>_<Title-In-Hyphens>_rev<n>.pdf` | `SOP-012_Homogenization_rev3.pdf` |
| Safety data sheet | `SDS_<Chemical-Name>_<Supplier>_<issue date>.pdf` | `SDS_Citric-Acid_Univar_2025-03-14.pdf` |

- **SOPs** are identified by their **number**. The revision (`rev3`, `r3`, `v3`, `Rev-B`) is optional. `SOP002_Solid_Liquid_Ratio.pdf` also reads as SOP-002. Two files with the
  same number mean two versions of one document.
- **Safety data sheets** are identified by **chemical + supplier**; the **issue date** is the version. Fields are separated by underscores; use hyphens inside a field. The date is
  year-month-day (`2025-03-14` or `20250314`).
- A name KelpWorks cannot read shows up as **Name not readable** with a reason. Rename the file in SharePoint and the next night picks it up (or **Ignore** it).
- Do not leave both an old and a new revision in `Current`. Put the new file in `Current` and move the old one to `Superseded`. If both stay, KelpWorks flags
  "another file with the same number is also in the folder".
- Word's temporary files (`~$...`), hidden files and sub-folders are ignored. Keep files directly in `Current`. A file may be up to 25 MB.

## What an administrator sees and does

**Documents** tab > **Changes in the SharePoint folders** (the tab shows a count while something waits). Each row is one change:

| Change | What it means | Review does |
|---|---|---|
| **New document** | A number / chemical KelpWorks does not have | SOP: choose a name, or say "this is an SOP KelpWorks already has" (links the controlled file to it and keeps its reference key, so QC checks keep linking). SDS: check chemical, supplier and issue date, and tick the inventory items it covers |
| **New revision** | The same document with different content | Makes the new file current. The file it replaces stays in the history (**History** button on the row) |
| **File removed** | A current document's file is no longer in `Current` | Marks the document **Withdrawn**: its last file and history are kept; nothing is deleted. A withdrawn SOP shows "withdrawn" where the production log links to it |
| **Name not readable** | The file name does not follow the convention | Cannot be accepted: rename it in SharePoint, or **Ignore** |

**Ignore** means "do not ask about this exact file again" (it comes back if the file changes). Use **Open the file** in the review window to look at a file before accepting it.
Everything an administrator accepts or ignores is recorded with their name, and the **History** of each document lists its earlier files (downloadable) and its change log.

The page also shows when each folder was last reported. If that is more than 3 days ago (setting `library_scan_stale_days`) a red note says the nightly sync on the office PC may have stopped.

**Safety data sheets** show a **Review due** date = issue date + 3 years (settings `sds_review_years` and `sds_review_warning_days`, which an administrator can change on the Calculations page):
OK / Review soon / Overdue. **Used for...** links a sheet to the inventory items it covers; those items then show an **SDS** link on the Inventory Items page.

## Safeguards

- An **empty** folder (for example the library is not synced on that PC) is never read as "everything was removed": KelpWorks warns and changes nothing.
- A file KelpWorks receives is stored apart until you accept it; when it leaves the folder before you decide, its copy is deleted. Files that cannot run code only (the same refusal list as any upload).
- Accepting a revision or a removal never deletes a file: replaced and withdrawn files stay on the server disk (and in the nightly backup) and in the document's history.
- The sync key can only report folders and upload files for review; it cannot change the lists. Only an administrator's sign-in can accept or ignore.

## Setup

Nothing new if the records sync is already running: the same `tools/kelpworks_archive_sync.py` and the same scheduled task now also report these two folders (use
`--skip-documents` to turn that off). After this version is deployed:

1. Copy the updated `kelpworks_archive_sync.py` over the one in `C:\KelpWorks\tools` (keep your edited `run_archive_sync.bat`).
2. Put the controlled files in `03_SOPs/Current` and `04_Safety-Data-Sheets/Current` of the library, named as above.
3. Run `run_archive_sync.bat` once (or wait for the night). Open **Documents**: the files appear as changes to review.
4. **First time only:** the one SOP KelpWorks already has ("Determining % Wet Solids SOP") has no number yet. Review its file, choose *This is the existing document*, and accept: the
   number is attached and the file becomes its first revision (a file uploaded earlier by hand is kept in its history).

## If something looks wrong

| You see | Likely cause |
|---|---|
| Nothing appears after the sync | The sync log says what it reported (`SOPs: 3 file(s) in the folder, ...`). Is the file in `Current`, directly (not a sub-folder)? Is the library synced on that PC? |
| "has not reached KelpWorks yet" when accepting | The file is uploaded at the next sync; run the `.bat` again and retry |
| A document is flagged "File removed" but QA only renamed it | A new name with new content is a *new revision* (of the same number) and shows next to it; a rename with identical content is recorded quietly |
| "Name not readable" | See the reason shown; the naming rules are above |
| The red "folders have not been reported" note | The scheduled task stopped; open `sync.log` on the PC |
