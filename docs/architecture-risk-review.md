# KelpWorks architecture and risk review (phase 1)

Date: 2026-10-09. Code reviewed: `main` at `c1e5343` (after PRs #4 to #7). Scope: the nine focus areas below.

## How to read this

- **Method.** Four reviewers read the code in parallel, each owning some focus areas, and wrote reproduction scripts against **throwaway servers** (the `tests/harness.py` server on a temporary database). Maintainability (area 8) was measured with scripts over the source. Nothing was run against live data and no code was changed.
- **Verification.** Ten of the most important findings were **re-run independently** after the reviewers reported (marked **R** = reproduced by me, over HTTP, on a throwaway server). Findings the reviewers reproduced but I did not re-run are marked **RV**. Findings from code reading only are marked **C**. Confidence is stated per finding.
- **Severity.** Critical = can corrupt production inventory or release records, or lets someone release or ship product they should not be able to. High = a real exposure or bug that is likely to matter in normal use. Medium = real but limited, or needs unusual conditions. Low = hygiene.
- **Effort.** S = under a day, M = a few days, L = a week or more.
- **Not examined.** The live database and anything only visible in the Render dashboard (snapshot settings, proxy timeouts, SIGTERM grace period, which environment variables are actually set). Items that depend on those are flagged **confirm in Render**.

## Remediation status

| Finding | Status |
|---|---|
| R-01 finished-goods edits | **Fixed** (batch 1): permissions, status whitelist, reason, audit event |
| R-02 lab results | **Partly fixed** (batch 1): Quality Manager only; released product is held when a required result is voided or a new failure appears. Still open: required attachment / second-person check, wider gate scope (R-11) |
| R-03 tote reuse | **Fixed** (batch 1): ownership check at draft save and finalize; tote status/weight edits restricted |
| R-04 secrets and defaults | **Fixed** (batch 2): no public secret (generated key file), no default passwords outside development, must-change enforced by the server, no prefilled login. Still to do in Render: confirm `KELP_ERP_SECRET` / `KELP_ERP_ADMIN_PASSWORD` are set on live and staging |
| R-12 token revocation | **Mostly fixed** (batch 2): token versions end sessions on password change / reset / deactivation; downloads use the same authentication as the API. Still open: `?token=` is a full-scope bearer token (short-lived download tokens) |
| R-13 login | **Fixed** (batch 2): throttling and no timing difference for unknown emails |
| R-05 stored XSS | **Fixed** (batch 3): files are served by their name's type only (non-PDF/image = download), script-capable types refused, print windows escape their data, a Content-Security-Policy on the app page |
| R-25 uploads | **Mostly fixed** (batch 3): header injection and non-ASCII filenames, total size limit and disk-free check, files removed when a draft is discarded or a request fails. Still open: per-user quota |
| R-08 logging | **Fixed** (batch 4): tracebacks and one line per API request go to the Render log (tokens redacted), a boot line with database size and counts, users see a short reference instead of raw errors |
| R-09 request limits | **Fixed** (batch 4): 40 MB body cap (8 KB for login) refused before reading, 30 s socket timeout |
| R-10 parsing bombs | **Fixed** (batch 4): bounded unpacking of docx / xlsx / pdf, capped previews |
| R-07 migrations | **Fixed** (batch 5): one transaction for the whole upgrade (a failed or killed start rolls back), a compressed snapshot before new code migrates an existing database, repairs as soft steps |
| R-16 / R-17 | **Fixed** (batch 5): one-time changes run once (`run_once`); a new database gets its reagent types |
| R-24 shutdown | **Fixed** (batch 5): SIGTERM finishes requests in progress and exits cleanly (verified on the Linux CI) |
| R-29 migration tests | **Fixed** (batch 5): an old release is run with real data and upgraded; failed-start, snapshot, and soft-step behaviour are tested |
| R-26 duplicate packaging rows | **Fixed** (batch 7): finalize adds rows up to one total per container; every database rule a request trips is a 409 with a plain message |
| R-27 drifting totals | **Mostly fixed** (batch 7): the accepted / rejected decision is locked after finalize, `edit_run` no longer changes reagent totals, finalize refuses a draft whose locked totes are missing from the request, NaN / infinity / negative quantities are refused. |
| R-28 tote delete | **Partly fixed** (batch 7): a tote that appears in a run or pre-processing record cannot be deleted (409). Still open: the stability log and photos of a tote with no run history are deleted with it, and a Fine tote leaves no disposal record |
| R-30 indexes | **Fixed** (batch 7): every foreign-key column is indexed (automatically, including future tables), plus `consumable_txns(ref)` and the status columns used by the lists |
| R-31 bad data | **Partly fixed** (batch 7): triggers refuse an unknown status on totes / finished goods / runs / pre-processing batches and negative finished-goods units. Still open: `*_id` columns without foreign keys (cannot be added to existing tables without a rebuild) |
| R-32 volume drift | **Partly fixed** (batch 7): a run's output litres follow the litres each finished-goods lot was created with, and the IBC count works with the real container name. Still open: pre-processing pack-out is not reconciled; the re-hash of signed logs does not record what changed |
| R-06 shipments | **Fixed** (batch 1): duplicate lines, un-cancel, disposed-lot return, conditional deduction, write lock |
| R-14 edit under amendment | **Fixed** (batch 1) |
| R-15 concurrency | **Fixed** (batches 1 and 7): writes take the lock up front and wait up to 30 s; a unique-number collision is a 409 |
| all others | Open |

## Executive summary

The application's core design is sound where it was designed carefully: each request runs in one transaction with rollback on error, the release workflow re-checks the signer's password and enforces states server-side, last-admin protection works, all SQL is parameterized (no injection found), the staging restore cannot run on live, and every old database in `tests/legacy_commits.txt` upgrades cleanly.

The serious problems come from one pattern: **the server trusts any logged-in user for operations that decide whether product is releasable or how much stock exists**, and several write paths have no transaction guard, no state whitelist and no audit record. In priority order:

1. **Finished-goods lots and lab results are open to any user** (R-01, R-02). A plain user can set any status, quantity or TDS on a finished-goods lot and can enter the lab results that satisfy the release gate. Neither leaves an audit record. This undermines the release workflow, which is otherwise well built.
2. **A tote can be consumed twice** (R-03), and **shipments can oversell** (R-06). Both create stock or product from nothing.
3. **Default secrets** (R-04): if `KELP_ERP_SECRET` is ever unset, anyone with the source can forge an admin token. **Confirm in Render that it is set on live.**
4. **Stored cross-site scripting** (R-05): a user can upload an HTML file that runs with an admin's login token when the admin clicks View, and unescaped fields reach three print windows.
5. **Operations** (R-07, R-08, R-09, R-10): migrations are not atomic and a failed boot can silently skip data fixes; the server writes no logs at all; one unauthenticated request can exhaust memory; small crafted files can pin the server for seconds.

None of this needs a rewrite. Most fixes are small and local; the larger structural items (a migration framework, splitting the 11k-line backend, a permissions model) are listed separately in the plan at the end.

### Counts

| Severity | Findings |
|---|---|
| Critical | 3 (R-01 to R-03) |
| High | 9 (R-04 to R-12) |
| Medium | 17 (R-13 to R-29) |
| Low | 6 (R-30 to R-35) |

## Ranked findings

`Src` is the reviewer's original ID (S = startup/ops, D = data model/inventory, Q = quality/authorization, X = security/concurrency).

| Rank | Sev | Finding | Src | Verified | Effort |
|---|---|---|---|---|---|
| R-01 | Critical | Any user can rewrite finished-goods lots (status, qty, TDS), unaudited | Q2, D4 | R | M |
| R-02 | Critical | Lab results (the release-gate input) can be entered or voided by any user; changes after release do not re-gate | Q1, Q7 | R | M |
| R-03 | Critical | A consumed tote can be taken by a second run or a pre-processing batch | D1, Q13, D4 | R | S |
| R-04 | High | Default signing secret and default credentials; must-change-password not enforced by the server | Q3, S7, X6, X12 | R | S |
| R-05 | High | Stored XSS: uploads served inline from the app origin; unescaped print windows | Q4, X1, X2 | R (upload), C (print) | S |
| R-06 | High | Shipments can oversell: duplicate lines, un-cancel, concurrent requests | D2, X3, Q15, D5 | R (duplicate lines) | S/M |
| R-07 | High | Migrations are not atomic; a failed or killed boot skips backfills for good; no pre-migration snapshot | S1, S2 | RV | M |
| R-08 | High | No server-side logging; 500s echo raw exception text | S3, X12 | C | S |
| R-09 | High | Unauthenticated memory/thread exhaustion (no body cap, no socket timeout) | X4, S4 | R | S |
| R-10 | High | Parsing bombs: a tiny xlsx/docx/pdf can pin the server for seconds and return 100+ MB | X5 | RV | S |
| R-11 | High | Release gate is narrow: only 5 microbial tests block; metals/TDS/pH never do; a retest hides a failure | Q5 | RV | S/M |
| R-12 | High | Tokens never revoked; `?token=` downloads skip the active-user check | Q12, X7 | RV | S/M |
| R-13 | Medium | No login throttling; timing reveals which emails exist | X6, Q16 | R | S |
| R-14 | Medium | Finalized run editable under someone else's amendment by any user | Q6 | RV | S |
| R-15 | Medium | Concurrency: lock timeouts return 500s; unique-number collisions surface as errors | X10 | RV | M |
| R-16 | Medium | One-time migration steps re-run every boot and overwrite admin choices | S9 | R | S |
| R-17 | Medium | Fresh database leaves `reagent_type` blank (same class as the fixed FK bug) | S12 | R | S |
| R-18 | Medium | Spec and setting changes are not change-controlled; CoAs change retroactively | Q8 | C | M |
| R-19 | Medium | Audit trail weak: unkeyed chain, no triggers, many actions not logged; a hash-mismatch void is rolled back | Q9, Q10, X11 | RV/C | M |
| R-20 | Medium | Separation of duties not enforced; admin can self-grant roles and reset passwords unaudited | Q11 | RV | M |
| R-21 | Medium | Recall traceability gaps: reagent/packaging lots not recorded; no lot-to-customer report | Q14 | C | L |
| R-22 | Medium | Missing disk can silently produce a fresh seeded database with default admin | S6 | C | S |
| R-23 | Medium | Backup and recovery: manual only, no runbook, temp-space and memory use | S8 | C | M |
| R-24 | Medium | No graceful shutdown; a deploy kill during startup triggers R-07 | S5 | C | S |
| R-25 | Medium | Uploads unbounded on disk; orphan files; header injection and broken non-ASCII filenames | X8, X9 | RV | M |
| R-26 | Medium | Packaging rows with the same container make finalize fail with a 500 | D3 | RV | S |
| R-27 | Medium | Amending feedstock decisions and `edit_run` reagent edits drift totals and stock; negatives accepted | D6, D7, D8 | RV | M |
| R-28 | Medium | Deleting a tote: 500 for referenced totes, silent cascade of its history | D9 | RV | S |
| R-29 | Medium | Migration tests use empty databases, so data-dependent steps are untested | S10 | C | M |
| R-30 | Low | Missing indexes on hot foreign-key columns | D10 | C | S |
| R-31 | Low | Database permits bad data: 2 CHECK constraints in 59 tables; many `*_id` without foreign keys | D15 | C | M |
| R-32 | Low | FG lot volume drifts when container litres change; pre-processing pack-out not reconciled | D11, D12 | C | S |
| R-33 | Low | Rebaseline is one-way and hides divergence; rollback after a snapshot-version bump breaks sign-offs | S11 | C | S |
| R-34 | Low | Small items: dead `ibc_used` stat, NULL sample-point qty, CORS `*`, missing security headers, boot housekeeping | D13, D14, X12, S13 | C | S |
| R-35 | Low | Maintainability: 6,500-line `Handler`, 427-line `migrate()`, dead columns/tables, duplicated code | (area 8) | measured | L |

## Detailed findings

### R-01. Any user can rewrite finished-goods lots (Critical, reproduced by me)
- **Where:** `route_fg` (`kelp_erp_server.py` ~9872; the PUT handler ~9951-9975), `dispose` FG branch (~10853).
- **What:** `PUT /api/fg/:id` takes `status`, `qty` and `tds` from the request body with no role check and no whitelist, and writes nothing to the audit log or the ledger. The only guards are "not into or out of `pending_release`" and "not to `on_hand` while the run is unreleased".
- **Reproduced:** as a plain (non-admin) user on a released run: `PUT {"status":"banana","qty":999}` returned 200 and the lot now holds status `banana`, qty 999. The reviewer also lifted a Quality hold, disposed a lot and moved it back to `on_hand`.
- **Why it matters:** quantity can be inflated and then shipped; a Quality hold can be lifted by anyone; the lot's TDS on the CoA can be falsified. No record shows who did it.
- **Fix:** allow only known status values and transitions; make `qty`/`tds` read-only here (changes go through the packaging/amendment paths or a logged adjustment); require the Quality Manager permission to place or lift a hold; log each change to a chained table. Add a regression test per rule.

### R-02. Lab results can be entered or voided by any user; later changes do not re-gate (Critical, reproduced by me)
- **Where:** `route_lab_results` (~5662-5761), release gate (~8339-8347), `coa_evaluate` (~2154-2215). `lab-results` is exempt from the amend lock and from the release hash (`_LOG_EXEMPT_SUBPATHS`).
- **What:** adding or voiding results needs only a login, a free-text lab name and a report number. No attachment is required. The five microbial tests are the only `required` specs, and their presence is the only thing the release gate checks for, so typed values clear the gate.
- **Reproduced:** a plain user posted a made-up lab with Salmonella "Negative" and plate count "<10" and got 200. The reviewer carried this through to a successful Quality release. After release, adding a Positive Salmonella result or voiding a required result left the run Released and the lots on hand, and a shipment succeeded.
- **Fix:** restrict add/void to a lab-results permission or the Quality Manager; for required tests require an attached lab report; auto-hold the lots and log an event when a failing or voided required result appears on a released run; consider a second-person verification step.

### R-03. A consumed tote can be taken by a second run (Critical, reproduced by me)
- **Where:** `_apply_tote_characterization` (~8772-8830, called by `save_draft`), `_finalize_run` tote check (~9576), `route_preproc` inputs, `PUT /api/totes/:id` (~5444).
- **What:** saving a draft sets `status='wip', run_id=<this run>` on any tote id without checking its current status, and finalize accepts `wip` without checking which run owns it. `PUT /api/totes/:id` accepts any status, so a consumed tote can also simply be flipped back to `in_stock`.
- **Reproduced:** after run 1 consumed tote T, a second run drafted and finalized with T. `run_inputs` then shows T accepted in two runs. The reviewer showed the used-IBC pool gains +2 for one physical tote, the yield report attributes the tote to only one run, and the same hole exists between runs and pre-processing batches (a tote consumed into blend lots was later consumed by a run).
- **Fix:** in `_apply_tote_characterization` require `status='in_stock'`, or `wip` and owned by this run, else 409; in `_finalize_run` require ownership; whitelist tote status transitions in the PUT; optionally add a partial unique index on accepted `run_inputs(tote_lot_id)`.

### R-04. Default secret and credentials (High; Critical wherever `KELP_ERP_SECRET` is unset) (reproduced by me)
- **Where:** lines ~79-93 (`DEV_SECRET`, `ADMIN_PASSWORD="kelp1234"`, `INITIAL_USER_PASSWORD="Cascadia123!"`), `index.html` (login form pre-filled with the admin default), `login` (~4949), `_auth`.
- **What:** with no `KELP_ERP_SECRET` the server signs tokens with a public string and only prints a warning. The seed admin and the three staff accounts use passwords that are in the repository. `must_change_password` is enforced only by the browser: a user who must change their password can use the whole API.
- **Reproduced:** with the default secret I forged a token for user id 1 and got `GET /api/users` = 200.
- **Live status:** `render.yaml` generates the secret (good), but it only sets `sync: false` for the admin email/password and does not set `KELP_ERP_INITIAL_PASSWORD`. **Confirm in Render** that `KELP_ERP_SECRET`, `KELP_ERP_ADMIN_PASSWORD` are set to strong values on live and staging, and that the three staff accounts have all changed their first password.
- **Fix:** refuse to start in production when the secret or admin password is a default; generate a random initial password per user and print it once; return 403 `password_change_required` from `_auth` for everything except the password change; remove the pre-filled login values.

### R-05. Stored XSS via uploads and print windows (High; upload side reproduced by me)
- **Where:** `_store_attachment` (~8559) and `_download_attachment` (~4606, headers ~4634); `printPackingSlip` (app.js ~4455), `printReport` (~5120), `printLabels` (~6947).
- **What:** any user can upload a file with `contentType: text/html`. The download serves it back with that type and `Content-Disposition: inline` from the app's own origin, and the "View" link carries the viewer's token in the URL. The login token lives in `localStorage`, so the script can read it. Separately, three print windows build HTML with unescaped customer names, addresses, notes, tracking numbers, locations and lot text, then `document.write` it into a same-origin window.
- **Reproduced:** a plain user uploaded `evil.html` as `text/html`; the download returned `Content-Type: text/html`, `Content-Disposition: inline` with the script intact. The script-runs-in-a-browser step and the print-window cases are **C** (code reading).
- **Fix:** serve every upload with a server-chosen type from an allow-list (pdf, png, jpg, gif) or as `application/octet-stream` with `attachment`; reject html/svg/js on upload; escape every interpolated value in the three print windows (the label and release-record printers already do); add a CSP.

### R-06. Shipments can oversell (High; duplicate-line case reproduced by me)
- **Where:** `create_shipment` (~10080-10125), `update_shipment` (~10130-10160), FG edit/move paths.
- **What:** (a) lines are validated against the stock before any deduction, so the same lot on two lines passes twice; (b) cancelling then un-cancelling adds or removes quantity without checking `qty >= 0`; (c) the read-check-write is not in a write transaction, so concurrent shipments race; (d) cancelling a shipment after the rest of the lot was disposed strands the units in a `disposed` lot.
- **Reproduced:** lot of 100; one shipment with two lines of 60 returned 200; 120 units shipped, lot shows 40 on hand. The reviewer also reproduced un-cancel driving a lot to -120 and, for the race, 88 units shipped from a 10-unit lot in one of 15 parallel trials.
- **Fix:** aggregate lines per lot first; use `UPDATE fg_lots SET qty=qty-? WHERE id=? AND qty>=?` and check the row count; block un-cancel when stock is short; start mutating requests with `BEGIN IMMEDIATE` (see R-15).

### R-07. Migrations are not atomic; no snapshot before migrating (High, reviewer reproduced)
- **Where:** `init_db` (~1411), `migrate` (~3535-3961), `main` (unguarded `init_db()`).
- **What:** `ALTER TABLE ADD COLUMN` runs outside a transaction, but the data backfill that follows is inside one that commits only at the end. If the boot dies later, the column stays and the backfill is rolled back, and the next boot sees the column and skips the backfill forever. The `qc_logs` rebuild creates `qc_logs_new` outside a transaction, so a crash leaves a table that makes every later boot fail. Every boot also loops over all runs, and one bad row stops the process, so Render restarts it in a loop and the site stays down. No copy of the database is taken first.
- **Reproduced (reviewer):** with `can_amend_log` dropped and a later step forced to fail, the Production/Quality managers ended with `can_amend_log=0` after a second boot.
- **Fix:** copy the database to `/var/data/backups/pre-migrate-<n>.db` (sqlite backup API) before migrating when the schema version changes; run `migrate()` in one explicit transaction, or record each data backfill in `app_flags`/a version table so it is retried until it commits; `DROP TABLE IF EXISTS qc_logs_new` first; wrap the non-schema steps (sample-ID refresh, QC backfill, `sync_pending_samples`) in try/except that logs and continues.

### R-08. No server-side logging (High for operations)
- `log_message` is a no-op and `_handle_api` turns any exception into `{"error": "Server error: <text>"}` with no traceback or log line. There is no `logging` import. An operator cannot see why a request failed, and users see raw exception text. **Fix (S):** log the traceback and one access line to stderr (Render captures it); return a generic message to the client; log a boot banner with DB path, size and counts.

### R-09. Unauthenticated memory and thread exhaustion (High, reproduced by me)
- `_body_json` reads `Content-Length` bytes with no cap, and `/api/auth/login` reads before any auth; `MAX_UPLOAD_BYTES` is checked only after the whole base64 body is in memory. There is no socket timeout.
- **Reproduced:** a 30 MB unauthenticated body to the login URL was read in full and answered 401 (the reviewer did 200 MB). On a Starter instance (512 MB) a few of these exhaust memory.
- **Fix:** cap the body (about 8 KB for login, about 40 MB elsewhere), set `Handler.timeout = 30`, stream uploads.

### R-10. Parsing bombs (High, reviewer reproduced)
- `xlsx_to_html`, `docx_to_html`, `docx_fill`, `pdf_text_lines` have no limit on uncompressed size or sheet dimensions. A 483-byte xlsx with one cell at `ZZ20000` took 12 s and returned a 126 MB response; a 306 KB docx with a 300 MB part took 7 s and returned 315 MB. Reachable by any logged-in user through the attachment preview.
- **Fix (S):** reject archive members over about 20 MB uncompressed, cap rendered rows/columns, and use bounded `decompressobj` for PDF streams.

### R-11. The release gate is narrow (High, reviewer reproduced)
- Only the five microbial specs are `required`. Heavy metals are "not evaluated" while `coa_application_rate_kg_ha` is 0 (its default) and "review"/"not evaluated" never block. TDS/pH are not release-blocking. A failed result needs only any non-empty comment. The latest non-voided result per spec wins, so a Negative entered after a Positive hides the failure without anyone voiding it.
- **Fix:** decide which specs must pass; make "review"/"not evaluated" block or require an explicit acknowledgement; require a void and a reason before a failed row can be superseded.

### R-12. Tokens are never revoked (High/Medium, reviewer reproduced)
- After a password change, an admin reset or deactivation, existing tokens keep working. A deactivated user's token still downloads reports, yield-usage, lab templates, attachments and SOPs for up to 12 hours because those routes call only `read_token()`, not `_auth()` (the backup route is correct). The `?token=` value is the full-power bearer token and lands in URLs, history and logs.
- **Fix:** add a per-user `token_version` claim bumped on password change/deactivation; route every download through one auth helper that loads the user; replace `?token=` with a short-lived, path-bound download token.

### R-13 to R-35 (condensed)
- **R-13 login:** 25 wrong logins never locked out (reproduced); known email 0.16 s vs unknown 0.02 s (reproduced); each failed guess costs about 0.28 s of CPU. Add a per-IP/per-email counter with backoff and a dummy hash for unknown users.
- **R-14:** `edit_run` ignores the amend permission, so any user can change a finalized run's notes, location and citric acid (which adjusts stock) while another person's amendment is open.
- **R-15 concurrency:** no `busy_timeout`/`BEGIN IMMEDIATE`. A writer holding the lock over 5 s gives others `500 database is locked`; 12 parallel harvests produced 7 `500 UNIQUE constraint failed` (data stayed consistent). Set `PRAGMA busy_timeout`, start writes with `BEGIN IMMEDIATE`, return 409 on unique collisions. The audit chain can also fork if two signers race (X11).
- **R-16 (reproduced by me):** migrations re-run every boot and overwrite choices: `preproc_target_solids_pct=10` is reset to 50 on each restart; metal spec units are reverted; retired container units are re-inserted and re-merged, growing the sequence each boot. Gate one-time changes behind `app_flags`.
- **R-17 (reproduced by me):** on an empty database `migrate()` adds `reagent_type` before `seed()` inserts the reagents, so Citric Acid, Potassium Sorbate and Sodium Benzoate stay NULL. Deduction still works by name today, but item pickers and validation depend on it. Run the backfill after seed.
- **R-18:** `coa_specs` and `settings` can be changed by an admin with no log or versioning, which retroactively changes every CoA. Snapshot specs at release and log changes.
- **R-19:** the audit chain is an unkeyed SHA-256 (anyone with database write access can recompute it), has no triggers against UPDATE/DELETE, no external anchor, and a broken chain blocks nothing. `run_revisions`, `run_edits`, `user_permission_log`, `consumable_txns`, FG edits, disposals, shipments, spec changes and password resets are not chained. When the log hash mismatches at release, the "void" is rolled back by the error it raises, and `CLAUDE.md` describes a `_release_reconcile` that does not exist in the code.
- **R-20:** the same person can finalize, review, enter lab results and release. An admin can grant themselves the Production/Quality/amender permissions and reset anyone's password with no audit entry beyond `user_permission_log`.
- **R-21 traceability:** backward trace from an FG lot to run, totes, site, species and harvest date works. Reagent, packaging and label lots (supplier, receipt, expiry) are not recorded, there is no lot-to-customers recall report, and renaming a customer rewrites history.
- **R-22:** if the disk fails to mount, the app starts on the container's ephemeral filesystem with a freshly seeded database, the default admin, and 394 seed totes shown in stock, and passes the health check. Require a marker file on the volume in production.
- **R-23:** backups are manual downloads; no runbook for restoring live (restore is intentionally staging-only); the zip is built in `/tmp` before any byte is sent; the plain `.db` download holds the database in memory twice; copying a `.db` over a live path without removing `-wal`/`-shm` risks a stale WAL. Add a scheduled snapshot with rotation plus an off-box copy and a written runbook. **Confirm in Render** that disk snapshots are enabled.
- **R-24:** Python is PID 1 with no SIGTERM handler, so Render waits out the grace period then kills; a kill during startup is the R-07 trigger. Handle SIGTERM and run under a minimal init.
- **R-25:** `contentType` containing CRLF injects real response headers (reproduced by the reviewer); a filename with a non-latin-1 character such as an en dash raises mid-response and corrupts the download; no per-user or total upload quota; files are written before the database row and never deleted when a draft is discarded.
- **R-26:** two packaging rows with the same container make finalize return `500 UNIQUE constraint failed: fg_lots.fg_lot_number` (rollback is clean; the message is cryptic). Group by unit at finalize, as the post-finalize sync already does.
- **R-27:** amending a feedstock decision after finalize does not recompute `input_kg`, the used-IBC delta or the tote status; `edit_run` charges reagent deltas by item name outside `run_reagent_commits` and accepts negative and non-finite numbers; a finalize body that omits a draft-locked tote leaves it stuck. Recompute derived values in the same transaction and validate numbers.
- **R-28:** `DELETE /api/totes/:id` returns 500 for a referenced tote and silently cascades away the stability log and photos of an unreferenced one; a Fine tote can vanish with no disposal record. Pre-check and return 409.
- **R-29:** the migration tests build old databases by booting old code on an empty database, so runs, samples, release events and QC logs never exist and the data-dependent steps (legacy release stamp, `decision_set`, QC backfill, rebaseline, sample-ID refresh) are untested. Add a populated fixture.
- **R-30 to R-34:** unindexed foreign-key columns (`run_inputs`, `fg_lots(run_id,status)`, `consumable_txns(consumable_id, ref)`, `shipment_lines`, `tote_lots(run_id, preproc_batch_id)` and others); only 2 CHECK constraints across 59 tables and `fg_lots.status` accepts any text; FG lot litres are not snapshotted; rebaseline erases divergence without recording it; plus small items (`ibc_used` is always 0 after the container rename, CORS `*` on every response, no CSP/frame/HSTS headers, `seed()` swallows a `seed.json` read error).

## Maintainability (focus area 8, measured)

| Measure | Result |
|---|---|
| Backend | `kelp_erp_server.py`: 11,336 lines; one `Handler` class of 6,498 lines and 217 methods; 402 functions in total |
| Longest backend functions | `migrate` 427 lines, `route_yield_usage` 199, `build_run_summary_pdf` 194, `_finalize_run` 190, `route_totes` 188; 14 functions over 100 lines |
| Frontend | `app.js`: 7,492 lines, 235 top-level functions in one file; `openRun` about 416 lines, `exportYieldCsv` 285, `openPreprocBatch` 244; 65 `innerHTML` assignments |
| Schema | 59 tables in a 993-line `SCHEMA` string; `migrate()` is the only upgrade mechanism (no version history of steps); 29 indexes (27 of 59 tables have any) |
| Dead or retired | tables `tote_ph_log`, `run_separation_solids` never referenced in code; retired columns kept (`short_id`, `rinse_ph`, `rinse_conductivity_us`, legacy `species_code` FK); JS `monthLabel` unused; `ibc_used` never non-zero |
| Duplication | the token-from-header-or-query block appears 7 times; 22 distinct duplicated 8-line blocks; 26 broad `except Exception` handlers (9 are `# pragma: no cover` catch-alls that hide faults) |
| Positive | no TODO/FIXME debt; no unreferenced backend functions; routes dispatch from one table (`_route`) |

**Recommended boundaries (standard library only, no framework):** split the backend into modules without changing behaviour: `config.py`, `db.py` (connection, transaction helper), `schema.py`, `migrations.py` (an ordered list of named one-time steps recorded in a `schema_migrations` table, replacing the 427-line function and fixing R-07/R-16/R-17 together), `auth.py` (one `authenticate()` used by every route including downloads, fixing R-12), `permissions.py` (a table of route -> required permission, fixing R-01/R-02/R-14/R-20 centrally), and one module per domain (`inventory`, `production`, `release`, `samples`, `reports`, `files`). Do this behind the existing test suite, one module per PR. For the frontend, move `app.js` to several plain script files by page and add one `esc()` helper used by every HTML template.

## What was checked and found sound

- **SQL injection:** every dynamically built statement was traced; column names come from constants, `IN` lists use placeholders, `ALTER`/`PRAGMA` use constants. None injectable.
- **Atomicity of requests:** one commit per request with rollback on any error; a failed finalize leaves no residue; draft discard refunds stock exactly.
- **Release workflow states:** signatures re-check the signer's password; wrong-state release returns 409; non-managers cannot review; opening an amendment on a released run moves lots back to pending release; shipping a pending-release lot is refused.
- **Authorization basics:** role and active status are re-read on every normal API call; privilege escalation through `/api/users` is blocked for non-admins; last-admin demote/deactivate is refused; admin routes return 403 to plain users.
- **Files:** upload storage names are random (no path traversal); static serving rejects traversal; docx/xlsx previews are escaped and shown in a sandboxed iframe; the staging restore validates paths, integrity and environment.
- **Schema parity:** all eight legacy versions upgrade to the same tables, columns and indexes as a fresh database, and boot twice with no content change.
- **Ops:** the sqlite backup API gives a consistent snapshot under load; no in-memory session state, so the single-instance constraint is the only scaling limit; boot of 300 runs took 0.05 s.

## Suggested remediation plan

Each item below is one pull request on a `hardening/` branch with tests that fail before the fix.

1. **Release-integrity batch (R-01, R-02, R-03, R-06, R-14).** Server-side permission checks and status/quantity whitelists on FG, lab results, totes and shipments; tote ownership checks; shipment line aggregation and conditional updates; audit rows for each. Turn the reproductions into regression tests first.
2. **Secrets and sessions (R-04, R-12, R-13).** Refuse default secrets in production; enforce must-change-password on the server; token versions; one auth helper for downloads; login throttling.
3. **XSS and uploads (R-05, R-25).** Allow-listed content types with attachment disposition; escape the print windows; a CSP; filename and header handling; upload quota.
4. **Request limits and logging (R-08, R-09, R-10).** Body caps, socket timeout, bounded parsing, traceback logging, generic error messages.
5. **Migration safety (R-07, R-16, R-17, R-24, R-29).** Pre-migration snapshot, transactional migrations with a version table, one-time steps gated, SIGTERM handling, a populated migration fixture.
6. **Release gate policy (R-11, R-18, R-19, R-20, R-21).** Needs a decision from Quality first: which specs must pass, who may enter lab results, whether separation of duties is required, and what a recall report must show. Then implement.
7. **Data-integrity cleanup (R-26, R-27, R-28, R-30, R-31, R-32).** Smaller fixes and indexes.
8. **Structure (R-35).** Split the backend into modules and the frontend into page files, one module per PR, after the items above so the tests are in place.

Quick check before any of this: **confirm in Render** that `KELP_ERP_SECRET` and `KELP_ERP_ADMIN_PASSWORD` are set to strong values on live and staging, that disk snapshots are enabled, and that the three staff accounts have changed their first password.

## Appendix: endpoint permissions as implemented

Observed by probing 82 routes as anonymous, plain user and admin on a throwaway server.

| Requirement | Routes |
|---|---|
| None | `GET /api/env`, `POST /api/auth/login`, static files |
| Any logged-in user | `/api/me`, `POST /api/me/password`, refdata, settings (GET), dashboard |
| Any logged-in user | totes (all methods incl. PUT, DELETE, move, ph, characterize), harvest, pre-processing, samples and cart, requisitions (GET), labs (GET) |
| Any logged-in user | consumables (GET, POST reagent, adjust, PUT), CIP (GET, POST, PUT), customers (all), shipments (all) |
| Any logged-in user | **FG (all methods incl. PUT and move)**, dispose, disposals, ledger, reports, yield-usage, QC charts (GET), requisition contact (GET), sample label names (PUT) |
| Any logged-in user | **all `/api/production/...`**: drafts, finalize, stages, attachments (incl. DELETE), **lab results add/void/scan**, edit run; release (GET, verify, resubmit) |
| Any logged-in user, token also accepted in `?token=` (no active check) | downloads: attachments, SOPs, lab templates, reports and yield-usage xlsx, summary pdf, CoA pdf |
| Admin | users (all), user password, settings (PUT), SOP documents (POST, PUT, DELETE), labs and their analyses/templates, CoA specs (PUT) |
| Admin | requisition contact (PUT), consumables bulk, container, label and CIP-agent creation, CIP delete |
| Admin (active checked) | `GET /api/admin/backup` (and `?full=1`) |
| Admin, staging only | `POST /api/admin/restore` (403 on production) |
| Admin or Quality Manager | integrity check and repair (repair also needs a password), QC chart limits (PUT) |
| Production or Quality Manager (+ password) | release: review |
| Quality Manager only (+ password) | release: approve or reject, reopen |
| Amender (+ password if reviewed or released) | open an amendment |
| Opener, amender, manager or admin | amendment submit and cancel |
| Amender | any other write to a finalized run while an amendment is open (except edit run, R-14) |
