# KelpWorks ERP

A small web-based manufacturing ERP for Cascadia Seaweed's liquid kelp extract
(LKE) process. Tracks stabilized kelp inventory, production runs, finished
goods, reagents/packaging, and prints barcode tracking labels.

Built dependency-free in the same shape as the other apps in this repo: pure
Python standard library (sqlite3 + http.server) for the backend, vanilla JS for
the web UI. **No `pip install` required.**

## Run

```
python kelp_erp_server.py
```
(or double-click `run.bat`) then open <http://localhost:8002>.

On a NEW database in local development (`run.bat` sets `KELP_ERP_ENV=development`) the seed login is **admin@kelp.local / kelp1234**. Anywhere else the first-run admin password is generated and printed once in the server log (or set `KELP_ERP_ADMIN_PASSWORD`).

## What it models

The process: ocean-harvested kelp is ground and stabilized with citric acid in
1000 L IBC totes, stored, then later diluted to a target TDS, preserved with
citric acid + potassium sorbate, and bottled as finished Liquid Kelp Extract.

| Area | What it does |
|------|--------------|
| **Dashboard** | Stabilized totes & kg on hand, finished-goods litres, low-stock alerts, recent runs |
| **Stabilized Inventory** | Every IBC tote as a lot. **Check in a harvest batch** → enter total kg + tote count and the system averages the weight across totes and auto-generates lot numbers |
| **Production** | Pick stabilized totes, set a target TDS, add citric/sorbate, define the packaged output (IBC / 4L / 1L / 250ml). Consumes the totes, draws down reagents, finished-good labels and empty IBCs, and creates finished-goods lots under an auto-generated Processing Lot # |
| **Product Release** | Finished goods are held *Pending Release* after a run is finalized. A Production or Quality Manager reviews and signs the production log, then a Quality Manager signs to release (or rejects) before product can be shipped. Signatures re-ask for the password and are recorded with the log's SHA-256 in a tamper-evident, hash-chained audit trail; editing the log after sign-off voids it. Printable record + CSV. Permissions are set per user in Admin |
| **Amend run** | A finalized run's production log is locked. To change a log entry, a user with the Production Log Amender permission (set per user in Admin) opens an amendment (reason + category); product is held while it is open, the change is recorded as one revision with a before/after diff, and the run goes back for review. Documents and label printing never need an amendment |
| **Data integrity check** | Admin / Quality Manager tool (Admin and Product Release pages) that cross-checks finished-goods lots, packaging entries, output litres, stock commits, release status, signed-log hashes and open amendments, with password-confirmed, audited repairs |
| **Finished Goods** | Two SKUs (Saccharina LKE, Macrocystis LKE) on hand by package size; edit qty / status / location |
| **Yield & Usage** | Observed conversion rates (process = measured weights, harvest = batch-average weights) and per-run reagent / packaging / label consumption, grouped by any combination of product / species / farm / stabilization / harvest month / processing month, with medians/ranges, harvest + processing dates and final pH/TDS per run, an exclude-run flag, CSV and Excel export -- the basis for a future BOM |
| **CIP Log** | Log every Clean In Place (equipment, times, operators, chemicals used with concentration / temperature / contact time, pass/fail, optional run link); chemicals deduct CIP Acid / Caustic / Sanitizer stock; "Last cleaned" per equipment |
| **Inventory Items** | Reagents (Citric Acid, Potassium Sorbate, Sodium Benzoate), containers (IBC totes, 55 gallon drums, bottles) and finished-good labels per SKU + package type, each with an optional Item # — receive / use, reorder alerts |
| **Labels** | Code128 barcode tracking labels for any tote or finished-goods lot, print-ready 2-up |

## Lot numbering (matches the inventory spreadsheet)

- **Stabilized tote:** `SITE-SPECIES-YYYYMMDD-TOTE` — e.g. `JAM-SL-20260504-003`
  (James Island, *Saccharina latissima*, checked in 2026-05-04, tote 003).
- **Processing lot:** `PR-YYYYMMDD-NNN` — e.g. `PR-20260616-001`.
- **Finished-good lot:** `<processing lot>-<pack>` — e.g. `PR-20260616-001-IBC`.

## Seed data

`seed.json` was extracted from `202605 Inventory.xlsx` (species, farm sites, the
394 stabilized Sugar Kelp totes from the 2025/2026 Fresh Inventory tabs, and
reagent/packaging on-hand quantities). On first run the database (`kelp_erp.db`) is
created and seeded automatically.

## Configuration (environment variables)

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8002` | HTTP port (hosts inject this) |
| `KELP_ERP_DB` | `./kelp_erp.db` | sqlite database file |
| `KELP_ERP_UPLOADS` | `./uploads` | folder for attached documents |
| `KELP_ERP_SECRET` | generated key in `kelp_secret.key` next to the database | token signing secret — **set it when hosting** (a value that is the old public default is ignored) |
| `KELP_ERP_ENV` | (production) | `development` enables the convenience login defaults; `staging` is the staging site (see docs/staging.md) |
| `KELP_ERP_ADMIN_EMAIL` / `KELP_ERP_ADMIN_PASSWORD` | `admin@kelp.local` / generated and printed once (`kelp1234` only in development) | first-run admin account |

## Deploying to Render

Files included: `Dockerfile`, `render.yaml`, `Procfile`, `requirements.txt`
(empty — no deps), `runtime.txt`, `.dockerignore`.

**Persistence:** the database **and** uploaded documents both live under
`/var/data`, a 1 GB persistent disk. A disk requires the **Starter** plan
(~$7/mo). The admin account is created on the **first** deploy only, so set the
password env var *before* that first deploy.

1. Push this repo to GitHub (it is its own standalone repo with `render.yaml` at
   the root).
2. In Render: **New + → Blueprint**, connect the repo. Render reads `render.yaml`
   and provisions the web service + disk automatically. (Or **New + → Web
   Service**, pick the repo, Runtime **Docker**, and add a 1 GB disk at
   `/var/data` plus the env vars manually.)
3. Set the two `sync: false` env vars in the Render dashboard:
   - `KELP_ERP_ADMIN_EMAIL` — e.g. `you@cascadiaseaweed.com`
   - `KELP_ERP_ADMIN_PASSWORD` — a strong password (you'll log in with these)
   `KELP_ERP_SECRET` is generated automatically; `KELP_ERP_DB` and
   `KELP_ERP_UPLOADS` are preset to the disk.
4. Deploy. Open `https://<your-service>.onrender.com` and sign in.

**Back up** by downloading `/var/data/kelp_erp.db` and the `/var/data/uploads`
folder from the Render shell. To migrate existing local data up, copy your
`kelp_erp.db` and `uploads/` into `/var/data` on the disk.
