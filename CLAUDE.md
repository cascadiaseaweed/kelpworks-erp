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
  `run_label_commits`) -- not the Labels tab, which prints internal
  barcodes), Reagents = the rest. `item_number` is an optional admin-set Item #.
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
  `State.tab` in `render()`. Add a tab: button in `index.html`, entry in the
  `render()` map, and a `pageX(v)` function.

## Domain model (key tables)

`species`, `sites`, `tote_lots` (stabilized totes; status in_stock/consumed/
disposed), `production_runs` + `run_inputs`, `fg_lots`, `consumables` +
`consumable_txns`, `run_reagent_commits`, `release_events`, `cip_events` / `cip_event_chemicals`,
`customers` / `shipments` /
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
