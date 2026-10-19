# KelpWorks change log (backlog)

- **Master:** the Excel workbook `KelpWorks_ChangeLog_<date>.xlsx` in SharePoint (QAQC > KelpWorks ERP). Status, Owner and Notes are kept there.
- **`items.csv`, `open-decisions.csv`:** a snapshot of the workbook's Change Log and Open Decisions sheets, so Claude Code can read an item by its ID. Refresh after the workbook changes:
  `python tools/export_backlog.py "<path to the workbook>"` (standard library only). The Status column in the CSV can be out of date; the workbook is right.
- **Working an item:** `/kw-item KW-24` (`.claude/commands/kw-item.md`): read the item, ask about any open decision, branch, plan, build with tests, report, and stop before committing.
- The workbook's "Claude Code Prompt" column is not exported: it was written for a Flask / SQLAlchemy stack, which KelpWorks is not.
