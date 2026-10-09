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
→ http://localhost:8002. `run.bat` sets `KELP_ERP_ENV=development`, which gives a NEW database the seed admin **admin@kelp.local / kelp1234** and pre-fills the login form; in any other
environment the first-run admin password is generated and printed once, and the staff roster accounts get an unusable password until an admin resets it (Admin > Users).
No build step, no install. First run creates + seeds `kelp_erp.db` from `seed.json`.

## Tests

```bash
python -m pip install -r requirements-dev.txt    # pytest -- dev/CI only; the shipped app stays standard-library only
python -m pytest
```
`tests/harness.py` (stdlib only) boots the REAL server as a subprocess on a free port with its own temporary SQLite database + uploads, seeded like a
first deploy (`Server`; `restart()` re-boots on the same data; `Server(db_path=...)` boots on a COPY of an existing database), plus a thin urllib
`ApiClient` (bearer-token `login()`, `admin_client()`, `make_user()`). `tests/conftest.py` turns those into pytest fixtures (`server`, `fresh_server`,
`anon`, `admin`, `make_user`). Never point a test at `kelp_erp.db`. CI (`.github/workflows/tests.yml`, one job named `test`) runs the suite on every pull request in three named steps:
a fresh database boots and restarts (`tests/test_fresh_db.py`), **migrations upgrade older databases** (`tests/test_migrations.py`: for each commit listed in
`tests/legacy_commits.txt` it checks that old version out, boots it on an empty database, then boots the CURRENT server on a copy and checks it starts, restarts,
keeps every row and key, and adds no foreign-key damage -- after each release add the commit that is now live to that file), and everything else. CI fetches full
git history for that; locally a commit missing from the clone is skipped, in CI it fails. Make `test` a required status check in the branch protection rule
for `main` so a red run blocks the merge.

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
- **Release-integrity rules** (risk review batch 1; tests in `tests/test_release_integrity.py`). *Lab results*: only a Quality Manager can add / void / scan them
  (everyone can read); after release, a voided required result or a new failure that the release did not accept puts the run's `on_hand` lots on HOLD and logs
  `lab_result_hold` (`_lab_regate`). *Finished-goods lots* (`PUT /api/fg/:id`, `_edit_fg_lot`): status is limited to on_hand / hold / sold and a lot that is
  pending_release or disposed cannot be changed by hand; hold / un-hold needs a Quality Manager, units / TDS / on_hand<->sold a Production or Quality Manager;
  each change needs a reason and is written to the hash chain (`fg_lot_edited`); location is open to all and goes in the move log. *Totes*: a tote joins a run
  only if `in_stock`, already locked to THAT run, or rejected from it (`_tote_available_for_run`); `PUT /api/totes/:id` only moves in_stock <-> hold and never
  changes the weight of a wip/consumed tote. *Shipments*: lines for the same lot are summed before checking stock, deduction is relative and conditional
  (`qty=qty-? WHERE qty>=?`), a reinstated shipment needs the stock, units returned to a disposed lot go on hold. *Run edits under an amendment* need
  `can_amend_log`. *Writes*: every non-GET request (except login) starts `BEGIN IMMEDIATE` and `db()` waits up to 30 s for the write lock, so read-check-write
  sequences cannot interleave. Don't add a mutating endpoint that skips these rules; add a test to that file.
- **Secrets and sessions** (risk review batch 2; tests in `tests/test_secrets_sessions.py`). Every authenticated route, including the `?token=` downloads, goes through
  `_user_from_token`: valid signature, the user exists and is active, and the token's `tv` equals `users.token_version` (bumped by a password change, an admin reset and a
  deactivation, so those end the user's other sessions; `POST /api/me/password` returns a fresh token for the current one). A user with `must_change_password` gets 403
  `password_change_required` from everything except `/api/me` and `/api/me/password` (the SPA asks for the new password before loading). Sign-in is throttled in memory
  (`login_wait_seconds`: 8 failures per account+address, 40 per address, per 5 minutes -> 429) and an unknown email still pays for a password check (`dummy_verify`).
  The address is the LAST `X-Forwarded-For` entry on Render only. `?token=` is still a full-scope bearer token (short-lived download tokens are a later item).
- **Uploads, downloads and browser headers** (risk review batch 3; tests in `tests/test_uploads_xss.py`). What a stored file is served as comes from its FILE NAME only
  (`served_type`): PDFs and images may open inline, everything else is a download with a server-chosen type (`SAFE_INLINE_TYPES` / `DOWNLOAD_TYPES`); the type a client
  sends is ignored, including for rows stored before this rule. Script-capable extensions (`BLOCKED_UPLOAD_EXTENSIONS`: html, svg, js, xml, exe ...) are refused at upload
  (`check_upload_filename`). `content_disposition()` builds every Content-Disposition (ASCII fallback + RFC 5987 `filename*`, no header injection). Uploads go through
  `_check_storage_room` (all documents together <= `KELP_ERP_MAX_UPLOADS_MB`, default 600, and the disk keeps 100 MB free -> 507) and `_write_upload`, which remembers the file so
  it is deleted if the request fails; files of rows removed by a draft discard / tote delete are deleted after the commit (`_files_to_remove`). Every response gets nosniff,
  X-Frame-Options DENY, Referrer-Policy and (over https) HSTS from `end_headers`; CORS headers are gone (the app is same-origin); the app page carries `APP_CSP`
  (`script-src 'self'`: NO inline script, so print windows are printed by the opener via `printWhenReady`, and every value written into a print window goes through
  `escHtml`). Don't add an inline `<script>`, an inline event attribute or an un-escaped template string.
- **Request limits, bounded parsing and logging** (risk review batch 4; tests in `tests/test_limits_logging.py`). `_body_json(limit)` refuses a body over
  `MAX_REQUEST_BYTES` (40 MB; the login, the only open route, 8 KB) with 413 BEFORE reading it, and `Handler.timeout` (`KELP_ERP_SOCKET_TIMEOUT`, 30 s) drops a client that goes silent.
  Untrusted .docx / .xlsx / .pdf are unpacked only through `zip_read` (parts over `MAX_ZIP_MEMBER_BYTES`, 10 MB, are refused) and `_inflate` (PDF streams capped); spreadsheet previews
  show at most `MAX_PREVIEW_ROWS` x `MAX_PREVIEW_COLS` and every preview page is capped (`cap_html`); a restore's zip members may not inflate past their declared size (`_copy_exact`).
  Logging goes to stderr through the `kelpworks` logger (`configure_logging`): one line per API request (`log_request`; **tokens in `?token=` are always redacted** -- use `redact()` for
  anything you log from a URL), a boot line with the database size and row counts, and every unexpected error through `_server_error`: the traceback is logged under a short
  reference and the user sees only the reference ("Something went wrong ... reference ab12cd34"); a locked database is a 503 "busy". A failed start logs why. Never put
  `"Server error: %s" % e` in a response; call `self._server_error(e)`.
- **Production-log required fields** are defined once, server-side, in `PROGRESS_SECTIONS` /
  `REQUIRED_FEEDSTOCK` (`kelp_erp_server.py`; exposed to the SPA as `refdata.requiredFields`).
  Everything is required except: every Notes field, the Homogenization / Separation /
  Pasteurization sample-point boxes, Extraction's QC Check Total-solids + Density fields,
  checkboxes, calculated values and legacy dilution-tank rows. `_run_progress` returns
  per-section `{total, filled, missing, done}` (attached to drafts and `_run_full`; also
  `GET /api/production/<id>/progress`) -- the progress chips are green only when a section is
  `done`. `_finalize_run` ends with `_required_problems`; raising there rolls the whole
  finalize back. The SPA marks required labels with `rfield()/reqLabel()` (red `*`), and
  `finalizeRun` saves the header + presses every `button.section-save` before finalizing (`pressSectionSaves`); the draft's **Save & close** does the same (a section that can't be saved keeps the window open with its message), and so does Save & close on the Process log of a run under amendment. When
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
- **Samples (analysis catalogue, retention inventory, lab cart, requisitions)** (the "Samples" tab; the **Analysis catalogue** is `GET /api/samples?retention=0`
  = every sample EXCEPT description `Retention`, with a shared "Group by" + collected-date "Sort" bar (`sampleViewBar` / `sampleTable` / `SAMPLE_DIMS`; catalogue groups by process point / type / description / container, retention by process point / type / container / run; sort also from the Collected heading; per-view state in `State.sampleViews`);
  Retention samples appear only in the Retention inventory (`?retention=1`); `pageSamples`; backend `route_samples` /
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
  Cart UI: each sample row has a lab picker, that lab's analysis checkboxes and a Ready / Needs analyses / Needs a lab badge; below it a card per
  lab + run ("Requisitions ready") with a one-click "Create requisition & download" (`createReqs` -> `POST /api/cart/requisitions {labId, runId}`;
  a single requisition downloads its .docx straight away). A lab's form with no `{{placeholders}}` comes back unchanged, so `_lab_public` reports
  `templateTokens` and the cart / Admin warn; `docs/requisition-templates/*` are installable per lab with one click (`POST /api/labs/<id>/template/builtin`,
  matched on the lab name by `_ready_template_path`). A missing template file on disk now fails the requisition (409) instead of silently using the built-in
  layout. **PO / reference #** is per requisition (one per lab + run card in the cart; `poNumbers` keyed "run:lab" in the preview / create body, `_requisition_po`): it defaults to the production run
  number and the user can change or clear it. **Customer contact on requisitions**: `{{customer_phone}}` + `{{customer_email_1..5}}` come from the `requisition_contact` table (seeded with the phone / two emails that were typed into the
  FoodAssure form; Admin > "Requisition contact details", `GET/PUT /api/requisition-contact`), overridable per requisition from the cart (`contact` in the preview / create body). **Previews**: `docx_to_html` / `xlsx_to_html` (stdlib `zipfile` + ElementTree; content-faithful, not layout-faithful) render a Word / Excel
  document as escaped HTML shown in a sandboxed iframe (`previewBody`). `POST /api/cart/requisitions/preview` builds the filled form + sample list for a lab+run
  WITHOUT writing anything (same `_requisition_build` as creation, placeholder req number; the cart card's "Preview" button, which can then create it);
  `GET /api/production/<id>/attachments/<aid>/preview` previews any stored .docx/.xlsx (links in the creation window, Samples > Requisitions, run Documents).
  Admin > Labs & analyses > Analyses has "+ From CoA tests" to add a lab's analyses from the Certificate of Analysis specifications.
  **Mineral scans (SGS).** `lab_analyses` has `kind` (`analysis` | `mineral` | `scan`), `category` (a group shown in the cart picker, `analysisPicker`, with a select-all per group), `symbol` and `capacity`.
  `ensure_sgs_analyses` (once per SGS lab, flagged in `app_flags`) recategorizes the 11 CoA minerals as the **CFIA Heavy Metals** group and adds the Other minerals (Ca, K, Na, P, Mg, Fe, S),
  Total Nitrogen / Moisture - Vacuum Oven / Proximate Analysis, and the two scans (`Mineral Scan Up to 12` / `Up to 20`). Scans are never ticked by hand (server drops/rejects scan ids): `_requisition_build`
  counts the ticked minerals and adds the smallest active scan whose `capacity` covers them (400 if more than the largest), puts the scan + direct analyses in `{{sample.analyses}}`, and always spells out every
  mineral in `{{notes}}` (`mineral_notes`, user notes appended). `sample.minerals` / `sample.scan` tokens and a "Minerals requested" sheet column are also available. Cart saves are queued per sample (`assignQueue`) so rapid ticks land in order.
  Static files are served `Cache-Control: no-cache` so a deploy never leaves a stale `app.js`.
  **Requisition Notes = one request per entry** (`requisition_notes`, `\n` becomes a Word line break): the mineral line(s), then each requested analysis's standing note (`lab_analyses.req_note`, edited in
  Admin > Analyses; seeded once per SGS lab by `ensure_sgs_analyses`: Total Nitrogen -> "Total Nitrogen expressed in percent", Proximate Analysis -> "Ash, Crude_protein, Crude_fat (Crude_lipid), Crude_Fiber";
  each line appears ONCE however many samples request it, never names Sample IDs, and the analysis it applies to is bold -- the filler supports `**bold**` markup in any resolved text (`_bold_runs`; everything else in the
  notes is regular), and a note not starting with its analysis name gets "**Name:** " put in front), then whatever was typed in the cart (duplicates dropped). The SGS form's Sample ID cell shows ID Simplified only (no
  product/lot/process-point line), and its two TAT check boxes (Standard ticked by default, Rush clear) each have their own Word edit range (`permStart` 9101 / 9102) so both can be changed in the protected form. **Contacts** (`requisition_contact`, ONE contact): `submit_name/phone/email` ("Samples submitted by"; the name and phone also fill "Send analysis results to", `{{customer_phone}}` and the signature) and `email_1..5` (the results emails: `{{results_emails}}` non-blank ones one per line, `{{results_email_1..5}}`, and FoodAssure's `{{customer_email_1..5}}`). Tokens `{{submitter_*}}`, `{{results_name}}`, `{{results_phone}}`. The SGS form's
  Client's Authorized Signature is `{{submitter_name}}`. Same fields in Admin (defaults) and the cart (this requisition only) via `contactFields`; a contact payload part that is left out keeps its stored value (`requisition_contact_clean(d, base)`).
  The SGS form's Analysis Requested cell uses `{{sample.analyses_lines}}` (one test per line, blank line between) and Specifications / Methods uses `{{sample.methods_lines}}`: each test's method on the SAME line as the test, blank when it has none. Both columns give every test one line plus a blank spacer line, so they only stay in step while NO test name wraps: the template's Analysis Requested column is 2523 twips wide (Sample ID 1260; the old 1623 wrapped "Mineral Scan Up to 12" and broke the alignment -- an installed lab template must be refreshed with "Use the ready-made KelpWorks form"). Verified by rendering the filled .docx through Word (COM `ExportAsFixedFormat`) and reading the PDF with `pdf_text_lines`.
  The sample sheet's description column is `sheet_description` (lot + process point, no product SKU), Collected is the date only, and Container Qty is a whole number.
  **List each Sample ID once** (`labs.merge_ids`, a checkbox in the lab's edit window; switched on once for SGS by `ensure_sgs_analyses`): `consolidate_sample_rows(rows, merge)` turns samples sharing an ID Simplified into ONE line --
  on the Word form, the sample sheet and the notes -- with Container Qty and Total sample volume added up, the tests requested combined (the mineral scan is re-sized for the combined minerals) and no ID-conflict warning;
  every sample is still marked submitted. Labs without the flag keep one form row per sample and the old sheet rule (same ID + description + tests).
  Notes entries are separated by a blank line and start on the line below the label (`{{br}}` token in the template).
- **Sample IDs and labels.** Every sample has a UNIQUE ID (`samples.sample_code`, e.g. `PR-20261006-053-HOM-01`, never changes) plus two label IDs:
  **Sample ID Detailed** (`id_detailed`) = the first line of the Detailed label = the processing lot, and **Sample ID Simplified** (`id_simplified`) = the first line of
  the Simplified label = `Lot-` + the run's last 3 digits (`lot_simplified`), both with `-<unit no>` when the point's labels are numbered. They are derived, never typed:
  `refresh_sample_ids` sets them from the sample's `run_sample_points` row (`label_type` detailed|simplified, `label_numbered`, `label_name`) after every `sync_samples`, at boot
  and when label settings are saved -- so the defaults at finalize are the un-numbered lot / `Lot-053`, and a user's label changes flow into every table (Analysis catalogue,
  Retention inventory, cart, requisition detail, requisition docx/xlsx). The lab REQUISITION always uses **ID Simplified** as its Sample ID (`{{sample.id}}` / the "Sample ID" column, cart and requisition detail; `{{sample.id_detailed}}`, `{{sample.id_simplified}}`,
  `{{sample.code}}`, `{{sample.container_qty}}`, `{{sample.volume_text}}` are also available per sample). The sample-list spreadsheet (`build_sample_sheet_xlsx`) CONSOLIDATES samples sharing a Sample ID
  (same description + tests; `consolidate_sample_rows`) into one line with **Container Qty** and **Total sample volume** (`container_volume`: the sample container's `litres_each` as mL, else the amount in its
  name such as "100 g"; summed per unit); the same ID on a different process point / tests stays separate lines and the preview warns (`requisition_id_conflicts`). The Word form rows stay one per sample.
  **Locked once on a requisition:** `refresh_sample_ids` skips any sample with a `requisition_id` (its IDs and label type never change again); the label window still saves/prints from the production log -- that only affects labels printed and samples not yet sent (a 🔒 count shows on the point's row, `idsLocked` in the sample API). (`short_id` is a retired column.)
- **Sample labels** ("Print sample labels" next to "Print FG labels" on a finalized run card, and on in-progress cards; `openSampleLabels` / `printSampleLabels`, data from
  `GET /api/production/<id>/sample-labels` = the run's sample POINTS with their label settings; settings saved by `PUT /api/production/<id>/sample-label-settings`, exempt
  from the amend lock). ONE TABLE ROW PER SAMPLE POINT: tick, process point, editable "Name on label", Label type (Detailed / Simplified), "Number" (count the point's labels
  from 1), description, collected, "Labels" count (default = the point's sample count; more repeats the numbers). 63.5 x 25.4 mm, 3 lines, no barcode/logo: Detailed = run lot /
  `<name> - <description>` / date AND time; Simplified = `Lot-053` / same line 2 / date only. "Print labels" (or "Save labels") stores the settings on the points, "Save these names
  as the default" stores stage-level names (`sample_label_names`, `PUT /api/sample-label-names`; built-in `SAMPLE_LABEL_DEFAULT_NAMES`: Packaging = "Finished Product"). The
  Sample Point rows in the production log have no print button.
- **Production Log Summary PDF** (`GET /api/production/<id>/summary.pdf[?dl=1&token=]`, completed runs only; the "Production log summary" card at
  the top of a run's Documents window). Generated on demand from the current log by `_run_summary_data` -> `build_run_summary_pdf` -- a
  stdlib-only PDF writer (`PdfBuilder`: standard Helvetica fonts via a built-in width table, WinAnsi/cp1252 text -- `≤`/`≥` are real glyphs via a Differences encoding on the spare codes `¤`/`¥` (`_PDF_SUBS`) --, tables, KPI cards, the logo
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
  Packaging QC check), `value` (compared as reported; unit forced to the spec's -- microbial is per g), `absent` (Salmonella), `metal` (the spec
  limit is in kg metal/ha in `max_val`; the lab result stays in the unit reported (ppm or %) and the CoA adds a Loading column = mg/kg x
  `coa_application_rate_kg_ha` x `coa_application_periods` / 1e6, compared with the limit; both are admin-only constants on the Calculations page,
  rate 0 = listed, not judged; a `<` result whose detection-limit loading exceeds the limit is REVIEW, not pass; `limit_kg_ha` is a legacy unused column).
  `coa_specs.active` = "Listed on the certificate": an unlisted test is still entered, judged and shown in the Lab results window (marked
  "not on certificate") but left off the CoA PDF; `required` is independent of it.
  `required` specs (the five microbial tests) BLOCK the Quality release (`_release_action`) until a result is on file; a FAILED result needs a
  release comment (400 without one; the comment prints on the CoA and the out-of-spec tests go in the `released` event detail).
  Microbial results are per gram of LIQUID product as reported (no TDS/solids conversion).
  **Report scan** (`POST /api/production/<id>/lab-results/scan {attachmentId}`): "Add lab report" takes an uploaded (or already attached) PDF and
  pre-fills the form -- `pdf_text_lines` is a stdlib PDF text reader (Flate streams, simple + Type0 fonts via ToUnicode/Widths, CTM/Tm positions;
  no OCR, so a scanned image just returns "enter by hand"), `parse_lab_report` picks the header (lab, report number, date, sample ID) and the
  rows of the "Analysis ... Result" table; names are matched to specs through `LAB_ANALYTE_ALIASES`, unknown rows become additional analyses.
  Nothing is saved by the scan; the user reviews the form. Tested on FoodAssure and SGS reports -- add aliases there for new analytes/labs. Lab results are exempt from the amend lock (`lab-results` in `_LOG_EXEMPT_SUBPATHS`) and are not part of the release hash. The CoA
  PDF is generated on demand (preliminary until the run is released) from the CURRENT specs.
- **Quality Control charts** (the "Quality Control" tab, `pageQC` + `qc*` helpers in app.js; `GET /api/qc-charts` = `_qc_chart_data`, `PUT /api/qc-charts/limits`, tables
  `qc_chart_limits` + append-only `qc_chart_limit_log`). One measurement at a time from a dropdown: process QC checks (`QC_FIELD_REGISTRY` columns grouped by measurement x process section via
  `QC_MEASURE_KEYS` / `qc_section_of`, plus dilution pH / volume variance and the derived extraction efficiency), feedstock pH / ORP per tote, and numeric laboratory results (`lab_results`; metals
  converted to ppm; `<` results are charted hollow and left out of the statistics). Completed runs only, `exclude_from_stats` runs hidden unless ticked. Everything is computed client-side from the
  one payload: summary tiles, an individuals chart over the run sequence (x = runs oldest -> newest, colour by species / farm / SKU / stabilization / month, filters for date / SKU / species / farm /
  stabilization), a moving-range chart, a process profile (the same measurement across sections, one line per run = within-run), and group comparisons (species / farm / SKU / stabilization / month
  strip plots + tables) plus a within-run vs between-run SD for measurements with several results per run. Control limits are set BY HAND per measurement + section (admin or Quality Manager; logged);
  recommended limits (mean +/- k sigma, sigma = MRbar/1.128) appear once `qc_chart_min_n_provisional` results exist (established at `qc_chart_min_n_established`) -- constants are admin-editable settings and
  documented on the Calculations page. Signals (beyond a limit, run of N on one side, trend of N) are only raised once limits are set. Specification lines come from `coa_specs` (shown dotted, separate from
  control limits; far-off lines are noted instead of drawn). Charts are hand-written SVG (no library).
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
  `KELP_ERP_SECRET` (when unset or equal to the old public default, a random key is generated once into `kelp_secret.key` next to the database), `KELP_ERP_ADMIN_EMAIL/PASSWORD`,
  `KELP_ERP_INITIAL_PASSWORD`, `KELP_ERP_ENV=development` (convenience defaults), `KELP_ERP_TRUST_PROXY=1` (trust the last `X-Forwarded-For` entry; always on Render); staging only: `KELP_ERP_ENV=staging`, `KELP_ERP_ALLOW_RESTORE=1`,
  `KELP_ERP_STAGING_PASSWORD`, `KELP_ERP_MAX_RESTORE_MB`. **Never set the staging ones on the live service.**
- **Staging** (full guide: `docs/staging.md`, blueprint: `render.staging.yaml`): a second Render service with its own disk that holds a copy of live data.
  `GET /api/admin/backup?full=1` (admin; `build_full_backup`) = one .zip with a consistent DB snapshot + `uploads/`, `lab_templates/`, `sop_documents/` + `manifest.json`
  (the plain `/api/admin/backup` is still the DB-only .db). `POST /api/admin/restore` (raw zip body; `restore_backup`) exists ONLY when `ENV_NAME == "staging"` and
  `ALLOW_RESTORE` (a live server answers 403): it validates everything first (manifest, safe member names, SQLite integrity), replaces the DB via sqlite's backup API and
  the three folders, re-runs `init_db()` (migrations), then `scrub_for_staging` resets EVERY user's password to `KELP_ERP_STAGING_PASSWORD`. `GET /api/env` (public) drives the
  amber STAGING banner. Admin UI: "Download full backup" everywhere, the restore box only on staging. Covered by `tests/test_staging.py`.

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

**Process: read `docs/release-guide.md`.** Work on a branch (fixes: `hardening/<name>`), never push to `main`: open a pull request, wait for the green `test`
check, and the user merges it (Render then auto-deploys live). Staging (a copy of live data, manual deploys) is in `docs/staging.md`. Keep the release guide in
step with any change to this process, and add the live commit to `tests/legacy_commits.txt` after each release.
