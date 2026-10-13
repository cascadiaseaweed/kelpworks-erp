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
6. **Check.** Compare each item's on-hand quantity with a physical count; correct any difference with a stocktake (Use / Receive, reason "Stocktake").
   Run Admin > **Data integrity check**.

## What a historical run looks like afterwards

- Lot number and run date are the originals; "finalized" is stamped at 12:00 on the run date, so the stabilization period, the yield report's processing month and the archive folder
  (`02_Production-Runs/2026/2026-06/...`) are right. The date it was **entered** is in the release history and the run's tooltip.
- A grandfathered run is not in the Product Release queue and is never re-held by a lab result added later. A run sent for normal release behaves like any other from then on.
- It can be amended later like any run, to fill in what was missing (Production > Amend run).
