# Text for the workbook's "Workflow Rules" sheet

The Workflow Rules sheet is yours (no tool writes it). Replace its text with the points below; they match `CLAUDE.md` and `docs/sop-change-requests.md` after KW-66.

**1. Files**
- Master (the only file that is written): `C:\KelpWorks\ChangeControl\LOG_KelpWorks_ChangeControl.xlsx`. Edit it here, in Excel desktop. Not in OneDrive.
- SharePoint copy (`Change_Control\LOG_KelpWorks_ChangeControl.xlsx`): READ-ONLY, refreshed after every tool write and by `python tools/changelog_write.py --publish`. Never edit it.
- Backups: `C:\KelpWorks\ChangeControl\Backups` and `Change_Control\Backups`, newest 30 each, one before every write. `--backups` lists, `--restore latest` restores.

**2. Who writes what (header colour on Change Log)**
- Intake columns A-M (Module ... Acceptance Criteria): new items and revisions come ONLY from Claude (chat) inbox notes, applied by Claude Code (`--apply-inbox`). Nobody else adds rows or KW numbers.
- Claude Code: Status (never Deferred or Won't do), Notes, PR #, Released Date, QM-001 Ref, Row Updated, Updated By, through `tools/changelog_write.py` only.
- Human: Owner, Approved to Start, Verified By, Verified Date, Deferred / Won't do, Open Decisions answers (Claude Code can record an answer you give: `--decision`).

**3. Rules**
- An item is started only when Approved to Start is Y or Pre-rule.
- Statuses: Not started, In progress, On staging, Released, Blocked; Deferred and Won't do are human only. Released needs a PR # and a Released Date; "Released" is not done until Verified By / Verified Date are filled.
- Claude (chat) never writes the workbook; it writes an Inbox note (`docs/backlog/inbox-note-format.md`).
- Close the master in Excel before Claude Code writes (it stops and says so if a `~$` lock file exists). It re-opens the file about 10 seconds after saving to confirm the values stuck, and takes a backup first.
- After you edit the master by hand, run `--publish` so the SharePoint copy shows it.

**4. If something looks wrong**: stop, `--backups`, `--restore <name>`, tell the approver.
