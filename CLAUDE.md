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
  per finished unit at finalize -- not the Labels tab, which prints internal
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
`consumable_txns`, `run_reagent_commits`, `cip_events` / `cip_event_chemicals`,
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
