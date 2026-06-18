# KelpWorks ERP

A small web-based manufacturing ERP for Cascadia Seaweed's liquid kelp extract
(LKE) process. Tracks stabilized kelp inventory, production runs, finished
goods, consumables, and prints barcode tracking labels.

Built dependency-free in the same shape as the other apps in this repo: pure
Python standard library (sqlite3 + http.server) for the backend, vanilla JS for
the web UI. **No `pip install` required.**

## Run

```
python kelp_erp_server.py
```
(or double-click `run.bat`) then open <http://localhost:8002>.

Seed login: **admin@kelp.local / kelp1234**

## What it models

The process: ocean-harvested kelp is ground and stabilized with citric acid in
1000 L IBC totes, stored, then later diluted to a target TDS, preserved with
citric acid + potassium sorbate, and bottled as finished Liquid Kelp Extract.

| Area | What it does |
|------|--------------|
| **Dashboard** | Stabilized totes & kg on hand, finished-goods litres, low-stock alerts, recent runs |
| **Stabilized Inventory** | Every IBC tote as a lot. **Check in a harvest batch** → enter total kg + tote count and the system averages the weight across totes and auto-generates lot numbers |
| **Production** | Pick stabilized totes, set a target TDS, add citric/sorbate, define the packaged output (IBC / 4L / 1L / 250ml). Consumes the totes, draws down consumables and empty IBCs, and creates finished-goods lots under an auto-generated Processing Lot # |
| **Finished Goods** | Two SKUs (Saccharina LKE, Macrocystis LKE) on hand by package size; edit qty / status / location |
| **Consumables** | Citric Acid, Potassium Sorbate, empty IBC totes — receive / use, reorder alerts |
| **Labels** | Code128 barcode tracking labels for any tote or finished-goods lot, print-ready 2-up |

## Lot numbering (matches the inventory spreadsheet)

- **Stabilized tote:** `SITE-SPECIES-YYYYMMDD-TOTE` — e.g. `JAM-SL-20260504-003`
  (James Island, *Saccharina latissima*, checked in 2026-05-04, tote 003).
- **Processing lot:** `PR-YYYYMMDD-NNN` — e.g. `PR-20260616-001`.
- **Finished-good lot:** `<processing lot>-<pack>` — e.g. `PR-20260616-001-IBC`.

## Seed data

`seed.json` was extracted from `202605 Inventory.xlsx` (species, farm sites, the
394 stabilized Sugar Kelp totes from the 2025/2026 Fresh Inventory tabs, and
consumable on-hand quantities). On first run the database (`kelp_erp.db`) is
created and seeded automatically.

## Configuration (environment variables)

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8002` | HTTP port (hosts inject this) |
| `KELP_ERP_DB` | `./kelp_erp.db` | sqlite database file |
| `KELP_ERP_UPLOADS` | `./uploads` | folder for attached documents |
| `KELP_ERP_SECRET` | dev value | token signing secret — **set before hosting** |
| `KELP_ERP_ADMIN_EMAIL` / `KELP_ERP_ADMIN_PASSWORD` | `admin@kelp.local` / `kelp1234` | first-run admin account |

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
