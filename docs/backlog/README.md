# KelpWorks change log (backlog)

- **Master:** the Excel workbook `KelpWorks_ChangeLog_<date>.xlsx` in SharePoint (QAQC > KelpWorks ERP). Status, Owner and Notes are kept there.
- **`items.csv`, `open-decisions.csv`:** a snapshot of the workbook's Change Log and Open Decisions sheets, so Claude Code can read an item by its ID. Refresh after the workbook changes:
  `python tools/export_backlog.py "<path to the workbook>"` (standard library only). The Status column in the CSV can be out of date; the workbook is right.
- **Updating the master:** `python tools/update_backlog_xlsx.py "<workbook>" updates.json [--dry-run]` sets an item's Status / Owner / Notes and a decision's Decided By / Decision (see the tool's
  header for the JSON format). It edits only those cells inside the .xlsx, so the formatting, drop-downs, formulas and SharePoint properties stay as they are, and the Summary counts recalculate when
  Excel opens the file (close the workbook in Excel first). Claude Code keeps the workbook current as items move (`/kw-item`, step 10); then re-run the export above.
- **Working an item:** `/kw-item KW-24` (`.claude/commands/kw-item.md`): read the item, ask about any open decision, branch, plan, build with tests, report, and stop before committing.
- The workbook's "Claude Code Prompt" column is not exported: it was written for a Flask / SQLAlchemy stack, which KelpWorks is not.
