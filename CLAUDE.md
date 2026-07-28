# KelpWorks ERP — Claude Code project guide

A small **manufacturing / processing ERP** for Cascadia Seaweed's liquid kelp
extract (LKE) line: stabilized kelp inventory → production runs → finished
goods → customer shipments, plus consumables, reports, barcode labels, and an
admin panel.

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
- **Consumable stock** moves through `_consume(conn, id, delta, reason, ref)`
  which updates `on_hand` and logs a `consumable_txns` row (feeds the ledger).
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
`consumable_txns`, `customers` / `shipments` / `shipment_lines`, `disposals`,
`run_attachments`, `run_edits`, `location_moves`, `tote_ph_log`, `users`.

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
