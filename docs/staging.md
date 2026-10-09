# Staging: test against a copy of live data

Staging is a second KelpWorks site on Render with its **own database and disk**. It holds a **copy** of the live data, so a change can be tried on realistic
data before it goes live. Nothing done on staging can reach the live site.

| | Live | Staging |
|---|---|---|
| Render service | `kelpworks-erp` | `kelpworks-erp-staging` |
| Updates | automatically when `main` changes | only when you click **Deploy** (so you can try a pull request's branch first) |
| Data | the real data | a copy of live, refreshed on demand |
| Banner | none | amber **STAGING** bar and a `[STAGING]` tab title |
| Restore button | does not exist (and cannot be switched on by mistake) | Admin page |

Cost: a second Starter instance plus a 1 GB disk, about the same as the live service.

## One-time setup (Render dashboard)

Either create it from the blueprint (New + > Blueprint, repo `kelpworks-erp`, **Blueprint file path** `render.staging.yaml`) or create a Web Service by hand
with the same settings:

1. Web Service, this repository, runtime **Docker**, plan **Starter**, name `kelpworks-erp-staging`.
2. **Auto-Deploy: Off.**
3. A disk named `kelpworks-staging-data`, mounted at `/var/data`, 1 GB (make it bigger if live data plus documents grow past roughly 400 MB: the restore needs room for the zip and the extracted files).
4. Environment variables:

| Variable | Value |
|---|---|
| `KELP_ERP_ENV` | `staging` |
| `KELP_ERP_ALLOW_RESTORE` | `1` |
| `KELP_ERP_STAGING_PASSWORD` | a password of your choice (8+ characters). **Every** user gets this password after a restore. |
| `KELP_ERP_SECRET` | any long random value, **different from live** |
| `KELP_ERP_DB` | `/var/data/kelp_erp.db` |
| `KELP_ERP_UPLOADS` | `/var/data/uploads` |
| `KELP_ERP_ADMIN_EMAIL`, `KELP_ERP_ADMIN_PASSWORD` | the staging admin created on the first boot (used only until the first restore) |

**Never set `KELP_ERP_ENV`, `KELP_ERP_ALLOW_RESTORE` or `KELP_ERP_STAGING_PASSWORD` on the live service.** The server only accepts a restore when it is
told it is a staging server, so the live site cannot overwrite itself.

## Refreshing staging from live (each time)

1. **On the live site:** Admin > **Download full backup**. This is one `.zip` with the database and every uploaded document, lab template and SOP. It contains
   password hashes and all business records: keep it private and delete it when you are done.
2. **On Render:** open `kelpworks-erp-staging` > **Manual Deploy** > deploy the branch or commit you want to test (`main` for a plain refresh).
3. **On staging:** sign in with the staging admin, open Admin > **Restore from a full backup...**, choose the zip, type `RESTORE`, and confirm. Staging is
   emptied and replaced by the copy, brought up to date with the code now running, and you are signed out.
4. **Sign in** with your **live email** and the **staging password**. Everything is there as on live: runs, lots, samples, labs, documents.
5. Test the change. When finished, delete the backup file from your computer.

## Testing a pull request on staging (before merging it)

Merging a PR into `main` deploys it to the live site, so use staging first for anything risky: migrations, changes to who may do what, anything that depends on real data. Staging never deploys on its own, so you choose when it changes.

1. **Wait for the green `test` check** on the PR. There is no point deploying a branch that fails the tests.
2. **Make sure staging has realistic data.** If it is stale, refresh it first (see [Refreshing staging from live](#refreshing-staging-from-live-each-time)). Staging keeps its data between deploys, so this is only needed when you want fresher data.
3. **Point staging at the branch.** Render dashboard > `kelpworks-erp-staging` > Settings > Build & Deploy > **Branch**: change `main` to the PR's branch (for example `hardening/phase1_release_integrity`) and save. Nothing deploys yet, because auto-deploy is off.
4. **Deploy it.** On the service, open the **Manual Deploy** menu and choose **Deploy latest commit**. Watch the deploy log until the service is live. The migrations run at startup on the copy of live data, so this also tests that the upgrade works on real data.
5. **Test it on the staging address** (amber STAGING banner). Sign in with your **live email** and the **staging password**, and try what the PR changes. The PR description lists what to check.
6. **Decide.**
   - Looks good: **merge the PR on GitHub.** That deploys to live; nothing else is needed for live.
   - Problem found: do **not** merge. Tell Claude what you saw; it fixes the branch and pushes, and the PR updates itself. Repeat step 4 (**Deploy latest commit**) to test the new commit.
7. **Set staging's Branch back to `main`** (Settings > Build & Deploy > Branch), so a later manual deploy does not redeploy the old branch. Use Manual Deploy afterwards if you want staging to match live again.

Menu names are as of writing and may be worded slightly differently in the Render dashboard.

What staging cannot tell you: how real users will behave with a change, or anything that depends on the live service's own settings (environment variables). After a live deploy, do the spot-check in [release-guide.md](release-guide.md).

## What a restore does and does not do

- Replaces the staging database and every document, lab template and SOP with the backup's.
- Runs the migrations, so a copy of live made by older code is upgraded to the code under test (a good way to try a migration on real data).
- Resets **every** user's password to the staging password and clears "must change password". Live passwords never work on staging.
- Keeps real names, customers, lots and lab results, so tests are realistic. Treat the staging URL as internal: do not share it outside the company.
- Does not copy anything back to live. There is no way to send staging data to the live site.
- Refuses a file that is not a KelpWorks full backup (no manifest, damaged database, unexpected paths), before anything is changed.
