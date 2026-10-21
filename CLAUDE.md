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
  **Products without labels** (`fg_skus.uses_labels = 0`: Sugar Kelp Extract `SACC-LKE` and Macrocystis Kelp Extract `MACRO-LKE`, both ordinary active products listed in the SKU table of `migrate()`): no label items are
  created at start, `_commit_label_stock` / `_ensure_packaging_baseline` deduct nothing for them, a label item cannot be added for them (400) and the "Add finished-good label" picker leaves them out; their old label items are
  removed once (`run_once("remove_labels_of_label_free_skus")`, keeping any item that has stock history). The SKU list in `migrate()` is reference data re-applied at every start (name, targets, `active`, `uses_labels`):
  change a product there, not in the database. Tests: `tests/test_sku_labels.py`.
- **Stock never blocks a run.** Every consumable deduction in a production run (reagents, sample containers, packaging, FG labels,
  and Pre-Processing pack-out via `_adjust_container_stock`) just lets `on_hand` go negative (the item shows LOW); nothing raises "Not
  enough ... on hand". **Sample Point defaults** (`_sample_point_defaults`, applied when the description/type is changed, never over a
  value sent in the same request, new rows start Slurry/Microbial): Microbial -> qty 1 + 50 mL falcon tube; Metals & Nutrients -> qty 2 +
  50 mL falcon tube; type Solid -> 100 g sample bag.
  **Required samples (KW-13; tests in `tests/test_required_samples.py`)**: the four default LKE characterization samples (`Handler.DEFAULT_LKE_SAMPLES`, seeded by `_seed_default_samples`) are locked rows
  (`run_sample_points.min_qty` = the default quantity; NULL = an ordinary row): `DELETE` answers 409, a changed type / description or a quantity below `min_qty` is 400 (a bigger quantity and another container are fine),
  and the UI shows them as "required" with the selects locked. This holds under an amendment too; optional samples can still be added, changed and removed. `_sample_points_public` adds `required` / `minQty`
  ONLY to locked rows, so no earlier run's signed log changes.
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
  review (-> `pending_release`, awaiting QA; **Production Manager only**), then a Quality Manager signs release (lots ->
  `on_hand`) or rejects (lots -> `hold`); one person holding both flags may do both unless the setting `release_separate_signers` is 1
  (and `review_not_by_finalizer` = 1 bars the finalizer from reviewing their own run; both default 0, both 403 `same_person`). Endpoints under
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
  pending_release or disposed cannot be changed by hand; hold / un-hold needs a Quality Manager, units / on_hand<->sold a Production or Quality Manager (TDS and pH are not editable here, see KW-31 below);
  each change needs a reason and is written to the hash chain (`fg_lot_edited`); location is open to all and goes in the move log. *Totes*: a tote joins a run
  only if `in_stock`, already locked to THAT run, or rejected from it (`_tote_available_for_run`); `PUT /api/totes/:id` only moves in_stock <-> hold and never
  changes the weight of a wip/consumed tote. *Shipments*: lines for the same lot are summed before checking stock, deduction is relative and conditional
  (`qty=qty-? WHERE qty>=?`), a reinstated shipment needs the stock, units returned to a disposed lot go on hold. *Run edits under an amendment* need
  `can_amend_log`. *Writes*: every non-GET request (except login) starts `BEGIN IMMEDIATE` and `db()` waits up to 30 s for the write lock, so read-check-write
  sequences cannot interleave. Don't add a mutating endpoint that skips these rules; add a test to that file.
- **Release gate** (risk review batch 6; tests in `tests/test_release_gate.py`). `coa_specs.release_rule` is `''` (not a release condition), `pass` (a result is on file and passes the specification; a heavy metal that cannot
  be judged -- application rate not set, unit unknown -- does not pass) or `min` (a result is on file and is at or above the lower limit; the upper limit is not a release condition, a `<` result does not prove it).
  Defaults (set once by `run_once("release_gate_rules_batch6")`): heavy metals + microbial = `pass`, TDS = `min`; pH / potash = none. Admin > Certificate of Analysis specifications edits the rule (the old
  "Required" checkbox is gone; `required` stays as the older "must be on file" flag). `release_gate(conn, run)` judges every ruled spec; `_release_action` refuses release with 409 `release_gate` listing what is not met --
  **no override, a comment does not help**. Tests without a rule that fail still need a release comment (pH). The result is stored in the `released` event (`detail.gate`); after release `_lab_regate` holds a run's lots when a
  test in that snapshot stops passing (void or a new failing result) -- older releases without a snapshot keep the old rule. **Lab results**: a failed result must be VOIDED (reason) before another is entered for that test
  (409 `void_first`); a test with a release rule needs the lab report attached (`lab_result_require_report`, 400 `report_required`). The hash-mismatch void at release is committed before the error is raised.
  **Change control**: spec edits and setting edits write `config_log` (`GET /api/coa-specs/<code>/log`, `GET /api/settings/log`, admin); password resets, admin-role and active changes write `user_permission_log`.
  **Recall**: `GET /api/release/runs/<id>/recall` (`_recall_report`: each FG lot -> shipments -> customers; Product Release "Where did this product go?"). Heavy metals are judged only when `coa_application_rate_kg_ha` > 0 on the Calculations page.
- **Secrets and sessions** (risk review batch 2; tests in `tests/test_secrets_sessions.py`). Every authenticated route, including the `?token=` downloads, goes through
  `_user_from_token`: valid signature, the user exists and is active, and the token's `tv` equals `users.token_version` (bumped by a password change, an admin reset and a
  deactivation, so those end the user's other sessions; `POST /api/me/password` returns a fresh token for the current one). A user with `must_change_password` gets 403
  `password_change_required` from everything except `/api/me` and `/api/me/password` (the SPA asks for the new password before loading). Sign-in is throttled in memory
  (`login_wait_seconds`: 8 failures per account+address, 40 per address, per 5 minutes -> 429) and an unknown email still pays for a password check (`dummy_verify`).
  The address is the LAST `X-Forwarded-For` entry on Render only. **Download links** (`?token=`) carry a SHORT-LIVED download token, never the session token: `POST /api/auth/download-token`
  returns a `scope: 'dl'` token (`make_token(..., scope="dl", ttl=DOWNLOAD_TOKEN_TTL)`, 15 minutes); `_token_user` accepts a session token only in the Authorization header and only a `dl` token in `?token=`,
  `_auth` / `_user_from_token` refuse a `dl` token as a Bearer token, and a password change / reset / deactivation ends it like any session. The SPA keeps one fresh (`dlToken()`, refreshed every 5 minutes
  and on return to the tab; a capture-phase click handler swaps the current token into any `a[href*="token="]` at click time), so build every file URL with `encodeURIComponent(dlToken())`, never `State.token`.
  Tests that follow a link must send NO Authorization header (`client.get(path, token="")`); `ApiClient.download_token()` mints one.
- **Idle timeout** (KW-08; tests in `tests/test_idle_timeout.py`). Setting `idle_timeout_minutes` (default 30, 0 = off, on the Calculations page, effective within a minute, no deploy): the browser (`Idle`, app.js) watches input,
  shows a one-minute "Still there?" countdown (any input cancels it), then presses every open `button.section-save` (`pressSectionSaves`: run drafts, amended runs, pre-processing batches -- the same as "Save & close"), closes the
  windows and signs out with a message listing what was saved. The server enforces the limit too (`idle_check` in `_user_from_token`, session tokens only; grace = a fifth of the limit, 2 s - 120 s, so the browser can save first):
  an idle session answers 401 `idle_timeout`, so a dead tablet cannot hold a session for the 12 hours of the token. Activity = any request except `/api/auth/download-token` (the background refresh), plus `POST /api/activity`
  (a ping every ~30 s while the person is using the screen; no write transaction, 200s not logged). In memory (`IDLE_LAST`): a restart restarts every idle clock. Windows that are not section-based (small edit forms) cannot be auto-saved.
- **Feedstock photos: view, replace, remove** (KW-02; tests in `tests/test_photo_remove.py`). A photo taken for a feedstock characterization card is a `run_attachments` row with `purpose = 'feedstock_photo'` (set by the card's
  upload; the run's `run_inputs.surface_photo / striation_photo` and the draft's saved `feedstockDetails` point to it). While the run is a DRAFT: the card shows the photo (click = full size in a new tab), "Replace" uploads a new one
  with `replaces=<old id>` (the old row is deleted, its places point to the new photo, the file is deleted after the commit) and "Remove" (`DELETE /api/production/<id>/feedstock-photo/<attachment id>`) clears every reference
  and deletes the file; both write a line to the tote's Feedstock Stability log ("(replaced)" / "(removed)"). Once the run is finalized the photo is part of the signed log and LOCKED (409 `photo_locked` from the Documents delete,
  `amendment_required` from the log lock): the finalized run's card and Feedstock Inventory's card do not pass `removePhoto`. A feedstock photo can no longer be deleted through the Documents list of a finalized run.
- **Uploads, downloads and browser headers** (risk review batch 3; tests in `tests/test_uploads_xss.py`). What a stored file is served as comes from its FILE NAME only
  (`served_type`): PDFs and images may open inline, everything else is a download with a server-chosen type (`SAFE_INLINE_TYPES` / `DOWNLOAD_TYPES`); the type a client
  sends is ignored, including for rows stored before this rule. Script-capable extensions (`BLOCKED_UPLOAD_EXTENSIONS`: html, svg, js, xml, exe ...) are refused at upload
  (`check_upload_filename`). `content_disposition()` builds every Content-Disposition (ASCII fallback + RFC 5987 `filename*`, no header injection). Uploads go through
  `_check_storage_room` (all documents together <= `KELP_ERP_MAX_UPLOADS_MB`, default 600, and the disk keeps 100 MB free -> 507) and `_write_upload`, which remembers the file so
  it is deleted if the request fails; files of rows removed by a draft discard / tote delete are deleted after the commit (`_files_to_remove`). Every response gets nosniff,
  X-Frame-Options DENY, Referrer-Policy and (over https) HSTS from `end_headers`; CORS headers are gone (the app is same-origin); the app page carries `APP_CSP`
  (`script-src 'self'`: NO inline script, so print windows are printed by the opener via `printWhenReady`, and every value written into a print window goes through
  `escHtml`). Don't add an inline `<script>`, an inline event attribute or an un-escaped template string.
  **Photos are shrunk in the browser before upload** (KW-03; `shrinkImage` / `prepareUpload` in app.js, constants `IMAGE_MAX_PX` 1600 and `IMAGE_QUALITY` 0.8): JPEG / PNG / WebP are redrawn at most 1600 px on the long side and
  saved as JPEG (PNG transparency becomes white, the file name gets `.jpg`); HEIC, GIF, PDF, Word etc. and any photo that would not get smaller are sent unchanged, and a failure falls back to the original. Every image upload
  goes through `prepareUpload` (feedstock photo slots, run documents `uploadAtt`, delivery documents `uploadReceiptDocs`); the server is unchanged and still stores whatever arrives, so a new upload path must call it too.
- **Request limits, bounded parsing and logging** (risk review batch 4; tests in `tests/test_limits_logging.py`). `_body_json(limit)` refuses a body over
  `MAX_REQUEST_BYTES` (40 MB; the login, the only open route, 8 KB) with 413 BEFORE reading it, and `Handler.timeout` (`KELP_ERP_SOCKET_TIMEOUT`, 30 s) drops a client that goes silent.
  Untrusted .docx / .xlsx / .pdf are unpacked only through `zip_read` (parts over `MAX_ZIP_MEMBER_BYTES`, 10 MB, are refused) and `_inflate` (PDF streams capped); spreadsheet previews
  show at most `MAX_PREVIEW_ROWS` x `MAX_PREVIEW_COLS` and every preview page is capped (`cap_html`); a restore's zip members may not inflate past their declared size (`_copy_exact`).
  Logging goes to stderr through the `kelpworks` logger (`configure_logging`): one line per API request (`log_request`; **tokens in `?token=` are always redacted** -- use `redact()` for
  anything you log from a URL), a boot line with the database size and row counts, and every unexpected error through `_server_error`: the traceback is logged under a short
  reference and the user sees only the reference ("Something went wrong ... reference ab12cd34"); a locked database is a 503 "busy". A failed start logs why. Never put
  `"Server error: %s" % e` in a response; call `self._server_error(e)`.
- **Migration safety** (risk review batch 5; tests in `tests/test_migration_safety.py`). `init_db()` runs the idempotent `SCHEMA`, then ONE transaction
  (`BEGIN IMMEDIATE` ... commit) holding `migrate()`, `seed()` (no longer commits), the `ensure_*` steps and a second `migrate()` after seeding: a start that fails or is killed
  half-way rolls EVERYTHING back (SQLite DDL is transactional), so the database is exactly as it was and the next start redoes the work. Before new code migrates an EXISTING
  database (the `code_fingerprint` in `app_flags` differs from the sha256 of `kelp_erp_server.py`) `snapshot_before_migrating` writes `backups/pre-migrate-<utc>.db.gz` next to the
  database (last 3 kept; skipped when the disk is too full). **Rules for a migration step:** a data change that must happen once goes through `run_once(conn, "name", fn)` (never an
  unconditional UPDATE: it would overwrite what an admin sets later); a repair or backfill that must not block a start goes through `soft_step(conn, "label", fn)` (SAVEPOINT;
  logged and skipped on failure, retried next start); anything a NEW database needs that `seed()` creates afterwards (reagent types) belongs in an `ensure_*` that runs after `seed()`.
  Never call `conn.commit()` inside `migrate()` / `seed()` / `ensure_*`. `SIGTERM` / `SIGINT` finish the requests in progress (up to 15 s) and exit 0 (`main`, `INFLIGHT`).
  Add the live commit to `tests/legacy_commits.txt` after each release; `test_data_written_by_an_older_release_upgrades_intact` runs an OLD release with real data and upgrades it.
- **Data-integrity rules** (risk review batch 7; tests in `tests/test_integrity_cleanup.py`). A `sqlite3.IntegrityError` (unique number, row still referenced, our own guard triggers) is
  answered 409 `conflict` with a plain message (`integrity_message`), never a 500. `ensure_indexes` (a soft step at start) indexes EVERY foreign-key column that has no index, so a table
  added later is covered automatically (a test fails if one is missed); `ensure_constraints` adds triggers because SQLite cannot add CHECK constraints to an existing table: `STATUS_RULES`
  limit `tote_lots` / `fg_lots` / `production_runs` / `preproc_batches` status to the documented values and `fg_lots.qty` cannot go negative (they only look at what a write CHANGES, so an old odd row never
  blocks an unrelated edit). **Add a new status value to `STATUS_RULES` or the write is refused.** Finalize adds packaging rows up to ONE total per container unit (one FG lot per unit, so the lot number
  cannot collide), and `_run_output` is the single calculation of `output_litres` / `ibc_used` (IBC = unit name contains "IBC"; the litres per unit come from the run's existing FG lots, else the
  container's current size) used by finalize, `_sync_completed_packaging`, the integrity check and its repair. Finalize returns 409 when a tote this draft locked as WIP is missing from the request.
  The accepted / rejected feedstock decision is fixed once the run is finalized (409 `decision_locked`; other characterization details can still be amended). A run's reagent totals belong to the
  Dilution & Preservation entries: `edit_run` no longer accepts them (it moved stock by item name outside `run_reagent_commits`). `num` / `numn` return the default / None for NaN and infinity;
  packaging quantities must be numbers and not negative (400), Sample Point quantities are a whole number 1-10 or blank (400 if not a number). A tote with a `run_inputs` / `preproc_inputs` row
  cannot be deleted (409 `tote_has_history`; use QAQC Hold or dispose). `seed()` raises when `seed.json` cannot be read (the start rolls back) instead of building an empty system.
- **Small hardening batch** (tests in `tests/test_hardening_small.py`; guide `docs/restore-runbook.md`). *Upload quota*: `_check_user_quota` (after `_check_storage_room`, in `_store_attachment` /
  `_store_tote_attachment`) stops one person uploading more than `upload_user_daily_mb` (default 300, 0 = off) in 24 hours across run and tote documents (429 `upload_quota`). *Deleted totes*:
  deleting a tote with no run / pre-processing history first writes an append-only `deleted_records` row (the tote, its stability log and its file list, who and when). *Rebaseline*: the SYSTEM `rebaseline`
  event keeps `previousHash` / `previousVersion`, and booting older code on a newer database logs a warning. *Live restore*: `restore_on_boot` (runs in `main` before `init_db`) restores a full backup .zip when
  `KELP_ERP_RESTORE_FROM=<path>` AND `KELP_ERP_RESTORE_CONFIRM=replace-live-data` are set: it validates everything (`_restore_zip`, the same core as the staging restore), keeps `backups/pre-restore-<utc>.db.gz`,
  does NOT reset passwords, renames the zip to `.restored-<utc>` and a failure stops the start (nothing changed). *Missing disk*: `KELP_ERP_REQUIRE_MOUNT=<path>` makes the server refuse to start when that
  path is not a mounted volume (otherwise a missing Render disk gives a fresh seeded database).
- **Recall traceability: inventory lots** (tests in `tests/test_lot_traceability.py`). Every item in `consumables` (reagents, packaging, sample containers, labels, CIP agents) is received as a LOT:
  `consumable_lots` (KelpWorks lot `<item #>-<YYYYMMDD>-<NN>`, the supplier, the SUPPLIER'S lot number (required), received / expiry date, quantity received and left, hold flag), created by
  `POST /api/consumables/<id>/receive` (`_receive_lot`; the Receive button) -- the plain `/adjust` (Adjust > "Used outside production") removes stock that left without a run and creates no lot; a physical count goes through `POST /consumables/<id>/count` (below). **Every use is assigned to lots inside
  `_consume`** (`_lots_allocate`: not-expired lots first by earliest expiry, no expiry last, expired lots after, never a lot on hold; what the lots cannot cover is simply untraced -- stock still never
  blocks anything) and recorded in `lot_uses` against the ledger's `ref` (processing lot, `CIP-...` ref, pre-processing batch lot); a refund with the same ref (`_lots_refund`) goes back to the same lots, so the
  net-change commits stay correct. Do not change stock by writing to `consumables.on_hand` directly: go through `_consume`. `GET /api/lots[?q=&expiring=1]`, `/api/lots/<id>/trace` (`_lot_trace`: the uses, the
  production runs reached -- directly, through a CIP event linked to a run, or through a pre-processing batch's output totes -- their finished-goods lots and the customers, via `_recall_report`),
  `/api/lots/run/<run id>` (`_run_materials`: what a run used with its lots and the untraced remainder; also in the recall report and the production summary PDF), `PUT /api/lots/<id>` (supplier lot / expiry /
  hold; Quality Manager or admin, a reason is required and written to the append-only `consumable_lot_log`) and `POST /api/lots/uses/<use id>/move` (correct which lot a use came from). Lots are not part of the
  release snapshot hash. UI: Inventory Items > Receive / Lots / Find / trace a lot; Product Release > "Materials used and their lots". Setting `lot_expiry_warning_days`. The three tables are records
  (`CLEAR` in `tools/make_clean_backup.py`).
- **Receiving: deliveries, several supplier lots, pack sizes, documents and counts** (tests in `tests/test_receiving.py`). The Receive dialog (`receiveStock`, `POST /api/consumables/<id>/receive`) takes a DELIVERY and one or more lot `lines`:
  `inventory_receipts` (supplier, `invoice_no` / `invoice_date`, `po_number`, `order_no` = the supplier's sales order, `delivery_doc` = BOL / packing slip / PRO, `carrier`, `ship_date`, `arrival_condition` ok | issue + note, notes;
  `_receipt_for` reuses the delivery with the same supplier + invoice number and fills its blank fields, so one invoice for several items is attached once). Each line becomes one `consumable_lots` row (`receipt_id`,
  `supplier_item_code`, `unit_price` PER STOCK UNIT, `received_as` "45 x Box, 50 lb"); a bad line rejects the whole receive. Packaging items (`is_container`) need no supplier lot: the invoice number stands in ("INV <no>", else "(none)");
  every other item still requires one. **Units**: an item has ONE stock unit (`kg g L mL ea`, `normalize_unit`, fixed once created); stock, lots and the ledger are always in it. Optional `consumable_packs` (name + `qty_per_pack`, unique per item)
  only convert what is typed (packs x qty_per_pack; a price given per pack is divided down); ANYONE may add one (`POST /api/consumables/<id>/packs` or `newPack` in a receive line), each add goes to the append-only `consumable_pack_log`; there is no pack edit / delete.
  **Documents**: `receipt_attachments` (kind invoice | delivery | coa | freight | other; same file rules, storage room and daily quota as every upload -- `receipt_attachments` is in both lists) via `POST /api/receipts/<id>/attachments`,
  downloaded at `/api/receipts/<id>/attachments/<aid>/download` (Bearer or a download token); never deleted. Details can be filled in or corrected later (`PUT /api/receipts/<id>`; a CHANGED existing value needs a reason; every change is in `receipt_log`);
  a lot received before deliveries existed gets one on demand (`POST /api/lots/<id>/receipt`). The lot finder also searches invoice / PO / order / delivery numbers. UI: lots table > Documents (`openDelivery`), lot trace > Delivery & documents.
  **Adjust** replaces the old "Use" button (`adjustStock`): "Used outside production" = `/adjust`; "Count" = `POST /api/consumables/<id>/count {counted, reason, note}` (`COUNT_REASONS`; Other needs a note; containers and labels whole numbers only):
  the difference goes through `_consume` as a ledger line "Stocktake count: <reason>" (a shortage is taken from the earliest-expiry lots; a surplus is untraced stock; a count with no difference is still a line) and sets `consumables.last_counted_at/by`.
  **Archive**: every delivery with at least one document is in the nightly records archive under `06_Receiving/<year>/<year-month>/<received date>_<supplier>_<invoice no>/` (`archive_receipt_entries`, `archive_receipt_folder`): the folder and the file-name key are fixed in `archive_receipts` the first time a document is archived (`RCPT-<id>` when the invoice number was unknown), files are `<key>_<Invoice|Delivery-Doc|CoA|Freight-Invoice|Document>_<name>.<ext>`, the version is the attachment id (a document never changes), `06_Receiving/_Index/receiving_index.csv` lists the deliveries, and `archive_file_bytes` finds a delivery document by its PATH alone (no run number), so the sync script is unchanged.
  The new tables are records / configuration for the clean-backup tool: `inventory_receipts`, `receipt_attachments`, `receipt_log`, `archive_receipts` are CLEAR; `consumable_packs`, `consumable_pack_log` are KEEP.
  **Approved suppliers** (KW-23; tests in `tests/test_supplier_list.py`): a delivery's supplier is CHOSEN, never typed. `consumable_suppliers` (per item, unique by name ignoring case) is the item's approved list;
  `consumable_supplier_log` is the append-only log of adds and removals (both KEEP in the clean-backup tool). Only an administrator or a Production Manager changes a list (`GET/POST /api/consumables/:id/suppliers`,
  `DELETE .../suppliers/:supplier id`; 403 otherwise; also returned as `suppliers` on `GET /api/consumables`). `_approved_supplier` is used by `_receive_lot` (Receive), the lot edit (`PUT /api/lots/:id`) and the delivery edit
  (`PUT /api/receipts/:id`): a supplier that is not on the item's list is 400 and is stored spelled as the list spells it; a blank supplier is still allowed; a lot / delivery that keeps its existing supplier saves unchanged
  even if that supplier has since left the list (only a CHANGED value is checked; a delivery's change must be approved for every item on it). Past deliveries and lots are left as recorded (no backfill). UI: Receive's Supplier is a
  dropdown with "+ Add a supplier..." for the people allowed to maintain the list (`supplierPicker`), the lot / delivery edit windows use the same dropdown, and Inventory Items > Edit has the "Approved suppliers" editor (`supplierEditor`).
- **Historical run entry** (past runs entered retrospectively; tests in `tests/test_historical_runs.py`; guide `docs/historical-entry.md`). Off until an administrator sets `historical_entry_enabled` = 1 (Calculations
  page). `POST /api/production/drafts` with a `historical {processingLot, release: review|grandfather, deductInventory}` object (`_create_historical_draft`; admin only, else 403; setting off -> 403 `historical_off`; the lot must
  match `HISTORICAL_LOT_RE` and be unused, the run date is required and not in the future) creates a draft that keeps the ORIGINAL processing lot and run date (`production_runs.is_historical`, `hist_deduct`, `hist_release`,
  `hist_note`, `hist_entered_by/at`) and gets NO default sample points. `_finalize_run` for such a run: `finalized_at` and its FG lots' `created_at` = run date 12:00Z (the normal path stamps now); required fields are NOT
  enforced (what was missing is stored in `hist_note`); `release: review` = the normal pending_review / pending_release path, `grandfather` = `release_state='legacy'` with the lots `on_hand` and no `submitted` event; a hash-chained
  `historical_entry` event records who entered it. **Inventory**: `_consume` returns at once for a run with `hist_deduct = 0` (no stock, ledger or lots; refunds too), and for a counted one `_lots_allocate` gets `as_of` = the
  run date (only lots received by then, expiry judged as of then). `_lab_regate` does nothing for a grandfathered historical run. A lot's received date cannot be in the future. Back-dated shipments use the existing Ship date.
- **Feedstock grade** (tests in `tests/test_feedstock_grade.py`). Every tote has a grade, computed -- never typed -- from the administrator's rules (`feedstock_grade_rules`, Admin > Feedstock grading,
  `GET/PUT /api/grade-rules`, `POST /api/grade-rules/test`, `GET /api/grade-rules/log`; PUT is admin only and each save writes `config_log` area `grade`). A rule is `IF (a AND b) OR (c AND d) THEN grade`:
  `groups_json` = list of groups (OR'd) of conditions (AND'd) `{field, op, value}`; fields `GRADE_FIELDS` = pH, ORP, `stab_weeks` (the same whole-weeks calculation as the Feedstock Inventory column,
  `tote_stab_weeks`), odour (text: is / is not / contains / does not contain) and odour intensity (ordinal Mild < Medium < Strong); number / ordinal ops `<  <=  =  >=  >  !=`; every field also has `is blank` /
  `is not blank`. A reading with no value never satisfies a comparison. `grade_for(rules, default, inputs)` (pure) returns the first ENABLED rule that holds, else the "otherwise" grade -- the `is_default = 1`
  row, created by `ensure_grade_rules` (`Ungraded` until an administrator sets one), so a grade is never blank. The grade is computed at read time (`GET /api/totes` -> `grade`, `gradeWhy`; `_grade_of_tote` for a
  single tote), so it follows readings, the growing stabilization period and rule changes at once; current odour / intensity are the latest `tote_stability_log` entries ('Odour', 'Odour intensity').
  **Changes are traceable**: `sync_tote_grades` keeps `tote_lots.grade` (the last RECORDED grade) in step and writes every change to the tote's Feedstock Stability log as a `Grade` row (from, to, a note with the
  trigger -- "Readings updated", "Grading rules changed (change #N)" (the `config_log` id of the rule change), "Daily check (stabilization period)", "Manual re-grade" or "First graded" -- the rule that decided and the
  readings), attributed to the person or "System". It runs after every non-GET request under `/api/totes`, `/api/harvest`, `/api/production` and `/api/preproc` (`_handle_api`), when rules are saved, once a day
  (`run_grade_daily_check`, thread `grade_scheduler`, `KELP_ERP_GRADE_CHECK_SECONDS`, default 900, flag `grade_synced_on`), via `POST /api/grade-rules/regrade` (admin) and at start-up (quiet: the first grade of an existing
  tote is set without a log line). A consumed / disposed tote is FROZEN (`grade_frozen = 1`) at the grade it had when it was used and shows that grade ("Fixed when the tote was used"); a run's `run_inputs` do not
  carry the grade (adding it to `_run_inputs_public` would change the signed-log hash and need a `RELEASE_SNAPSHOT_VERSION` bump). UI: Feedstock Inventory ⓘ details > Grade history. Adding a grade input = a `GRADE_FIELDS` entry + the value in the two places that build `inputs` + the Calculations entry. The table is configuration (`KEEP` in the clean-backup tool).
- **Sites** (tests in `tests/test_sites.py`): the site part of a tote lot number. Only the six in `SITE_LIST` are offered -- CDV Cordova, COR Cormorant, DIP Diplock Island, JAM James Island, KOD Kodiak, SGR Seagrove -- everywhere a person chooses a site (check-in, the pick-list and table filters; the CSV import and `POST /api/harvest` refuse any other code, `active_site_codes`), listed by name. `sites.active` = 0 keeps an older site (AOF, ASG, KIS, NO, RO, WB) for the totes that already carry it; filters still list such a site while a tote in the list uses it. `ensure_site_list` applies the list ONCE (`run_once`, a `config_log` row, area `site`; a later edit is never overwritten). **Cordova used to be COR and Seagrove used to be ASG**: their existing totes move to the site codes CDV and SGR but KEEP their lot numbers (they are the labels on the totes), then COR becomes Cormorant. `MIX` (pre-processing blends) is untouched. There is no screen to add a site yet: a new one is a code change to `SITE_LIST` (and a new `run_once`).
- **Feedstock % TDS** (tests in `tests/test_feedstock_tds.py`): an OPTIONAL reading on a tote (`tote_lots.tds_pct`, `tds_updated`), entered in Feedstock Inventory's "Update feedstock conditions" window (the Details card, `buildFeedstockCard` with
  `showTds: true`) and sent to `POST /api/totes/:id/characterize` as `tdsPct` (0-100, else 400; blank leaves it alone; 0 is a value) with `tdsDate` = the window's reading date. Like pH / ORP every change is logged to the tote's Feedstock Stability
  log as "Feedstock TDS (%)" (from, to, who, when; an unchanged value logs nothing) and the current value pre-fills the card. A production run's feedstock card does NOT show it (`run_inputs` and the signed-log snapshot are unchanged).
  It is shown in the tote's details and as an optional (hidden by default) Feedstock Inventory column; it is not a grading input yet (add one with a `GRADE_FIELDS` entry).
- **Historical readings** (tests in `tests/test_historical_readings.py`): the "Reading date" in Update feedstock conditions (default today, never in the future) dates every reading in that window -- pH, ORP, % TDS, odour, odour intensity and the other
  details. `tote_stability_log.reading_date` keeps it (NULL = the day it was logged; the log table shows a Reading date column). **The reading with the newest reading date is the current value** (ties: the later entry): `POST /api/totes/:id/ph` and
  `POST /api/totes/:id/characterize` (`readingDate`; `tdsDate` still works) write `tote_lots.ph / orp / tds_pct` and `*_updated` only when the reading is not older than the one on file; an OLDER reading is still logged (note "Historical reading ...", From blank, even
  when the number is the same) and the response lists it in `historical`. Odour / intensity / the other details have no column: their current value is the log's newest entry by `COALESCE(reading_date, date(created_at))` then id (`_latest_characterization`,
  the grade inputs in `GET /api/totes` / `_grade_of_tote`), so a historical reading never changes the grade. Re-saving the same value with today's date still logs nothing.
  **Received date**: editable in the same window (`POST /api/totes/:id/received-date {receivedDate, reason}`; `GET .../ph` returns `receivedDate` and `harvestDate`). Not in the future and not before the harvest date; changing a date that is already filled in
  needs a reason; every change is a "Received date" line in the Feedstock Stability log (old, new, who, when, reason); an unchanged date logs nothing. It is metadata only (labels, the table): the stabilization period runs from the harvest date.
- **Feedstock bulk import (CSV)** (`_harvest_bulk`, `POST /api/harvest/bulk`, tests in `tests/test_feedstock_import.py`, template `public/templates/feedstock_bulk_import_template.csv`). Columns `BULK_COLUMNS`: required site, species,
  harvestDate; optional avgWeightKg (blank = not weighed yet, stored NULL, range-checked when given), toteNumber, receivedDate, grind, stabilizationMethod, storageUnit, storageSource, location, ph, orp, tdsPct, status (in_stock | hold; QAQC Hold <-> hold as everywhere), notes; headers are case-insensitive,
  an unknown / missing / repeated column is a row-1 problem. The calculated inventory columns (lot number, stabilization period, grade, last updated) are not importable. **The whole file is validated first**: every problem is collected as
  `{row, column, message}` (row = spreadsheet row, header = 1) and returned as 400 `import_errors` with `problems` / `problemCount` (`ApiError(..., extra=...)`); one problem means NOTHING is imported. `dryRun: true` only checks and returns the lots
  and warnings (new site / location codes). **Lot numbers cannot repeat**: lot = `SITE-SPECIES-YYYYMMDD-NNN`; an explicit toteNumber (1-999) is refused if it repeats in the file, exists in `tote_lots`, or belongs to a DELETED tote
  (`tote_numbers_used` reads `deleted_records`; deleted numbers are never reused); blank numbers continue after the highest number in use (existing, deleted and explicit); the normal check-in uses the same helper. A partial unique index
  `idx_tote_unique_number (site_code, species_code, checkin_date, tote_number) WHERE preproc_batch_id IS NULL` is the backstop (a soft step: skipped and logged if an old database already holds a duplicate). Each imported tote gets an
  "Imported" line in its Feedstock Stability log. UI: `openFeedstockImport` ("Check file" shows the problem table; Import re-checks). **Setting one value on many totes**: `POST /api/totes/set-bulk {ids, avgWeightKg?, grind?, stabilizationMethod?, note?}` (UI: Feedstock Inventory > select totes > "Set values for selected", `bulkSetTotes`) sets only the fields given, on every selected tote, all or nothing (400 if any is in a run / consumed / disposed or a Pre-Processing blend would leave Fine, 404 if one is gone), one Feedstock Stability log line per changed field per tote.
- **Clean backup tool** (`tools/make_clean_backup.py`, tests in `tests/test_clean_backup.py`; guide: the last section of `docs/restore-runbook.md`). Turns a full backup into one with the configuration and NO records
  (the way to wipe live and start fresh: restore the clean zip). `KEEP` / `CLEAR` list every table; **a new table must be added to one of them** or the test (and the tool) fails -- records go in `CLEAR`, configuration
  and its change history in `KEEP`. Customers are records; consumable stock is set to 0; `uploads/` is dropped; the file is rebuilt with `VACUUM INTO` so removed rows do not linger. Never run it against live data directly.
- **Records archive and nightly backups** (batch 9; tests in `tests/test_records_archive.py`; guide `docs/records-archive.md`). The ERP decides what is archived and how it is named; the stdlib script
  `tools/kelpworks_archive_sync.py` (an office PC, Task Scheduler) copies it into the synced SharePoint library `KelpWorks-Records`. `archive_manifest` (`GET /api/archive/manifest`) is a pure function of the
  database: for every FINALIZED run `archive_run_entries` lists the files with a `version` (generated PDFs: `archive_content_key` = hash of the report data without the generated-at line; documents: the attachment id),
  and `GET /api/archive/file?run=&path=` serves one (the production summary / CoA PDFs are built by the same Handler methods as the download buttons, through `archive_handler()`). Naming:
  `<processing lot>_<document type>_<detail>.<ext>` via `archive_slug` (letters, numbers, hyphens); run folder `02_Production-Runs/<year>/<year-month>/<lot>_<SKU>_<run date>/` is stored once in `archive_runs`
  and never changes; documents are classified by what refers to them (`lab_requisitions`, `lab_results.attachment_id`, `run_inputs` photos, tote photos, else image / other). **The archive only grows**: the script
  moves a replaced file into `_superseded` and never deletes a record (the CoA entry `supersedes` its PRELIMINARY file when released). Access = an administrator, or the `X-Archive-Key` header (env
  `KELP_ERP_ARCHIVE_KEY`, 24+ characters, constant-time compare, throttled like a sign-in); the key can read only `/api/archive/*`. **Nightly backup** = `run_nightly_backup` (`backups/nightly/kelp_erp_<date>.zip`, the same
  zip as `build_full_backup`, free-space check, newest `KELP_ERP_BACKUP_KEEP` kept) driven by the `backup_scheduler` thread (after `KELP_ERP_BACKUP_HOUR_UTC`; on by default only in production); `POST /api/archive/backup` runs
  it now OUTSIDE the request transaction (the sqlite backup API waits forever on a connection that holds its own write transaction -- never call `build_full_backup` inside `_handle_api`). The script keeps
  daily / weekly / monthly tiers and uses Windows extended-length paths. Changing a file name rule moves files in the library, so treat the names as a contract: add a test.
- **Photo archive: photos move from the server disk to SharePoint** (KW-04; tests in `tests/test_image_archive.py`; guide `docs/records-archive.md`). Pictures only (`IMAGE_EXTS`) in `run_attachments` (feedstock photos, note
  pictures, other images) and `tote_attachments` (tote photos of a run that was finalized). Four additive columns on both tables: `archived_at / archived_path / archived_sha256` (the confirmation) and `purged_at` (the file was
  removed). **Confirm**: the sync script, after saving a photo in the library, sends `POST /api/archive/confirm {files:[{path, src, runId, sha256}]}` (archive key or administrator, `archive_confirm_images`); the confirmation is kept
  only when the path is the one `archive_run_entries` gives that file and the SHA-256 equals the one of the server's own file. The manifest says per picture `confirmed` / `purged`; a purged picture stays listed and is not a "missing
  file" problem, and the script warns if a purged photo is missing from the library (it is then the only copy). **Purge** (`run_image_purge`): only when `image_purge_enabled` = 1 (**default 0**), the run was finalized more than
  `image_archive_days` (default 30) ago, is not open for amendment, and the file's SHA-256 still equals the confirmed one; every removal is a row of the append-only `image_purge_log` (CLEAR in the clean-backup tool) and the file is
  deleted after the commit. A daily thread (`image_purge_scheduler`, `run_image_purge_daily`) and Admin > Records archive & backups > Photo archive (`POST /api/archive/images/purge`, with a dry run) run it. A purged photo's download
  answers a browser with a redirect to the library (`KELP_ERP_SHAREPOINT_BASE_URL` + the archived path) and everything else with 410 and `archiveUrl`; the SPA replaces a picture that fails with that link (`document` error handler).
  The upload-room check no longer counts purged photos. Never delete a photo file by any other path.
- **Controlled documents: SOPs and safety data sheets synced from SharePoint** (batch 10; tests in `tests/test_documents_library.py`; guide `docs/controlled-documents.md`). SharePoint
  (`03_SOPs/Current`, `04_Safety-Data-Sheets/Current` of the KelpWorks-Records library) is the master; KelpWorks mirrors it ONLY after an administrator accepts. The sync script reports each folder
  (`POST /api/library/scan {kind, files:[{path,size,sha256}]}`, the WHOLE folder, archive key or admin) and uploads what is asked for (`POST /api/library/upload?kind&path&sha256`, raw body, <= 25 MB, stored in `library_inbox/`).
  `library_reconcile` (pure, testable) turns the difference into `library_inbox` rows: **new** (identity not in the register), **revision** (same identity, different sha256, or a withdrawn document back), **missing**
  (a current document whose file left the folder; never raised when the folder is empty), **unreadable** (name does not parse: `parse_sop_filename` / `parse_sds_filename`). Identity: SOP = `doc_number`
  (`SOP-012`; `SOP002_...` also reads), SDS = `sds_key` (chemical|supplier; the issue date is the version). A rename with identical content is recorded quietly (`source_path`). Admin routes (`/api/library/inbox`, `/accept`, `/ignore`): accept
  copies the waiting file into `SOP_DIR` / `SDS_DIR`, puts the file it replaces into `document_revisions` (kept on disk; `GET /api/doc-revisions/:id/download`), logs to `sop_document_edits` / `document_edits`, and never deletes: a removed
  file = status `withdrawn`. `sop_documents` gained doc_number / revision / status / file_sha256 / source_path (migrate); the existing SOP (`wet_solids_sop`) is linked once via `linkTo` (its links and key stay).
  **The SOP NUMBER decides new vs revision**: a number not in the register is a NEW SOP, a number it has is a REVISION of that SOP (`library_reconcile`, identity = `doc_number`). The review (`_inbox_public`) shows the result:
  `sopNumber`, `currentName`, `newName` (= `sop_name_from_file`, what the SOP will be called once accepted), `currentRevision` / `newRevision`, `renamed`, `nameNote` (the number was added because another SOP has that title); the Documents
  review table shows "New SOP" / "Revision of SOP-012" and the name ("old -> new" when the title changes).
  **Retiring and replacing** (administrators, `POST /api/sop-documents/<id>/retire|replace|reinstate`, `_sop_lifecycle`, tests in `tests/test_documents_library.py`): status `retired` (in `STATUS_RULES`; columns `retired_at/by/reason`,
  `replaced_by`) takes an SOP out of use -- not in `/api/documents`, `refdata.sops`, the pickers or the production log (its `sop_links` are removed and logged; its file, revisions and history stay). **Retire** needs a reason and works on
  any SOP (for example its file left the Current folder with no replacement; a pending "missing" item for it is then settled as "Already retired"). **Replace** is only for an SOP with NO number (one loaded before the sync): its links
  and, if the replacement has none, its reference key move to the SOP that supersedes it (`replaced_by`), then it is retired. A numbered SOP is controlled in SharePoint (replace = a revision; 409 `controlled_in_sharepoint`).
  **Reinstate** undoes a retirement (places must be linked again); a retired SOP's file coming back to the folder is offered as a revision and accepting reinstates it. `ensure_constraints` DROPS and recreates the status triggers every
  start (an older database's trigger would otherwise refuse a status added to `STATUS_RULES` later), and the legacy wet-solids key seed in `migrate()` never takes a key back from the SOP that received it. The review's "new SOP"
  window (`i.candidates` = current SOPs without a number, with `suggested` / `why` from `sop_shared_words`) makes the choice explicit: no default, a required answer, and a confirmation before adding a new SOP while a number-less one exists.
  **The SharePoint file name is the single source of truth**: an SOP's `name` = `sop_name_from_file` (the file's title, else the number; the number is appended when another SOP has that name) on accept, on a new revision and
  on a same-content rename (`library_reconcile`); a typed name is ignored and `PUT /api/sop-documents/:id` refuses to rename a numbered SOP (409 `controlled_in_sharepoint`); an SDS's chemical / supplier / issue date also come only from the file name.
  New tables: `sds_documents`, `sds_items` (SDS <-> inventory items; shown as an SDS link on Inventory Items), `document_edits`, `document_revisions`, `library_inbox`, `library_scans` (last report per folder; stale after `library_scan_stale_days`).
  SDS review due = issue date + `sds_review_years` (`sds_review`, Calculations page). `SDS_DIR` is in `backup_dirs()` (the full backup / staging restore); the waiting inbox is not (the sync uploads it again). The UI is the "Documents" tab
  (`pageDocuments`, `libraryReviewCard`, `reviewLibraryItem`). A new status value for these tables goes in `STATUS_RULES`. Admin > "SOPs" is the same table as Documents (`sopTable`; "Linked at" is shown to everyone) plus the optional,
  editable `key` (nothing is coded against it) and **SOP links**: `sop_links (spot, sop_id)` chosen by an administrator (`PUT /api/sop-documents/:id/links {spots}`, `PUT /api/sop-links/<spot> {sopIds}`,
  `POST /api/sop-links/bulk {sopId, action: add|remove, spots}`; every change goes to the SOP's history). A spot is a key in `SOP_SPOTS` (server; a row is `(key, area, stage, kind, label)`: area = Production log / CIP Log / Samples /
  Shipping, stage = the section, kind = Section / Page / View / Process Check / QC Check / Sample Point / Other box / Window, mirrored by `SOP_KIND_ORDER` in app.js; the picker (`sopPlacePicker`, `sopPlaceControls`,
  `sopPlaceGroups`) filters by area and groups by section or by kind, and is shared with the "Where SOPs are linked" table (Admin; kept MINIMIZED -- a collapsed section that is always closed when the page loads; it stays open only while you work in it, `State.sopPlacesOpen`); the production-log WINDOWS are deliberately not offered; links to places no longer listed are pruned at start) and an element with `data-sop-spot="<key>"` (app.js: sections, boxes,
  pages, `buildQualityCheckBox` opts.spot, `buildFeedstockCard` opts.sopSpot, `SAMPLE_VIEW_SPOTS`) or a window whose title starts with an entry of `SOP_WINDOW_SPOTS`; a debounced MutationObserver
  (`applySopSlots`) puts a small SOP icon (KW-12) beside the title of each spot (a QC check box's title, a window's heading, a section's summary line; a plain block gets a small icon row at its top), from `refdata.sopLinks`; clicking it opens a popup (`showSopPopup`) listing the linked SOPs, each opening its file. `tests/test_sop_links.py` fails when the two key lists differ: add a new place in BOTH. **An SOP cannot be deleted in
  KelpWorks** (`DELETE` answers 409 `controlled_in_sharepoint`: remove the file in SharePoint and the document is withdrawn) and `_update_sop` refuses a hand-uploaded file for a numbered SOP (same code).
- **Production-log required fields** are defined once, server-side, in `PROGRESS_SECTIONS` /
  `REQUIRED_FEEDSTOCK` (`kelp_erp_server.py`; exposed to the SPA as `refdata.requiredFields`).
  Everything is required except: every Notes field, the extraction fields when extraction is bypassed (KW-15), the two Separation solids % moisture readings individually (KW-18: ONE of them must be entered -- a registry entry whose key is a tuple, `("pctMoisture", "pctMoistureScrew")`, is filled when any key has a value; `tests/test_moisture_one_required.py`), **odour** (the feedstock Odour / Odour intensity are optional since KW-05 --
  recording an odour exposes operators to mould and VOCs -- and the final QC check's odour is optional by design, KW-06), the Homogenization / Separation /
  Pasteurization sample-point boxes, Extraction's QC Check Total-solids + Density fields,
  checkboxes, calculated values and legacy dilution-tank rows. `_run_progress` returns
  per-section `{total, filled, missing, done}` (attached to drafts and `_run_full`; also
  `GET /api/production/<id>/progress`) -- the progress chips are green only when a section is
  `done`. `_finalize_run` ends with `_required_problems`; raising there rolls the whole
  finalize back. The SPA marks required labels with `rfield()/reqLabel()` (red `*`), and
  `finalizeRun` saves the header + presses every `button.section-save` before finalizing (`pressSectionSaves`); the draft's **Save & close** does the same (a section that can't be saved keeps the window open with its message), and so does Save & close on the Process log of a run under amendment. When
  adding a production-log field, add it to the registry (and use `rfield`) or it stays optional.
  **Final QC odour (KW-06)**: the Packaging QC Check ("LKE characterization", the final check on the pasteurized product) has an optional Odour + Odour intensity (`production_runs.packaging_odour` /
  `packaging_odour_intensity`, `buildQualityCheckBox` opts.`showOdour`, same vocabulary as feedstock). They are in `stages.packaging` (and so in the signed snapshot) ONLY when written, so no earlier run's hash
  changes; the production summary PDF's Packaging row shows "final odour ...". Tests: `tests/test_odour_optional.py`.
  The feedstock accept/reject decision must be an explicit choice: `run_inputs.decision` is
  NOT NULL, so `run_inputs.decision_set` records whether an operator actually chose (inputs of
  runs finalized before it existed were backfilled as set; `_run_inputs_public` returns
  `decision: None` until set).
- **Homogenization tank 2A / 2B** (KW-10 / KW-11; tests in `tests/test_homogenization_tanks.py`). A run names its tank(s) -- `production_runs.homog_tanks` `2A` | `2B` | `2AB`, required, no default -- and each tank in use has its OWN
  Pre-Dilution Tank Level, solids-loading Process Check (wet / liquid weights), Dilution water added and Post-Dilution Tank Level in the `homog_<stem>_2a` / `_2b` columns (JSON `slurryL2a`, `wetSolidsWtG2b`, ...). The Target %Solids
  Loading is shared; QC Check, Sample Point and Rinse water stay one set for the lot. `_recompute_homogenization` (called by `save_stage`) calculates each tank's Measured %Solids, Recommended Dilution Water (c1V1 = c2V2) and Lot %Solids,
  clears the tank not in use, and sets the RUN-LEVEL values (`slurryL`, `dilutionWaterL`, `postDilutionTankL`, `dilutionWaterTargetL` = sums; `pctWetSolids`, `lotPctSolids` = volume-weighted; `wetSolidsWtG` / `liquidWtG` copied for one
  tank, NULL for two) -- the same pattern as the 6A/6B run totals, so the summary PDF, the water totals and reports keep reading the run-level fields. A total exists only when every tank in use has its value. The per-tank keys appear in
  `stages.homogenization` (and the signed log) only when set; required fields use the `_uses_2a` / `_uses_2b` predicates (the run-level fields are no longer required). Setting `homog_tank_capacity_each_l` (5000 L per tank) drives a
  warning only. A run with no tank choice is left exactly as entered.
- **Presence: who has what open** (KW-07; tests in `tests/test_presence.py`). Concurrent edits are still "last save wins" (no record version check, by decision); presence only WARNS. The server keeps an in-memory register
  (`PRESENCE` / `PRESENCE_ONLINE`, `presence_update`, `presence_leave`; nothing in the database or the backups, lost on restart -- browsers re-announce within seconds): a browser tab calls `POST /api/presence {tab, open: [{key, label}]}`
  every `presence_timeout_seconds` / 3 (setting, default 90 s, on the Calculations page) and the answer is who is online and who has each card open; `{tab, leave: true}` forgets a tab (sent on `pagehide` and sign-out). The
  call is exempt from the per-request write transaction and its 200s are not logged. Card keys: `run:<id>`, `batch:<id>`, `tote:<id>` (`PRESENCE_KEY_RE`; a new kind of card needs a regex entry). The SPA (`Presence`, `presenceEnter`)
  asks first when someone else has the card open (Open anyway / Cancel), then tracks the editor window that opens next until it is closed, with a live banner inside it; initials badges (`presenceBadge`) sit on draft / finished
  run cards, draft batch cards and the Feedstock Inventory rows, and the top bar lists who is online. Hooked in: `openRun` (drafts), `openProcessLog` (only under an open amendment, otherwise the log is read-only),
  `openPreprocBatch` (drafts) and `updateCondition` (tote). A new editor that people can overwrite each other in should call `presenceEnter('<kind>:<id>', label)` before it opens.
- **Bypass extraction** (KW-15 / KW-16; tests in `tests/test_extraction_bypass.py`). A run can bypass extraction (the product still flows through the flow cell, nothing optimized):
  `production_runs.extraction_bypassed` + `extraction_bypass_comment` (default `EXTRACTION_BYPASS_DEFAULT` "Extraction not optimized", editable, a blank reason is 400), set through the extraction stage
  save. While bypassed none of the extraction fields is required (the `_extracting` predicate) and the reason is (`_bypassed`); `_apply_extraction_bypass` clears the extraction readings on save and drops the
  reason when the bypass is turned off. `bypassed` / `bypassComment` are in `stages.extraction` (and the signed log) only when set. **The Extraction Performance card of a bypassed run is the Homogenization-out QC
  check** -- nothing is copied: `extraction_view(extraction, homogenization)` relinks it for the production summary (Extraction row "Bypassed: <reason>", scorecard column), Yield & Usage (`tdsAfterExtraction` = the
  homogenization TDS) and the screen (read-only table). **Extraction efficiency is not applicable** for such a run (blank, shown "bypassed", never 0 %) in the run card, summary, Yield & Usage (CSV / XLSX) and
  the QC charts (no `qc:extraction_eff` point, and no extraction readings, so the Extraction section statistics are not polluted). The reason also shows in the run's Notes panel (`runNoteItems`) and so at the
  top of the release review. Separation reads nothing from extraction, so "separation in = homogenization out" needs no code. A new consumer of extraction QC values must go through `extraction_view`.
- **Run notes** (KW-01; tests in `tests/test_run_notes.py`). `run_notes` (run, `section` = one of `NOTE_SECTIONS`: initiation / feedstock / homogenization / extraction / separation / pasteurization / dilution / packaging / general,
  `body` <= 4000 chars, created / updated by + at) and `run_note_images` (a note's pictures, each a `run_attachments` row stored and served like any document; pictures only, <= 10 per note, the same upload rules / quota / archive).
  Routes `/api/production/<id>/notes[/<nid>[/images[/<aid>]]]` (`route_run_notes`): like every production-log entry a note can be added, changed or removed while the log is open (draft, or an open amendment) --
  `_amend_guard` answers 409 `amendment_required` for a finished run and `_release_reconcile` voids a sign-off the change contradicts. **Part of the signed log**: `_run_full` carries `logNotes` ONLY when the run has notes (so a
  log without notes is unchanged); the pictures (`logNoteImages`, in `_RELEASE_HASH_SKIP`) are not, like documents. A note added / edited / removed under an amendment is a recorded revision change (`_item_label`). The SPA's
  `buildNotesSection` is the "Notes" accordion (add form with section + text + pictures, edit / remove) and `attach` puts each section's notes at the bottom of that section's accordion; they are also in the run's Notes panel
  (`runNoteItems`) and on the production summary PDF ("Notes", ordered by section, pictures counted). Removing a note (or a draft) removes its picture files after the commit. `run_notes` / `run_note_images` are records (`CLEAR`).
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
  **Small dilution tweaks** (KW-19 / KW-20 / KW-21; tests in `tests/test_dilution_tweaks.py`): the plan's "TDS concentrated" follows the Separation filtrate TDS AS IT IS TYPED (`separationTds(stages)`: the live value
  `stages.separation.liveLiquidTdsPct` kept by the Separation section, else the saved `liquidTdsPct`), not only after Separation is saved; the preservative totals are an `amountTile` (litres, with the kilograms the
  stock solution holds on the line under it); a preservative whose SKU target is 0 starts at 0 in each tank's "added (L)" box (`zeroFillIfNotUsed`: only a blank box, taken away again if the SKU changes to one
  that uses it) while a preservative the product needs stays blank and required. A 0 counts as entered, and `_commit_reagent_usage` writes no ledger line for a 0 net change.
- **Pre-Processing (shred + blend)** (`preproc_batches` / `preproc_inputs` / `preproc_packaging`, `route_preproc`,
  `/api/preproc`, the "Pre-Processing" tab: batches are cards like production runs -- `pagePreproc` / `preprocDraftCard` (section chips from `_preproc_progress`, the same `stageProgress` component) / `preprocDoneCard` -- and `openPreprocBatch` opens the editor / batch record in a wide window) turns coarse-grind feedstock into
  fine-grind feedstock (harvest check-in / CSV import set `grind`, default Coarse; check-in no longer has a storage-unit source or deducts empty-IBC stock). A draft batch pulls coarse or whole totes (`tote_lots.grind` Coarse or Whole, in stock, not on QAQC Hold) from a
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
  inventory = samples whose description is `Retention`; discard-by = collection date + `sample_retention_months` (default 24 months, KW-33; a database that still held the old default 12 moved to 24 once, `retention_months_24`). Labs + their
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
- **Samples / lab small changes** (KW-14 / 27 / 34 / 35; tests in `tests/test_samples_lab_small.py`). The Analysis catalogue hides the *ID Detailed* and *ID Simplified* columns by default (a per-browser "Columns" chooser,
  `sampleColumnMenu`, key `kelp.sampleCols.catalogue`) and its status filter is a multi-select (`multiFilterButton`, `f.statuses`) that starts as every status except *Sent to lab* so what was sent is one click away; the Retention
  inventory keeps its own "Show removed / sent samples" box. In the production log each Sample Point card ends with *Containers to collect* (`sampleContainerTotals`: the quantities added up per container), and a *Sample summary*
  card after the Notes section, at the end of the log (`buildSampleSummaryCard`, refreshed with the section progress) shows samples per process point and type with the container totals, the same grouping as the Production Log Summary PDF.
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
- **Finished goods without a SKU yet** (KW-32; tests in `tests/test_assign_sku.py`). The products that use no labels (Sugar Kelp Extract `SACC-LKE`, Macrocystis Kelp Extract `MACRO-LKE`, i.e. `fg_skus.uses_labels = 0`) are
  finished goods whose SKU is assigned when they ship -- not intermediates: they are released, have a CoA and are stocked like any lot. Every lot remembers what it was made as (`fg_lots.made_as`, set by `_mark_fg_assignment`)
  and a no-label product's lots start with `sku_assigned = 0` (shown "<product> (no SKU yet)"; its FG label is still printed -- the Label buttons, "Print all on-hand labels" and the run card's "Print FG labels" include it and the label's Product line says "(no SKU yet)" -- and prints with the SKU once assigned). The production run's SKU picker (`skuGroupedSelect`: new run, historical run, draft editor) groups the products as No SKU yet / Non-organic / Organic and a NEW run starts on Sugar Kelp Extract (`NEW_RUN_DEFAULT_SKU`). **Assigning** (`_assign_sku`,
  `POST /api/fg/:id/assign-sku {skuCode, qty?, note?, password}`) is a signed act (`_require_sku_signer`: a Production or Quality Manager, password re-entered; the admin role alone is not enough) on a RELEASED (`on_hand`) lot, to
  any active SKU that uses labels, a whole number of units: the whole lot is renamed in place (the lot number is on the IBCs and stays), part of a lot is split into a child lot (`parent_lot_id`, same run, number
  `<parent>-<SKU>`), the SKU + package's label item is deducted through `_consume` (stock never blocks), and a hash-chained `fg_lot_sku_assigned` event records who, from / to and the units. `create_shipment` assigns
  what has no SKU yet in the same transaction: each line of an unassigned lot carries `skuCode` (two lines of one lot may ship as different SKUs), a missing SKU is 400, assigning needs the signer + `password`. The run keeps
  its own SKU (Yield & Usage, release and the CoA are unchanged); a moved (split) lot copies the SKU state (and ph) from its source.
- **Finished-goods TDS and pH** (KW-31; tests in `tests/test_fg_actual_quality.py`): `fg_lots.tds` / `fg_lots.ph` are the run's ACTUAL final Packaging QC Check results (`packaging_tds_pct` / `packaging_qc_ph`), never the product target
  (finalize stored the target before). `_sync_fg_lot_quality` sets them at finalize and again whenever the Packaging stage is saved on a completed run (an amendment); a lot created later by a packaging edit takes them from the run
  (`_adjust_fg_lot`). They are read-only: `PUT /api/fg/:id` answers 400 to a changed `tds` / `ph` (amend the run instead). Shown on the Finished Goods table, its Edit window and the printed FG label (the CoA already reads the run).
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
- **Amendments and the release queue** (KW-28 / KW-29; tests in `tests/test_amendment_queue.py`). A run is in the Product Release queue as "Awaiting production-log review" only once its amendment is SUBMITTED
  (`_amend_close`: `pending_review`, review hash cleared, earlier review / release no longer shown -- `_release_summary` reports `reviewedBy` / `releasedBy` only while the state is `pending_release` / `released`); an open
  amendment is listed as "Under amendment" and saving the log does not change that, so the Process log's **Save & close** under an amendment asks whether to submit it now. **Notes in one place**: `runNoteItems` / `runNotesPanel`
  (app.js) list every free-text note -- run notes, feedstock tote notes and rejection reasons, dilution tank notes, each amendment's reason and the amender's note to the reviewer (`run_revisions` rows now carry `comment` =
  `run_amendments.submit_comment`, not part of the signed snapshot) and the sign-off comments -- at the top of the run card and of the Product Release review window. A new free-text field on the log belongs in `runNoteItems`.
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
  Legacy runs without packaging entries can't be cross-checked and are reported as info only. **Pre-Processing stock** (tests in `tests/test_preproc_packaging.py`): every COMPLETED batch is also checked against the inventory ledger -- `_preproc_stock_expected` (pack-out containers summed per container, citric acid, emptied source IBCs returned to Used IBC) vs the net of every `consumable_txns` line carrying the batch lot as `ref` (`_preproc_stock_differences`); a difference is an error (area "Pre-processing stock", `batchId`) with the repair `restock_preproc` (`POST /api/integrity/repair {kind, batchId, password}`): the difference goes through `_consume` as one more ledger line (reason "Integrity repair (pre-processing stock)") plus a `config_log` row (area `integrity`); it does not use `release_events` (those rows need a run).
- **Env vars:** `PORT` (8002), `KELP_ERP_DB`, `KELP_ERP_UPLOADS`,
  `KELP_ERP_SECRET` (when unset or equal to the old public default, a random key is generated once into `kelp_secret.key` next to the database), `KELP_ERP_ADMIN_EMAIL/PASSWORD`,
  `KELP_ERP_INITIAL_PASSWORD`, `KELP_ERP_REQUIRE_MOUNT` (refuse to start unless this path is a mounted volume), `KELP_ERP_RESTORE_FROM` + `KELP_ERP_RESTORE_CONFIRM=replace-live-data` (one-time live restore at start;
  remove both afterwards), `KELP_ERP_ENV=development` (convenience defaults), `KELP_ERP_TRUST_PROXY=1` (trust the last `X-Forwarded-For` entry; always on Render); staging only: `KELP_ERP_ENV=staging`, `KELP_ERP_ALLOW_RESTORE=1`,
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
- **Dark theme** (KW-43; tests in `tests/test_theme.py`): each person's own choice, `users.theme` (`light` | `dark`, NULL = light; returned as `theme` by `/api/me` and the sign-in answer, set by `PUT /api/me/theme`), mirrored in
  `localStorage` so the page and the sign-in screen are drawn in it at once (`applyTheme` / `toggleTheme`, the moon / sun button in the top bar); the page is `<html data-theme="dark">`. `styles.css` states every
  hard-coded light surface / border / dark-text colour as a variable (`--bg-xxxxxx`, `--bd-xxxxxx`, `--fg-xxxxxx`: the light value in `:root`, the dark one in the `html[data-theme="dark"]` block, a test fails if one is missing) and the
  heading colour as `--accent-text`. **Do not hard-code a light colour for a background, border or dark text in CSS or an inline style: use one of these variables (or a new pair, with its dark value).** The Quality Control charts
  are SVG with fixed colours; their dark-mode overrides are attribute selectors at the end of `styles.css`. Print windows and the document previews stay light on purpose.

## Domain model (key tables)

`species`, `sites`, `tote_lots` (stabilized totes; status in_stock/wip/hold/consumed/
disposed; `grind` Coarse|Fine|Whole), `preproc_batches` / `preproc_inputs` / `preproc_packaging`, `samples` / `sample_events` / `sample_cart` / `labs` / `lab_analyses` / `lab_requisitions`, `production_runs` + `run_inputs`, `fg_lots`, `consumables` +
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

**Data integrity:** `docs/data-integrity-statement.md` describes the ALCOA+ controls to auditors and customers; keep it in step when a control it names changes (audit trails, signatures, locks, backups, release gate, tests).

**Change-log items:** the team's requested changes are tracked in `docs/backlog/` (CSV snapshot of the Excel change log); work one with `/kw-item KW-nn` (see `docs/release-guide.md`).

**Process: read `docs/release-guide.md`.** Work on a branch (fixes: `hardening/<name>`), never push to `main`: open a pull request, wait for the green `test`
check, and the user merges it (Render then auto-deploys live). Staging (a copy of live data, manual deploys) is in `docs/staging.md`. Keep the release guide in
step with any change to this process, and add the live commit to `tests/legacy_commits.txt` after each release.
