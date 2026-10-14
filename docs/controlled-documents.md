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

## Where SOPs are managed

- **Documents tab:** the one place everyone reads SOPs and safety data sheets, sees revisions and history, and sees **where each SOP is linked** ("Linked at"). Administrators also review changes from SharePoint here. Nothing is uploaded by hand.
- **Admin > SOPs:** the same list, with what only an administrator can do: **Key…** (an optional reference key, changeable or clearable) and **Link to…** (where the SOP is linked, see below).
- **The SharePoint file name is the single source of truth.** An SOP's **name** is the title in its file name (`SOP-012_Homogenization_rev3.pdf` shows as "Homogenization"; a file with no title shows its number;
  if another SOP already has that name, the number is added: "Homogenization (SOP-311)"). There is no Rename in KelpWorks: rename the file in SharePoint and the next nightly sync updates the name (a new
  revision with a new title, or the same file renamed). The same holds for a safety data sheet's chemical, supplier and issue date, which come from its file name.
- **SOPs are never deleted in KelpWorks, and their files are never replaced by hand.** Both happen in SharePoint: take the file out of `Current` and the next sync offers the document as *withdrawn* (its files and
  history are kept); put a new revision in `Current` and it is offered as a new revision. The server refuses a delete or a hand-uploaded file with "controlled in SharePoint".

## Linking an SOP to places in the app

An SOP can be shown as a small **SOP** line, with a link to its current file, at the places below. Choose them in three ways, all with the same result:

- **Admin > SOPs > Link to...** on one SOP: a tidy ticklist of the places with your own **filters and grouping**:
  - **Search** by any word (for example `qc`, `homogenization`, `cart`).
  - **Show** one or more areas (Production log, CIP Log, Samples, Shipping) with the buttons; "All" shows everything.
  - **Group by** *Area and section* (Production log > Homogenization, CIP Log, ...) or *Type* (Sections, Pages, Views, Process Checks, QC Checks, Sample Points, Other boxes, Windows), for example to tick every QC Check at once.
  - A box beside each group ticks the whole group (only the places currently shown); **Only ticked** shows what is already chosen; a counter shows how many places are ticked.
  The same filters and grouping are on the **Where SOPs are linked** table. Your choices are remembered in your browser.
- **Admin > Where SOPs are linked** (a minimized section: click it to open): the table of places (with the same filters and grouping): tick several places (or a whole group) and **Add an SOP to these places...** (or **Remove an SOP from these places...**) in one go. **Choose SOPs...** on a row sets all the SOPs for that place.
- Several SOPs can share a place and one SOP can be at many places.

**Taking an SOP out of use (administrators, Admin > SOPs).** Nothing in KelpWorks is ever deleted, but an SOP can be retired:
- **Retire...** (any SOP; a reason is required): for an SOP that is no longer relevant, for example its file was removed from the Current folder and nothing replaces it. A retired SOP leaves the Documents list, the pickers and every
  production-log link; its file and history are kept under **Retired SOPs** at the bottom of the Admin SOP table, where **Reinstate** brings it back (its places must be linked again). If its file reappears in the SharePoint folder it is
  offered for review as a revision, and accepting reinstates it.
- **Replace...** (only an SOP **without a number**, one that was loaded before the SharePoint sync): use it when the SOP you want to keep was added as a *new* SOP by mistake. Its places and reference key move to the SOP you choose and this
  entry is retired, with a line in both histories.

**Is this an SOP KelpWorks already has?** When a new SOP file arrives and KelpWorks still holds SOPs **without a number**, the review asks you to choose: *Yes, this is the existing SOP* (pick which; the closest match by title words is suggested)
or *No, it is a different, new SOP* (you are asked to confirm). There is no default, so a new SOP cannot be added by accident while an old one is waiting. When no SOP without a number is left, the question disappears.

The places (grouped in the picker by area):

- **Production log**, one group per stage: Initiation, Feedstock, Homogenization, Extraction, Separation, Pasteurization, Dilution & Preservation, Packaging. Each group has *Whole section* and the boxes inside it:
  for example Homogenization > Process Check (the solids-loading box that carried the one fixed SOP link before), each QC Check, each Sample Point, the dilution boxes and each additional dilution pass,
  the packaging table, and each tote's feedstock characterization card. (Windows of the production log, such as the Process log or Lab results windows, are not offered.)
- **CIP Log:** the whole page, and the Log / edit CIP window.
- **Samples:** the whole page (all views), each view (Analysis catalogue, Retention inventory, Cart, Requisitions), the sample details window, and the requisition windows.
- **Shipping:** the whole page, and the New / edit shipment window.

The link always opens the SOP's **current** file, so a new revision is picked up automatically; a withdrawn SOP shows "withdrawn". Every change is written to the SOP's history (Linked at / Unlinked from).
The earlier link (Homogenization > Process Check to the wet-solids SOP) was carried over. To make another place available, a developer adds a `data-sop-spot` key in the app (or a window title prefix in
`SOP_WINDOW_SPOTS`) and the same key to `SOP_SPOTS` in the server; a test keeps the two lists equal.

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
| **New document** (an SOP: **New SOP**) | A number / chemical KelpWorks does not have. For an SOP the number decides: KelpWorks has no SOP-012, so SOP-012 is a new SOP | SOP: shows the name it will get (from the file name); or say "this is an SOP KelpWorks already has" (links the controlled file to it, keeping its links and reference key; it takes its name from the file). SDS: shows chemical, supplier and issue date (from the file name); tick the inventory items it covers |
| **New revision** (an SOP: **Revision of SOP-012**) | The same document with different content. For an SOP the number decides: KelpWorks already has SOP-012, so the file is a revision of it, never a second SOP. The Name column shows the current name and, if the new file's title differs, "old -> new"; the revision shows "rev 2 -> rev 3" | Makes the new file current (and the name follows the new file name). The file it replaces stays in the history (**History** button on the row) |
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
