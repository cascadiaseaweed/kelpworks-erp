---
description: Work one (or a bundle of) KelpWorks change-log items, e.g. /kw-item KW-24
argument-hint: KW-24 [KW-25 ...]
---
Work on KelpWorks change-log item(s): $ARGUMENTS

Follow these steps in order. Do not skip one; say so if one does not apply.

1. **Read the item.** Open `docs/backlog/items.csv` and find the row(s) for the ID(s) above (ID, module, section, change request, rationale, priority, mode, acceptance criteria).
   The Excel workbook is the master for Status / Owner / Notes; the CSV is a snapshot of the definitions. Also read `docs/backlog/open-decisions.csv`, `CLAUDE.md` (the sections for the area
   the item touches) and `docs/release-guide.md`.
2. **Decisions first.** If an Open Decisions row exists for the item and its `Decision` column is blank, or the item's own text is ambiguous, STOP and ask the user (one clear question with
   your recommendation) before building. Never guess a decision.
3. **Branch.** Make sure the working tree is clean and no other branch work is mixed in. Check `gh pr list` for open pull requests that touch the same files (a conflict risk). Create
   `updates/kw-<number>-<short-name>` from the latest `origin/main` (a bundle is named after its first ID; fixes may use `hardening/kw-...`). Never work on `main`.
4. **Plan.** "Plan mode" items: write the plan (files, data changes, tests, risks) and get the user's approval before editing. "Direct" items: give a 3-5 bullet plan and go ahead unless a
   risk flag below applies, then treat it like a Plan-mode item.
5. **Risk flags (check each; mention the ones that apply):**
   - new table or column -> additive and idempotent only (`SCHEMA` / `migrate()`), a table must be added to `KEEP` or `CLEAR` in `tools/make_clean_backup.py`;
   - production-log content that is signed (new field, new section, a notes section) -> `RELEASE_SNAPSHOT_VERSION`, the required-fields registry (`PROGRESS_SECTIONS`) and the progress rules;
   - a calculated value or a constant -> the `CALCULATIONS` entry and `SETTINGS_DEFAULTS` on the Calculations page;
   - stock -> only through `_consume`; stock never blocks a run;
   - uploads -> the upload rules (file types, storage room, daily quota) and the archive;
   - release, Certificate of Analysis, specifications, required fields, records or document control -> a Quality Manager must be told: add it to the Quality Manual (QM-001) change list;
   - anything that changes an audit control -> `docs/data-integrity-statement.md`.
6. **Build.** Standard library only (no new pip packages). Match the code around it. Add tests in `tests/` (extend the right file or add one) that fail without the change; run the FULL suite
   (`python -m pytest`) and fix failures. Verify the change in the browser preview (restart the server first) and look at the console for errors. Update `CLAUDE.md` and any guide the change makes
   untrue.
7. **Stop and report.** Summarise: what changed (files), how each acceptance criterion is met, tests run, anything decided on the user's behalf, anything needing the user (decisions, Quality
   Manager, a setting to change on the live site). Do NOT commit, push or open a pull request until the user says so.
8. **When told to commit / push / open the pull request:** commit message and pull request title start with the item ID ("KW-24: ..."); the pull request body lists the acceptance criteria as
   a checklist, says what was tested, and names any follow-up. Never merge: the user merges.
9. **After the user says it is live:** add the live commit to `tests/legacy_commits.txt` (a small follow-up pull request) and remind the user to set the item's Status to Released in the
   Excel workbook (re-run `python tools/export_backlog.py "<workbook>"` to refresh the CSV).

Keep it small: one item (or the bundle named in the arguments) per branch.
