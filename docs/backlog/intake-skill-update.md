# Update for the Claude chat intake skill (KW-79)

**Purpose.** Every new change request must carry a **Type** (one value) and **Touches** (one or more areas). `tools/changelog_write.py` rejects a `new_item` note without both, with the lists in the reason. **Owner is set automatically** (N. Wrana): the skill must not send it. Paste the text below into the `kelpworks-change-intake` skill (Claude > Customize > Skills > that skill), keeping its existing steps. The two lists must stay identical to the ones in the code; `tests/test_changelog_classification.py` checks this file against `tools/changelog_write.py`.

---

## Text to add to the skill

### In the note format for `new_item`

`"Type"` is no longer free text: it is exactly ONE value from the Type list below (it is the category of the change). Add `"Touches"` (required): one or more values from the Touches list, separated by semicolons, for example `"Production log; Product release"`. Use the spelling below exactly; a note with any other value, or without either field, is rejected. Do not send `Owner` (it is set to N. Wrana by the tool).

### How to choose

1. **Type = what kind of change is it, mainly?** Pick the single best fit. Tie-breakers: a change to who can do what, to secrets, to backups or to recovery is *Security & hardening*; a change that keeps stored records correct, complete or traceable (audit trail, lots, reconciliation, migrations, foreign keys) is *Data integrity & traceability*; a change driven by a QM-001, ISO 9001 or SQF requirement or its evidence (CoA, specifications, lab results, document control, change control) is *Quality & compliance*; how people use or see a screen (layout, speed, labels, printing) with no new rule is *User experience*; work outside the software is *Operations (non-ERP)*.
2. **Touches = which parts of KelpWorks does it reach?** List every area the change would edit or affect (the tool stores them in list order). Use *Whole app* only when a change really is app-wide; use *Outside KelpWorks* for work that does not change the software.
3. **Propose both from the request text and show them to the user for confirmation before writing the note.** If the user corrects either, use the correction.
4. Revisions may change them too (only while the item is Not started, Deferred or Blocked). A new item without them is rejected, so never send one without.

### Type (exactly one)

<!-- TYPES -->
- Security & hardening
- Data integrity & traceability
- Quality & compliance
- Production workflow
- Inventory & shipping
- Samples & lab
- Reporting & analytics
- User experience
- Equipment & maintenance
- Process & tooling
- Operations (non-ERP)
<!-- /TYPES -->

### Touches (one or more, separated by semicolons)

<!-- TOUCHES -->
- Production log
- Feedstock inventory
- Pre-processing
- Inventory items
- CIP / SIP log
- Samples
- Product release
- Finished goods / shipping
- Documents / SOPs
- Admin / users
- Settings / Calculations
- Reports / QC charts
- Records archive / backups
- Database / schema
- Change-log tooling
- Labels / printing
- New module
- Whole app
- Outside KelpWorks
<!-- /TOUCHES -->

### Examples

A hardening item: `"Type": "Security & hardening", "Touches": "Admin / users; Records archive / backups"`

A screen-only item: `"Type": "User experience", "Touches": "Feedstock inventory; Labels / printing"`

### In the skill's description

Say that every new change request is classified with a Type and Touches from fixed lists and that the user confirms them before the note is written.

### QM-001 impact (KW-71)

Every item also records what it does to the Quality Manual (QM-001), but that is **not** a note field: Claude Code writes the `QM-001 Impact` column when the item is planned and released, so the skill must not send it. When the user asks the skill to draft a manual change
for an item, it can leave a `qm_entry` note (format in `docs/backlog/inbox-note-format.md`: kw_items, section, change = Replace / Add / Delete, before, after, insert_after for an Add, reason). It never sets a decision: the Quality Manager decides each entry in Excel.
