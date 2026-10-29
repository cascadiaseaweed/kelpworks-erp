# Tracking changes against the Quality Manual (QM-001)

Every change to KelpWorks is checked against the Quality Manual (QM-001) before it can be Released, every change the manual needs is recorded once, and the Quality Manager decides it. The manual itself stays where it is
(SharePoint, controlled); nothing here edits it. Introduced by KW-71.

## The pieces

| Piece | Where | Who writes it |
|---|---|---|
| **QM-001 Impact** | column Y of the Change Log sheet, one value per item: `<class> - <one line>` | Claude Code (`changelog_write.py --id KW-nn "QM-001 Impact=..."`) |
| **QM-001 Ref** | column V of the Change Log, the register Ref(s) of the manual change (for example `C21`) | Claude Code |
| **QM-001 Changes** | a sheet in the workbook: one row per manual change | Claude Code / Claude (chat) draft it; the Quality Manager's columns are human-only |
| **Status report** | `python tools/changelog_write.py --qm-report`, also published as `QM-001_Status_Report.txt` beside the SharePoint copy after every write | automatic |
| **Draft revision** | `python tools/qm_draft_revision.py` | Claude Code, on demand |

**Impact classes:** `None` (always allowed with a one-line reason, for example `None - bug fix, no requirement involved`), `Requirement changed`, `Requirement added`, `Requirement relaxed`, `Record / control changed`. A value that starts with `PROPOSED`
is an unconfirmed backfill proposal and does not count until the word is removed.

**Register columns (QM-001 Changes):** Ref, Date, KW items, Manual section / clause, Change (Replace / Add / Delete), Before, After, Insert after, Reason, Drafted by | **the Quality Manager's:** QM Decision (Approved / Rejected / Needs change), Decided By, Decision Date,
Manual Revision, Entered in Manual (date) | Drafted In (written by the draft-revision tool). The Ref continues the manual's own C-numbers. Rows marked `Drafted by: backfill` are changes the manual already carried before the register existed.

## The path of one change

1. **Item approved** (Approved to Start = Y). The plan (`/kw-item`) states the QM-001 impact.
2. **Built, tested, pull request merged, live.**
3. **Draft the manual change** (anything but `None`): write an entry file and run `python tools/changelog_write.py --qm-entry entry.json`
   (`{"kw_items": ["KW-99"], "section": "2.2.2 User roles", "change": "Replace", "before": "...", "after": "...", "reason": "..."}`; an Add also needs `insert_after`, the start of the paragraph it follows). Claude (chat) can leave the same as a `qm_entry` inbox note.
   The tool prints the new Ref.
4. **Release:** `python tools/changelog_write.py --id KW-99 Status=Released "PR #=nn" "Released Date=YYYY-MM-DD" "QM-001 Impact=Requirement changed - who can lift a hold" "QM-001 Ref=C21"`.
   The tool **refuses** Released without a confirmed impact, and for any class other than None without a Ref that is on the register. The Quality Manager's decision is *not* awaited: it is chased by the report.
5. **Decide:** the Quality Manager opens the master in Excel and fills QM Decision, Decided By and Decision Date on the entry (an entry that needs rewording is set to Needs change and revised with `--qm-entry` using `ref`, or a new entry is drafted).
6. **Draft the revision** once entries are Approved: copy the current manual into the drafts folder (`General_WIP_Drafts`; the name must contain `DRAFT`) and run
   `python tools/qm_draft_revision.py --rev 5.2 --manual "<the draft copy>"`. It writes, into the drafts folder only and never over an existing file, a **change list** (`QM-001_Rev5.2_change_list_DRAFT_<date>.docx`) and a copy of the manual with each change as a
   Word **tracked change** (`QM-001_Rev5.2_DRAFT_<date>.docx`, author "KelpWorks draft"). A change is applied only when its before wording is found exactly once; anything else is listed under "Apply by hand" with the reason. The cover (revision, status, effective date)
   and the document-control fields are never touched. The register's **Drafted In** column records which draft each entry went into.
7. **Review in Word:** accept or reject each tracked change, apply the "by hand" ones, set the revision, status and effective date, and approve the revision through the normal document-control process (SharePoint).
8. **Enter it:** on the register, fill Manual Revision and Entered in Manual (date). The report no longer lists the entry.

## The report

`python tools/changelog_write.py --qm-report` lists (1) Released items with no confirmed impact (KW-23, KW-51 and KW-66 first), (2) register entries waiting for the Quality Manager's decision, (3) approved entries not yet in the manual (not yet drafted / drafted and
waiting to be entered), (4) rejected or "needs change" entries.

## One-time setup and backfill (done once on the master)

`python tools/changelog_write.py --qm-setup` adds the column and the sheet (it refuses to run twice). `python tools/changelog_write.py --qm-backfill docs/backlog/qm001-backfill.json` writes an impact for every Released item that has none (never overwriting one) and creates
a register row for each Ref the manual already carries. The impacts the item's own record settles (a QM-001 Ref, or "no requirement changed") are confirmed; the rest start with `PROPOSED` for the Quality Manager to confirm or correct once: edit the cell in Excel (remove
the word, or change it) and run `python tools/changelog_write.py --publish`. Close the master in Excel before running a command that writes. `--restore latest` is the way back.

## Safety

- The controlled manual is never opened for writing. The generator reads a draft copy that must sit in the drafts folder, writes new files only, compares the source file's checksum before and after, and re-reads its result: every XML part is well-formed, rejecting
  every tracked change gives the original text back, accepting all of them holds every proposed wording.
- The Quality Manager's columns are never written by a tool; `Entered in Manual` is entered by a person after the revision is approved.
- No change to the running ERP, its database or its live data: this is change-control tooling, tests and documents.
