# KelpWorks ERP — Claude Code project guide

A small **manufacturing / processing ERP** for Cascadia Seaweed's liquid kelp
extract (LKE) line: stabilized kelp inventory → production runs → finished
goods → customer shipments, plus reagents/packaging inventory, reports, barcode
labels, and an admin panel.

## Golden rules (read before editing)

1. **Standard library only — no third-party runtime dependencies.** The whole
   server is Python stdlib (`sqlite3`, `http.server`, `hashlib`, `hmac`,
   `zipfile`). Even the Excel export is a hand-rolled OOXML writer (see
   `XlsxSheet` / `xlsx_build` in `kelp_erp_server.py`). Do **not** add `pip`
   deps unless the user explicitly agrees.
2. **Schema changes are additive and idempotent.** New tables go in the
   `SCHEMA` string as `CREATE TABLE IF NOT EXISTS`. New columns on existing
   tables go in `migrate()` guarded by a `PRAGMA table_info` check. `init_db()`
   runs `SCHEMA` → `migrate()` → `seed()` (first run only) → `ensure_users()`
   every startup, so migrations apply automatically on deploy with no data loss.
3. **Keep `print()` ASCII-only** — the Windows dev console is cp1252 and chokes
   on non-ASCII. (File/HTTP content is UTF-8 and fine.)
4. **Verify in the browser preview** after changes, then restart the server.
5. **Keep the Calculations page in sync.** `CALCULATIONS` in `app.js`
   (rendered by `pageCalculations`) documents every calculated field in the
   app. Whenever you add, change, or remove a calculated field (a value
   derived from other fields via a formula — not a report SUM/COUNT total),
   update its `CALCULATIONS` entry to match; remove the entry if the field is
   deleted. Any new hardcoded constant a calculation depends on (a threshold,
   default, conversion factor) belongs in `SETTINGS_DEFAULTS`
   (`kelp_erp_server.py`) as an admin-editable `settings` row instead of a
   bare literal — read it via `get_setting_value(conn, key, default)`
   server-side or `settingValue(key, fallback)` client-side, and list its key
   in the calculation's `settings` array so it shows up on that page.

## Run it

```bash
python kelp_erp_server.py       # or: run.bat
```
→ http://localhost:8002 · seed admin **admin@kelp.local / kelp1234**.
No build step, no install. First run creates + seeds `kelp_erp.db` from `seed.json`.

## Layout

- `kelp_erp_server.py` — the entire backend: DB schema, migrations, auth, and
  every API route (one `Handler` class; routes dispatched in `_route()`).
- `public/index.html`, `public/app.js`, `public/styles.css` — vanilla-JS SPA
  (no framework). `logo.png` / `logo-white.png` are the Cascadia brand marks.
- `seed.json` — reference data + the initial stabilized tote lots (extracted
  from the 202605 inventory workbook).
- `Dockerfile`, `render.yaml`, `Procfile`, `runtime.txt` — Render deploy.

## Backend conventions (`kelp_erp_server.py`)

- **Auth:** PBKDF2 password hashing + HMAC-signed bearer tokens (`make_token` /
  `read_token`). `_auth()` loads the user row; `_require_admin()` gates admin
  routes. Users have `role` (admin/user), `active`, `must_change_password`.
- **Routing:** everything is under `_route()`. JSON in/out via `_send_json` /
  `_body_json`. Binary responses (attachment download, `/api/reports/xlsx`) are
  handled specially in `do_GET` and authenticate via the `Authorization` header
  **or** a `?token=` query param (so files/sheets can open in a browser tab).
- **Inventory stock** (the `consumables` table, shown on the "Inventory Items" tab; the UI calls the general group
  "Reagents") moves through `_consume(conn, id, delta, reason, ref)` which updates
  `on_hand` and logs a `consumable_txns` row (feeds the ledger). One table, three
  groups told apart by flags: Packaging = `is_container`, finished-good labels =
  `label_sku_code`+`label_package` set (one item per SKU + package type, deducted 1
  per container consumed by the Packaging commit (`_commit_label_stock`, net-change via
  `run_label_commits`) -- not the per-row Label buttons, which print internal
  barcodes), Reagents = the rest. **Item #** = `<CAT>-<3-digit sequence>` (RGT reagent, CIP cleaning agent, PKG packaging
  container, SMP sample container, LBL finished-good label; `item_category` / `next_item_number` / `assign_item_numbers`): auto-assigned
  once (boot backfills existing items in a tidy order; new items get the next number), never reused, never encodes attributes. An admin
  may type another unique value, but an item is never left without one. The FG-label table is grouped under a heading per SKU.
- **Stock never blocks a run.** Every consumable deduction in a production run (reagents, sample containers, packaging, FG labels,
  and Pre-Processing pack-out via `_adjust_container_stock`) just lets `on_hand` go negative (the item shows LOW); nothing raises "Not
  enough ... on hand". **Sample Point defaults** (`_sample_point_defaults`, applied when the description/type is changed, never over a
  value sent in the same request, new rows start Slurry/Microbial): Microbial -> qty 1 + 50 mL falcon tube; Metals & Nutrients -> qty 2 +
  50 mL falcon tube; type Solid -> 100 g sample bag.
- **Reagent types** (`consumables.reagent_type`: Citric Acid / Potassium Sorbate / Sodium Benzoate, set in Inventory Items > Edit; backfilled
  from the item names once) group inventory items that are the same reagent. A run stores which item it draws each reagent from
  (`production_runs.dilution_{citric,ksorbate,nabenzoate}_item_id`, shown in the log only when set so old signed snapshots don't change;
  default = the item named like the type, else the first of that type); `_commit_reagent_usage` deducts from that item and, if the choice
  changes, refunds the old item and charges the new one (`run_reagent_commits.consumable_id`). `GET /api/reagents[?runId=]` feeds the
  pickers; `buildReagentWatch` (app.js) renders the pickers and the "Exceeds inventory" note under each reagent field (stock = on-hand + what
  this run already deducted from that item). Pre-Processing's citric acid picks an item the same way (`preproc_batches.citric_item_id`).
- **Reagent usage** (Citric Acid, Potassium Sorbate, Sodium Benzoate) is deducted
  by `_commit_reagent_usage` from the Dilution & Preservation entries on that
  section's Save and at finalize: one net-change ledger line per reagent
  (`run_reagent_commits` remembers what was committed; a draft discard refunds it),
  same model as Packaging's `_commit_packaging_stock`.
- **CIP (Clean In Place) log** (`cip_events` + `cip_event_chemicals`, `/api/cip`, the
  "CIP Log" tab) is a standalone record per cleaning -- not part of a production run
  (optional `run_id` link only). Its chemical lines are the source of truth for CIP
  agent consumption: `_apply_cip_stock` deducts/refunds the *difference* on save/edit/
  delete (one ledger line per agent, ref = `CIP-YYYYMMDD-NNN`) and never blocks on a
  shortage. Agents are reagents flagged `consumables.is_cip_agent` (seeded: CIP Acid,
  CIP Caustic, CIP Sanitizer, in L).
- **Yield & Usage** (`route_yield_usage`, `/api/yield-usage` + `/xlsx`, the "Yield & Usage"
  tab) is a read-only report over completed runs: two conversion rates -- process
  (output L / measured `run_inputs.weight_kg`) and harvest (output L / stored
  `input_kg`, the batch-average tote weight) -- plus per-run consumption read from the
  consumable ledger (`ref` = processing lot; reagents / packaging / sample containers /
  FG labels). Group-by is a set of checkboxes (`groupBy=a,b` over `YU_DIMS`: sku, species,
  farm, stabilization, harvest_month, processing_month); species/farm/harvest date come from
  the run's consumed totes, and a run holding several values of a ticked dimension goes in
  that dimension's "Mixed" bucket. Run rows show harvest date (tote check-in), processing
  date (`production_runs.finalized_at`, stamped at finalize; older runs fall back to the run
  date, shown "(est.)"), extraction efficiency ((`extraction_tds_pct` - `homog_tds_pct`) / `homog_tds_pct`, %) and final pH/TDS (Packaging QC Check `packaging_qc_ph`/`packaging_tds_pct`).
  `production_runs.exclude_from_stats` (+ reason,
  set in Edit run) removes a test/spoiled run from the stats. Runs finalized before
  reagent deduction have no usage data and are left out of usage stats, not counted as 0.
- **Product release (review + Quality sign-off)** gates sales. Finalizing a run sets
  `production_runs.release_state='pending_review'` and creates its `fg_lots` with status
  `pending_release` (not shippable: `create_shipment` only ships `on_hand`; `PUT /api/fg`
  can't move a lot in/out of `pending_release` or to `on_hand` unless the run is released).
  A user flagged `is_production_manager` or `is_quality_manager` (Admin > Users; admin role
  alone grants nothing; flag changes go to `user_permission_log`) signs the production-log
  review (-> `pending_release`, awaiting QA), then a Quality Manager signs release (lots ->
  `on_hand`) or rejects (lots -> `hold`); the same person may do both. Endpoints under
  `/api/release` (`route_release`/`_release_action`); every signature re-checks the password.
  Audit = append-only `release_events`, hash-chained (`release_log` / `release_verify_chain`),
  each with the SHA-256 of the production-log snapshot (`_release_snapshot_hash`, built from
  `_run_full` minus edits / FG lots / the exclusion flag). Any non-GET under
  `/api/production/<run id>/...` runs `_release_reconcile`: if the log no longer matches the
  signed hash the sign-off is voided (run -> pending review, unsold lots -> pending release,
  shipped units recorded in the event). Runs finalized before this existed are `legacy`
  (grandfathered as released, one SYSTEM event each, via `migrate()`). Don't add a way to
  edit/delete `release_events` rows.
- **Production-log required fields** are defined once, server-side, in `PROGRESS_SECTIONS` /
  `REQUIRED_FEEDSTOCK` (`kelp_erp_server.py`; exposed to the SPA as `refdata.requiredFields`).
  Everything is required except: every Notes field, the Homogenization / Separation /
  Pasteurization sample-point boxes, Extraction's QC Check Total-solids + Density fields,
  checkboxes, calculated values and legacy dilution-tank rows. `_run_progress` returns
  per-section `{total, filled, missing, done}` (attached to drafts and `_run_full`; also
  `GET /api/production/<id>/progress`) -- the progress chips are green only when a section is
  `done`. `_finalize_run` ends with `_required_problems`; raising there rolls the whole
  finalize back. The SPA marks required labels with `rfield()/reqLabel()` (red `*`), and
  `finalizeRun` saves the header + presses every `button.section-save` before finalizing. When
  adding a production-log field, add it to the registry (and use `rfield`) or it stays optional.
  The feedstock accept/reject decision must be an explicit choice: `run_inputs.decision` is
  NOT NULL, so `run_inputs.decision_set` records whether an operator actually chose (inputs of
  runs finalized before it existed were backfilled as set; `_run_inputs_public` returns
  `decision: None` until set).
- **Tank 5A/5B -> 6A/6B dilution**: the plan (Pasteurization In: 5A/5B levels, receiving tanks 6A / 6B / 6A+6B, tank 6A/6B starting
  levels, TDS = Separation filtrate TDS, target = SKU) is calculated client-side by `dilutionPlanCalc` (c1V1=c2V2: max product =
  space x c2/c1, transfer = min(available, max), water = transfer x (c1/c2 - 1), 0 if c1<=c2) and stored on `production_runs`
  (`pasteurization_*`); the actuals (product transferred, water added, final level per tank, per-tank Ksorbate/benzoate added,
  variance vs the `dilution_variance_flag_pct` setting) are the `dilution_*` columns. The run-level `dilution_fill_level_tank_6ab_l`
  and `dilution_ksorbate/nabenzoate_added_l` are now the SUMS of the per-tank values, so reagent deduction is unchanged.
  Per-tank required fields use the optional 4th element of a `PROGRESS_SECTIONS` field entry (a predicate over `stages`).
  Capacity per tank is the `dilution_tank_capacity_each_l` setting (connected = double).
  Product left in 5A/5B => further passes: `run_dilution_passes` (routes `/api/production/<id>/dilution-passes`, UI
  `buildDilutionPassCard`) each carry their own plan + actuals + per-tank preservatives; `_recompute_dilution_totals` keeps the
  run-level Ksorbate/benzoate totals = pass 1 + all extra passes, so the reagent deduction covers every pass (each pass also has its own pH balancing: measured pH + citric acid added, with the
  citric acid summed into the run's citric usage by `_commit_reagent_usage`). Pass items are
  required-field items in the Dilution & Preservation section, and `dilutionPasses` is part of the signed-log snapshot.
- **Pre-Processing (shred + blend)** (`preproc_batches` / `preproc_inputs` / `preproc_packaging`, `route_preproc`,
  `/api/preproc`, the "Pre-Processing" tab: batches are cards like production runs -- `pagePreproc` / `preprocDraftCard` (section chips from `_preproc_progress`, the same `stageProgress` component) / `preprocDoneCard` -- and `openPreprocBatch` opens the editor / batch record in a wide window) turns coarse-grind feedstock into
  fine-grind feedstock (harvest check-in / CSV import set `grind`, default Coarse; check-in no longer has a storage-unit source or deducts empty-IBC stock). A draft batch pulls coarse totes (`tote_lots.grind='Coarse'`, in stock, not on QAQC Hold) from a
  pick list (status -> `wip`, same lock as a run), each characterized with the shared `buildFeedstockCard`
  (`/api/totes/:id/characterize` + `/photo`, so it lands in the tote's own stability log; the shredded mass is simply the sum
  of the pulled totes' weights -- there is no separate shredding section), then blend solids loading (all optional;
  `preprocPlanCalc`: water = kg x (start%/target% - 1), target default = `preproc_target_solids_pct`; final % solids is CALCULATED =
  start% x kg / (kg + water added), not measured),
  pH balancing (measured pH + citric acid kg, default target `preproc_target_ph`) and pack-out rows (empty IBC container x
  qty x fill L). Completing (`_preproc_complete`, one transaction, all required fields enforced by `_preproc_problems`) creates one
  `tote_lots` row per output IBC (`<batch lot>-NN`, `grind='Fine'`, `preproc_batch_id`, `solids_pct`, status in_stock; site/species inherit
  when every source agrees, else the `MIX` placeholder rows), consumes the sources (`consumed`, no run), deducts Citric Acid and the
  output containers, and returns the emptied source IBCs to the Used IBC pool. A completed batch is read-only (no amend flow). Traceability:
  `preproc_inputs` + `GET /api/totes/:id/trace` (Feedstock Inventory "Trace" action); fine lots print a `blendLabel`. Output weight = blend
  mass (shredded kg + water) split by fill volume. `MIX` is hidden from harvest check-in; species-specific SKUs won't offer `MIX` lots in the run picker.
- **Samples (catalogue, retention inventory, lab cart, requisitions)** (the "Samples" tab, `pageSamples`; backend `route_samples` /
  `route_cart` / `route_requisitions` / `route_labs`). `samples` = one row per physical unit of a FINALIZED run's Sample Point rows
  (qty 4 -> 4 samples, stable code `<processing lot>-<STG>-<NN>`), kept in step by `sync_samples` (called after every non-GET
  `/api/production/...` write and `sync_pending_samples` at boot, which also backfills older runs); a unit whose row was
  removed/shrunk is dropped only while it is still available/in_cart -- submitted/removed samples are history. Status: available ->
  in_cart -> submitted (on a requisition) | removed (reason required, `sample_events` is the append-only history). Retention
  inventory = samples whose description is `Retention`; discard-by = collection date + `sample_retention_months`. Labs + their
  analyses (`labs`, `lab_analyses`) and an optional per-lab Word template (stored under `LAB_DIR`) are admin-maintained in Admin >
  Labs & analyses. The cart (`sample_cart`) holds a lab + analysis ids per sample; "Create requisitions" makes one
  `lab_requisitions` row per run + lab, fills the lab's .docx with `docx_fill` (stdlib `zipfile` + regex on the XML: `{{tokens}}`
  anywhere, and the one table row containing `{{sample.*}}` tokens is repeated per sample; `{{sample.check:Analysis}}` gives a
  checkbox column; labs without a template use `build_starter_docx`), saves it as a `run_attachments` document on the run (a paragraph with `{{analysis.name}}` repeats once per requested test, `{{po_number}}`
  / `{{date_long}}` are scalars; a lab flagged `sample_sheet` -- e.g. Food Assure, whose form says "see attached spreadsheet" -- also gets an
  `.xlsx` sample list from `build_sample_sheet_xlsx`; ready-to-upload token copies of the Food Assure and SGS forms live in `docs/requisition-templates/` -- see its README; analyses carry an optional `method` printed by `{{sample.methods}}`; repeated rows get unique `permStart/permEnd` / sdt ids so protected forms stay valid)
  (exempt from the amend lock and the release hash), marks the samples submitted and empties them from the cart.
- **Production Log Summary PDF** (`GET /api/production/<id>/summary.pdf[?dl=1&token=]`, completed runs only; the "Production log summary" card at
  the top of a run's Documents window). Generated on demand from the current log by `_run_summary_data` -> `build_run_summary_pdf` -- a
  stdlib-only PDF writer (`PdfBuilder`: standard Helvetica fonts via a built-in width table, WinAnsi/cp1252 text, tables, KPI cards, the logo
  decoded from `public/logo.png` once). Key results only, grouped: headline KPIs, a stage-by-stage "Process at a glance", a Quality scorecard
  (metric x stage matrix with target / final-vs-target instead of per-stage QC dumps), a Samples & lab work matrix (process point x sample
  type from the `samples` catalogue + requisitions), materials from the ledger, FG lots and sign-off. Not stored as a run attachment (never stale).
- **Certificate of Analysis (lab results + specs)** (`coa_specs`, `lab_results`; `ensure_coa_specs` seeds the specs INSERT OR IGNORE so admin
  edits stick; `coa_evaluate`, `build_coa_pdf`, `GET /api/production/<id>/coa.pdf`, `route_lab_results` = `/api/production/<id>/lab-results`,
  `route_coa_specs` = `/api/coa-specs`; UI: the run card's "Lab results" button / `openLabResults` + `addLabReport`, the CoA card in the Documents
  window, a lab block on the Product Release run, Admin > Certificate of Analysis specifications). Results are typed in from the uploaded lab reports
  (one row per analyte per report: value as reported -- number, `<` number, Negative/Positive -- unit, method, lab, report number/date, optional
  linked document) and are NEVER edited: a correction voids the row (reason required) and a new one is entered; add/void are logged to the
  hash-chained `release_events`. Latest non-voided result per spec wins; tests with no spec are "additional analyses". Spec basis: `run` (TDS/pH from the
  Packaging QC check), `value` (compared as reported; unit forced to the spec's -- microbial is per g), `absent` (Salmonella), `metal` (results
  stay in the unit the lab reported, ppm or %; converted to ppm and compared with `max_val` in ppm; `max_val` is NULL = listed, not judged,
  until the Admin "Heavy-metal limit calculator" (`POST /api/coa-specs/metal-limits`) sets it from the regulatory loading limit kept in
  `limit_kg_ha`: ppm = kg/ha x 1e6 / `coa_application_rate_kg_ha`; a `<` result whose detection limit exceeds the limit is REVIEW, not pass).
  `required` specs (the five microbial tests) BLOCK the Quality release (`_release_action`) until a result is on file; a FAILED result needs a
  release comment (400 without one; the comment prints on the CoA and the out-of-spec tests go in the `released` event detail).
  Microbial results are per gram of LIQUID product as reported (no TDS/solids conversion).
  **Report scan** (`POST /api/production/<id>/lab-results/scan {attachmentId}`): "Add lab report" takes an uploaded (or already attached) PDF and
  pre-fills the form -- `pdf_text_lines` is a stdlib PDF text reader (Flate streams, simple + Type0 fonts via ToUnicode/Widths, CTM/Tm positions;
  no OCR, so a scanned image just returns "enter by hand"), `parse_lab_report` picks the header (lab, report number, date, sample ID) and the
  rows of the "Analysis ... Result" table; names are matched to specs through `LAB_ANALYTE_ALIASES`, unknown rows become additional analyses.
  Nothing is saved by the scan; the user reviews the form. Tested on FoodAssure and SGS reports -- add aliases there for new analytes/labs. Lab results are exempt from the amend lock (`lab-results` in `_LOG_EXEMPT_SUBPATHS`) and are not part of the release hash. The CoA
  PDF is generated on demand (preliminary until the run is released) from the CURRENT specs.
- **Packaging edits after finalize** re-derive everything computed from the packaging rows in the
  same transaction (`_sync_completed_packaging`): container + FG-label stock (net-change commit),
  the run's FG lots (`_adjust_fg_lot`, refuses to go below units already shipped/moved), and
  `output_litres` / `ibc_used`. A draft's rows still only count once committed. Principle: a
  derived value (FG lot, stock, output) must be recomputed from the source rows by the code that
  changes them, never edited separately. Legacy runs without commit rows get a no-movement baseline
  first (`_ensure_packaging_baseline`).
- **Amend run** (production-log changes after finalize). A completed run's production log is
  LOCKED: `_amend_guard` (called in `_route` for every `/api/production/<id>/...` write) returns
  409 `code: amendment_required` unless the run has an open amendment. Exempt: `/attachments`
  (documents), `/amendments`, and `edit_run`'s yield-analysis exclusion flag; label printing is
  client-side. Only users with `users.can_amend_log` ("Production Log Amender", a checkbox in Admin > Users
  next to Production/Quality Manager; admin role alone grants nothing; existing managers were
  backfilled) can open, edit under, or submit an amendment. `run_amendments` + routes in
  `route_amendments`: open (reason + category; a run that was reviewed/released also needs the
  amender's password as a signature) -> run is `amending`,
  review hash cleared, on_hand lots held -> edit -> submit (ONE revision = diff of the log vs
  `start_snapshot`, reason/category stored; refuses to leave a previously complete run incomplete;
  run -> `pending_review`) or cancel (only if nothing changed; restores prior state/lots).
  Events (`amendment_opened/submitted/cancelled`) go in the hash-chained `release_events`.
  The card's small bar-chart icon (`analysisInfoButton`, hover explains, click opens the "Analysis" window, `editRun` in app.js) holds only the Yield & Usage exclusion flag; the
  card's "Amend run" button opens amendments; run date / location / operators / notes are edited in the Process log's
  Initiation section (under an amendment).
  Documents are excluded from the release snapshot/hash (`_RELEASE_HASH_SKIP`) so they never
  create revisions or void sign-offs. If you change what the snapshot contains, bump
  `RELEASE_SNAPSHOT_VERSION`: boot re-hashes reviewed/released runs (SYSTEM `rebaseline` event) so a
  format change is never mistaken for a tampered log.
- **Revision tracker**: `run_revisions` (append-only). Rev 1 = finalize; each submitted amendment
  adds a revision with the reason, category and field-level old -> new changes. Runs finalized
  earlier show a synthetic Rev 1. `progress`/`revisions`/`revision`/`amendment` are excluded from
  the release snapshot hash.
- **Data integrity check** (`_integrity_check`, `GET /api/integrity`; admin or Quality Manager;
  UI: Admin and Product Release pages): per finalized run, packaging entries vs FG lots + shipped
  + disposed, output litres, container/label stock commits, release status vs lot status, signed
  log hash vs current log, open amendments, required fields (info), and the audit chain. Safe
  derived-data repairs (`POST /api/integrity/repair`: resync_lots, recompute_output,
  recommit_stock, fix_lot_status) need the actor's password and are logged to `release_events`.
  Legacy runs without packaging entries can't be cross-checked and are reported as info only.
- **Env vars:** `PORT` (8002), `KELP_ERP_DB`, `KELP_ERP_UPLOADS`,
  `KELP_ERP_SECRET`, `KELP_ERP_ADMIN_EMAIL/PASSWORD`, `KELP_ERP_INITIAL_PASSWORD`.

## Frontend conventions (`public/app.js`)

- Tiny DOM helper `el(tag, attrs, ...children)`; `table(headers, rows, numCols,
  rowClick?)`; `modal(title, body, onSubmit, submitLabel, opts?)`
  (**backdrop click does not close** — only Cancel/submit). `api(method, path,
  body)` wraps fetch with the bearer token.
- Pages are functions (`pageDashboard`, `pageProduction`, …) selected by
  `State.tab` in `render()`. Add a tab: a button in the right colour group of the
  `<nav id="tabs">` in `index.html` (groups: Overview, Inventory, Process, Quality,
  Fulfilment, Insights, Admin -- each `.tab-group.g-*` has its own colour in
  `styles.css`), an entry in the `render()` map, and a `pageX(v)` function.
  There is no Labels tab: labels print from each row's Label button (`printLabels`).

## Domain model (key tables)

`species`, `sites`, `tote_lots` (stabilized totes; status in_stock/wip/hold/consumed/
disposed; `grind` Coarse|Fine), `preproc_batches` / `preproc_inputs` / `preproc_packaging`, `samples` / `sample_events` / `sample_cart` / `labs` / `lab_analyses` / `lab_requisitions`, `production_runs` + `run_inputs`, `fg_lots`, `consumables` +
`consumable_txns`, `run_reagent_commits`, `release_events`, `cip_events` / `cip_event_chemicals`,
`coa_specs` / `lab_results`, `customers` / `shipments` /
`shipment_lines`, `disposals`, `run_attachments`, `run_edits`, `location_moves`,
`tote_ph_log`, `users`.

**IBC lifecycle** (easy to get wrong): harvest check-in consumes empty IBCs from
a chosen source; a production run **frees** each processed tote's IBC into the
*Empty Used IBC* pool (+1/tote) and **fills** finished-goods IBC packages from
*Empty New IBC* (−1/package).

**Lot numbers:** tote `SITE-SPECIES-YYYYMMDD-NNN` (e.g. `JAM-SL-20260504-003`);
processing lot `PR-YYYYMMDD-NNN`; FG lot `<processing-lot>-<pack>`.

## Deploy (Render)

Docker web service + a 1 GB persistent disk at `/var/data` holding **both**
`kelp_erp.db` and the `uploads/` folder (`render.yaml` wires the env vars).
Migrations run on boot, so pushing to `main` auto-deploys safely. Admin + the
initial staff roster are created on first boot via `ensure_users()`.
