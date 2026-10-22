# Inbox note format (for Claude chat)

Claude (chat) does not write the change-log workbook. To add or revise an item it writes ONE file per note into `Change_Control\Inbox` (UTF-8 JSON, `<note_id>.json`). Claude Code then runs
`python tools/changelog_write.py --apply-inbox`, which validates every note and applies the valid ones. A copy of this text is kept in the Inbox as `README_NOTE_FORMAT.txt`.

## New item

```json
{"note_id": "chat-20261022-001", "kind": "new_item", "from": "Claude (chat)", "created": "2026-10-22T18:00:00Z",
 "fields": {"Module": "Production Log", "Section": "Separation", "Type": "Change", "Change Request": "...", "Context / Rationale": "...",
            "Transcript Time": "...", "Raised By": "N. Wrana", "Priority": "P2", "Lens": "...", "Claude Code Mode": "Direct", "Acceptance Criteria": "..."}}
```

The writer assigns the next free KW number, Status "Not started", Approved to Start blank (a human approves it), Row Updated = the day it was applied and Updated By = "Claude (chat) (inbox)".
Required fields: Module, Section, Type, Change Request, Raised By, Priority, Claude Code Mode, Acceptance Criteria. Priority and Claude Code Mode must be values already used in the sheet (for example P1, P2, P3 and Direct, Plan mode).

## Revision of an item

```json
{"note_id": "chat-20261022-002", "kind": "revision", "from": "Claude (chat)", "created": "2026-10-22T18:05:00Z", "id": "KW-66",
 "based_on": {"Row Updated": "2026-10-22"}, "revision_summary": "Acceptance criteria now include the restore test",
 "fields": {"Acceptance Criteria": "..."}}
```

Allowed only while the item's Status is Not started, Deferred or Blocked (never In progress or On staging; a Released or Won't do item needs a new item). `based_on.Row Updated` is the value you saw in the SharePoint copy: if the item changed
since, the note is rejected as stale. `revision_summary` (up to 300 characters) is appended to Context / Rationale as `Revised <date> (Claude chat): <summary>`. If Approved to Start is Y and the scope changed, the result says so: the human clears it so the item is approved again.

## New decision (a row on the Open Decisions sheet)

```json
{"note_id": "chat-20261022-003", "kind": "new_decision", "from": "Claude (chat)", "created": "2026-10-22T18:10:00Z",
 "fields": {"ID": "KW-67", "Decision Needed": "...", "Options Discussed": "A / B", "Recommendation (Claude)": "..."}}
```

The ID must be an item on the Change Log; all four fields are required. Decided By and Decision are a human's and cannot be set from a note. The same ID with the same Decision Needed is a duplicate.

## Plan cells (named cells on a KW plan sheet)

```json
{"note_id": "chat-20261022-004", "kind": "plan_cells", "from": "Claude (chat)", "created": "2026-10-22T18:15:00Z", "sheet": "KW-60 Clean Start Plan",
 "cells": [{"cell": "C37", "was": "", "now": "2026-10-23 NW"}]}
```

The sheet name must be `KW-<number> <words> Plan`. `was` is the text the cell holds in the copy you read; if it differs now the note is rejected as stale. At most 50 cells, only in rows that exist (a missing cell in an existing row is added).

## What can be set, and what happens to a note

- On an item, only the intake columns: Module, Section, Type, Change Request, Context / Rationale, Transcript Time, Raised By, Priority, Lens, Claude Code Mode, Claude Code Prompt, Acceptance Criteria. Everything else (Status, Notes, Owner, Approved to Start, PR #, dates, QM-001 Ref, ID) is refused.
- Values must be text, without control characters, up to 30,000 characters.
- Applied notes move to `Inbox\processed`, rejected ones to `Inbox\rejected`; each gets `<note_id>.result.json` with `result` (applied / rejected / unchanged), `id` and `reason`. Read it to know what happened.
- Rejected for: invalid JSON; missing or reused `note_id` (note_ids are unique forever); unknown `kind`; a column that is not an intake column; missing required fields; a Priority / Claude Code Mode not used before; a Change Request equal to an existing item's;
  a stale revision; an unknown item.
