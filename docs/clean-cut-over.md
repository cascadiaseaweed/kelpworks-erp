# Clean cut-over: resetting live to a clean start

How the live system was reset from pre-go-live test data to a clean database on 2026-10-23 (change KW-60), written as a repeatable workflow. Use it if
the live data ever has to be cleared again (for example after a second round of pre-go-live testing). The commands and the restore mechanics are in
`docs/restore-runbook.md` ("Wipe everything and start fresh"); this page is the order of work, who does what, and what to keep as a record.

**It is irreversible on live** apart from two copies: the PRE-GO-LIVE ARCHIVE backup (step 2) and the `pre-restore-<utc>.db.gz` the restore keeps on the disk.
Do not start unless both exist.

## Who does what

| Who | What |
|---|---|
| Administrator (N. Wrana) | Takes the backup, copies files in SharePoint and to Render, edits the Render environment, signs in to check. Claude cannot reach Render or SharePoint and never runs anything against live. |
| Claude Code | Builds and tests the tool, makes the clean file, rehearses it on a throwaway local server, drafts the cut-over note, and reads the log lines the administrator pastes. |
| Quality Manager | Completes and signs the cut-over note. It is the QM-001 record of the reset. |

## The workflow, in order

**1. Freeze.** Tell everyone not to enter anything in live. Anything entered after the backup is lost.

**2. Full backup of live, kept as the controlled record.** Admin > Download full backup. Copy it to the SharePoint library `KelpWorks-Records` > `00_Pre-Go-Live` as
`PRE-GO-LIVE ARCHIVE <date>.zip`, **outside** `01_System-Backups` so no retention cleanup touches it, and restrict that folder to administrators (the backup holds every user's
password hash). Check that the copy has the same SHA-256 as the download (`Get-FileHash <file> -Algorithm SHA256`).

**3. Make the clean file** from that backup (any PC with Python; it only reads and writes files):

```bash
py tools/make_clean_backup.py <backup>.zip --dry-run
py tools/make_clean_backup.py <backup>.zip <clean>.zip --by "Your Name"
```

Read the dry run first. The tool checks its own result: every record table empty with its numbering reset, every configuration table identical row for row, and only the
intended differences (inventory items, three backup flags, one new `system / clean_start` entry in the change history). It writes `<clean>.report.txt` (the proof) and
`<clean>.cut-over-note.md` (the draft QM-001 note). A backup from an older release works if it has the inventory and change-history tables.

**4. Rehearse (Claude, on a throwaway local server).** Restore the real clean file on a temporary server with its own database, then check: the restore log line, configuration
row counts unchanged, record tables empty, the standard inventory items recreated at the first start, the `clean_start` entry, sign-in, audit chain intact, and the first tote
lot / first run number are 001. Also restore the full backup the same way to prove the archive copy is usable. A rehearsal on the Render staging service is better still
(`docs/staging.md`); on 2026-10-23 it was skipped by choice, so the first Render restore was the live one.

**5. Clear the old records archive.** In `KelpWorks-Records` delete the contents of `02_Production-Runs`, `06_Receiving` and `01_System-Backups`, and delete
`_sync-state.json` in the library root. Never `03_SOPs`, `04_Safety-Data-Sheets` or `00_Pre-Go-Live`. The change log is elsewhere and untouched. Do this immediately before
step 6: the nightly sync could otherwise copy the old run back before the restore.

**6. Put the clean file on the Render disk.** Over SSH from PowerShell (key set up once, address from Render > live service > Connect > SSH):

```powershell
scp "<clean>.zip" srv-XXXX@ssh.oregon.render.com:/var/data/restore_me.zip
```

Then on the server check the size and checksum against the file on your PC:

```bash
ls -l /var/data/restore_me.zip
sha256sum /var/data/restore_me.zip
```

Do not go on unless both match. A line `client_global_hostkeys_prove_confirm: server gave bad signature for ED25519 key 0` after the sign-in is a harmless host-key update message from
Render's SSH gateway; the checksum is the proof that the file arrived intact.

**7. Arm the restore.** Render > the **live** service (not staging) > Environment: add `KELP_ERP_RESTORE_FROM=/var/data/restore_me.zip` and
`KELP_ERP_RESTORE_CONFIRM=replace-live-data`, save. Render redeploys and the server restores at start.

**8. Read the log.** Look for `RESTORED from /var/data/restore_me.zip.restored-<utc>` (the zip is renamed so it cannot run twice). `RESTORE FAILED` means nothing changed: fix the
cause and arm again.

**9. Remove both variables** and save (one more redeploy).

**10. Verify** (administrator, then the Quality Manager): sign in with the usual password; Product Release > Verify audit trail says the chain is intact; Admin > Data integrity check is
clean; Feedstock Inventory and Product Release are empty; Calculations, the SOP register with its links, the Certificate of Analysis specifications and the lab catalogue are unchanged;
the change history shows the `clean_start` entry. Do not enter test data in live. Tell users to refresh their browsers (they stay signed in).

**11. Run the archive sync once by hand** (office PC) so the archive indexes rebuild from the clean database.

**12. Records.** Complete the cut-over note (restore date and time from the log, who ran it, the date the old archive folders were deleted, the pre-go-live backup location and checksum,
the restore test) and have the Quality Manager sign it. Store it with the pre-go-live backup, record its QM-001 reference in the change-log item, and mark the item Released.

**13. Tidy up (they hold password hashes).** Delete the full backup and the clean zip from Downloads, and delete the renamed `restore_me.zip.restored-<utc>` from the Render disk
(Render > the service > Shell, or SSH, then `rm /var/data/restore_me.zip.restored-<utc>`). Keep `/var/data/backups/pre-restore-<utc>.db.gz` (the way back) for as long as you
might need it, then delete it too: it holds the old data and password hashes.

**14. Hand over.** Enter the real inventory (KW-64) and start production.

## What the reset does and keeps

Cleared: every record, every uploaded document and photo, the release / audit history, and every inventory item except the four the program finds by name (Citric Acid, Potassium Sorbate,
New and Used 1,000 L IBC Tote), which stay as blank items. Numbering restarts at 001. Kept: users and passwords, settings, products, specifications, labs, SOP / SDS register, quality-chart limits
and the history of changes to those. Old records exist only in the pre-go-live backup, so a reused number overwrites nothing but also cannot be looked up in live.

## Lessons from 2026-10-23

- The existing `tools/make_clean_backup.py` plus the start-up restore already did most of the work; extending them avoided a new in-place command on the live database.
- Four inventory items must stay because the code needs them and `seed()` never runs again (they are found by a stable key, so one that was renamed is still kept); the other standard items come back at every start.
- A backup from an older release lacked tables and crashed the tool; fixed (PR #95). Make the clean file from a fresh backup anyway.
- The sync script's `_sync-state.json` is only its memory of files copied; deleting it does not touch the change log.
- Staging rehearsal was skipped; a local throwaway-server rehearsal of the same file was done instead.
