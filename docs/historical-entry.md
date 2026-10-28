# Entering past production runs

For runs that already happened (before KelpWorks went live): they keep their **original processing lot and run date**, their log does not have to be complete, and you choose for
each one whether it moves inventory and whether it goes through release. Only an administrator can do this, and only while the setting is on.

## Switch it on, and off

Calculations page > **Variables and values** > **Historical run entry: administrators may enter past runs (1 = on)** > set `1` > Save.
A **+ Historical run** button appears on the Production page. **Set it back to `0` when the history is entered**; every change is logged.

## Before you start

1. **Back up** (Admin > Records archive & backups > Back up now). Rehearse on staging first if you can (restore the clean backup there and enter two runs).
2. Make a list of the runs, oldest first: original processing lot, run date, product, the totes, the packaging and output, whether you have the lab results, whether you have the
   deliveries (supplier lots) it used, and whether it was shipped (customer, date).
3. Enter **customers** (Shipping > Customers) for any shipments you will record.

## The order

1. **Totes.** Feedstock Inventory > Check-in, with the **real harvest date and received date** (the tote lot number is built from the harvest date). Only totes a run consumed, plus
   what you hold now, need to exist. Check a run's totes in before you create the run: they are consumed by it.
2. **Deliveries** (only for runs you will "count"). Inventory Items > Receive, one lot per delivery, with the supplier's lot number and the **real received date**. Oldest first.
   A historical run draws only on lots received **by its run date**, earliest expiry first, judging expiry as of that date.
3. **The runs, oldest to newest.** Production > **+ Historical run**:
   - **Original processing lot** and **Run date**: exactly as on the labels and paperwork (for example `PR-20260506-012`). A lot number can be used once.
   - **Inventory:**
     - *Count it*: deducts stock and assigns supplier lots as of the run date. Use it only when you entered the deliveries (step 2) it used.
     - *Leave inventory alone*: no stock, ledger or lot effect, so the counts you entered today are not distorted. The run shows "no lot recorded" for its materials.
   - **Release:**
     - *Normal review and Quality release*: the run waits for the Production Manager's review and the Quality Manager's release through the gate (metals, microbial, TDS),
       like any run. Use it when you have the lab results and the product is still in stock.
     - *Grandfathered*: released as entered; no review, no gate. Recorded in the audit trail as "Entered retrospectively". Use it for product that was already released and sold.
   - Then fill in what you have (feedstock, stages, packaging; in a historical run **sample points are not added for you**, enter only the retention samples that exist) and press
     **Finalize run**. Required fields are not enforced; what was missing is recorded on the run (the **Historical** badge shows it).
4. **Lab results** for the runs sent to normal release: Production > Lab results (attach the lab report), then Product Release for the review and release.
5. **Shipments.** Shipping > New shipment, with the **real ship date**, for lots that are on hand (grandfathered runs, or released ones).
6. **Check.** Compare each item's on-hand quantity with a physical count; correct any difference with Adjust > Count on the item (the count, the reason and your name go to the item's history).
   Run Admin > **Data integrity check**.

## What a historical run looks like afterwards

- Lot number and run date are the originals; "finalized" is stamped at 12:00 on the run date, so the stabilization period, the yield report's processing month and the archive folder
  (`02_Production-Runs/2026/2026-06/...`) are right. The date it was **entered** is in the release history and the run's tooltip.
- A grandfathered run is not in the Product Release queue and is never re-held by a lab result added later. A run sent for normal release behaves like any other from then on.
- It can be amended later like any run, to fill in what was missing (Production > Amend run).
- The Data integrity check lists the runs entered this way under **Required fields** as an information line ("N finalized run(s) are missing required production-log fields"). That is expected: the missing values are the ones the paper log did not show, and they are listed in each run's notes. It is not an error; the line goes away for a run only if it is completed by an amendment.

## Loading many runs with the import tool (KW-64)

`tools/import_historical_runs.py` enters a batch of past runs from a prepared **entry workbook** (one sheet per paper lot, in the order of the KelpWorks screens) through the same
"+ Historical run" path described above. It uses the Python standard library only. The workbook, the photos folder, the **mapping file** and the **report** hold production data: keep them
**outside the repository** (never commit them).

What it does and does not do:

- It never fills a gap with a default. A cell marked `(not recorded)` or left blank is not sent; it is written into the run's notes ("NOT RECORDED on the paper log ...") together with the workbook's
  assumptions, the items it flagged to check and any paper value that has no KelpWorks field. A cell still marked `(decide)`, a label it has no mapping for, a missing photo, a missing tote or a
  choice not yet made in the mapping file stops the load (`--check` lists them all).
- It does **not** load totes. The feedstock totes must already be in Feedstock Inventory (bulk import) and in stock; the tool finds each one by its lot number and attaches it to the run. It does not
  switch the setting `historical_entry_enabled` on or off: you do (Calculations page), on the site you are loading, and switch it off afterwards.
- It creates the runs oldest first. A run that fails before it is finalized is discarded (its stock and totes go back as they were), the load stops, and a re-run after the fix skips what is already
  there. A discarded draft still uses up a run number, so after the load create and discard a normal draft to see which number the next normal run will get.
- It names each run `PR-<run date>-<sequence>` (change `lotNumberFormat` in the mapping file to use another format; `{date}` is the run date, `{seq}` the order of creation).
- After each run it reads it back from the site and compares it with the workbook's **Overview** sheet (totes, measured feedstock kg, dilution passes, IBCs, output L, final pH and TDS, extraction
  efficiency, run date, release state): `PASS` or `DIFFERENCE`. Every difference must be explained before sign-off. The report is a CSV you keep with the workbook.
- It refuses to run on the wrong kind of site: `--expect staging` or `--expect live` is compared with what the site says it is (`GET /api/env`). Loading live also needs `LOAD LIVE` typed at the prompt;
  `--yes` only skips the prompt on staging. The password is typed at the prompt (or in the environment variable `KELP_ERP_IMPORT_PASSWORD` for tests) and is never written to a file.

The mapping file (see `docs/historical-import-mapping.example.json`) holds the choices the paper log cannot give: the product (`sku`) of each lot, `release` (`review` or `grandfather`), `inventory`
(`count`, the default, or `leave`), a `toteLots` override for a tote whose number is unclear, and `assumptionsAcceptedBy` once you have accepted the workbook's assumptions.
Grandfather only a run whose product is **entirely sold**; a run with product still on hand goes through normal review and Quality release (its lots stay "pending release" until the lab report is attached,
the Production Manager has reviewed and the Quality Manager has released).

```
py tools/import_historical_runs.py --check     --workbook W.xlsx --mapping M.json
py tools/import_historical_runs.py --preflight --workbook W.xlsx --mapping M.json --target https://<staging> --expect staging --email you@...
py tools/import_historical_runs.py --run       --workbook W.xlsx --mapping M.json --target https://<staging> --expect staging --email you@... --report staging-1.csv
```

### Order: staging first, then live

1. Settle the open decisions in the workbook and the mapping file; run `--check` until it says "No problems", and approve the mapping and the gap list.
2. **Live:** Admin > Download full backup (keep it). **Staging:** Manual Deploy of `main`, then Admin > Restore from a full backup with that file (`docs/staging.md`). Sign in with your live e-mail and the staging password.
3. **Staging:** switch `historical_entry_enabled` on (Calculations page). Run `--preflight`, then `--run --expect staging --report ...`. Look at the runs in the app (original lots, notes, photos, Materials used, item stock),
   run Admin > Data integrity check, try one release (attach the lab report, review, release), and create then discard a normal draft to see the next lot number.
4. Problem found: fix, restore the same backup to staging again (a clean slate) and repeat. When the rehearsal on the final version is clean, sign it off (the report plus your note).
5. **Live:** a new full backup first; check no run has been started since; switch the setting on; `--run --expect live` and type `LOAD LIVE`; check the report and the integrity check; switch the setting **off**.
6. Lab reports, review and release as they arrive; record the shipments of units already sold (real ship dates); Adjust > Count to the physical stock (steps 4 to 6 above).

Rollback: before any normal run exists, restore the pre-load backup (`docs/restore-runbook.md`). After a normal run exists, do not restore: correct the entered runs with amendments.
