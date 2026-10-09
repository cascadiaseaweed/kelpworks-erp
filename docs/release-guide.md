# KelpWorks release guide

How a change moves from a laptop to the live site: where the app runs, how to test a change, and how it is deployed on Render.
This file lives in the repository on purpose: it changes through the same pull request and `test` check as the code. **If the process changes, change this file in the same pull request.**

Related: [`docs/staging.md`](staging.md) (the staging site), [`CLAUDE.md`](../CLAUDE.md) (how the code is organised).

## Words used here

- **Branch**: a separate line of work. Changes on a branch do not affect anyone until they are merged. `hardening/` in a name is only a label (like a folder) for fixes that make the app more reliable or safe; any branch is an ordinary branch.
- **PR (pull request)**: a request on GitHub to merge a branch into `main`. It shows every changed line and has a **Merge** button. Nothing reaches `main`, and so nothing goes live, until someone clicks Merge.
- **CI (continuous integration)**: a robot that runs the tests on every PR (GitHub Actions, one job named `test`). Green tick = passed, red cross = failed.
- **`main`**: the branch that is live. Render deploys it automatically.

## Where KelpWorks runs

| Place | What it is | Data |
|---|---|---|
| Your computer | The app at `http://localhost:8002` (`python kelp_erp_server.py` or `run.bat`). | A local database, `kelp_erp.db`. Not the real business data. |
| Scratch servers | Throwaway copies (for example port 8012) or the test harness, used to try risky changes. | Throwaway databases, deleted afterwards. |
| GitHub | The code, `cascadiaseaweed/kelpworks-erp`, and CI. | Code only. |
| Staging (Render) | `kelpworks-erp-staging`: a second site holding a **copy** of live data. Updates only when you click Deploy. See [staging.md](staging.md). | A copy of live, refreshed on demand. Shows a STAGING banner. |
| Live (Render) | `kelpworks-erp`: the site the team uses. Redeploys automatically whenever `main` changes. | The real database and documents on a 1 GB disk that survives restarts and redeploys. |

Local, staging and live data are completely separate. Deploying changes code only, never data. Every restart migrates the database automatically (columns and tables are only added), which is why a code change is safe to deploy.

## The workflow in Claude Code desktop

Ask in plain words; Claude runs the git commands and tells you what happened.

| Step | Say this | What happens |
|---|---|---|
| 1. Start | "Create a new branch hardening/fix-something" | Claude switches to `main`, pulls the latest, creates the branch. |
| 2. Build | Describe the change or problem | Claude edits the code and checks it, usually on a throwaway database. |
| 3. Test | "Restart the dev server", then "run the tests" | Your instance on `localhost:8002` reloads and you click through the change. The automated tests run. |
| 4. Save | "Commit changes" | Claude commits on your branch. Nothing is live. |
| 5. Review | "Push and open a pull request" | Claude pushes the branch and opens a PR into `main`. It does not merge. |
| 6. Check | "Is the test check passing on the PR?" | Claude reports the `test` check and whether the PR can be merged. |
| 7. (Optional) Try on staging | Deploy the branch on Render's staging service (see below) | The change runs on a copy of live data, before it is live. |
| 8. Go live | You click **Merge** on the PR in GitHub | Render rebuilds and deploys within a few minutes. |
| 9. Catch up | "Switch to main and pull, then restart the dev server" | Your computer matches the live code. |

**Say "push and open a pull request", never "push to main".** Direct pushes to `main` skip the test check and any review and cannot be undone with a button. If the check fails, say "the check failed, please fix it": Claude reads the failure, fixes it on the same branch and pushes; the PR updates itself.

Typical session, one line at a time:

```
Create a new branch hardening/fix-sample-sheet-dates
Fix the problem where ... (describe it)
Restart the dev server
Run the tests
Commit changes
Push and open a pull request
Is the test check passing on the PR?
(merge the PR on GitHub)
Switch to main and pull, then restart the dev server
```

## Testing a change

1. **Click through it locally.** Open the screen you changed on `localhost:8002` and check it works with no errors in the browser. This is the main test; the automated tests cannot judge a Word form or a screen.
2. **Run the automated tests.**
   ```bash
   python -m pip install -r requirements-dev.txt    # once: pytest, a dev-only tool (the shipped app has no dependencies)
   python -m pytest
   ```
   The tests start the real server on a throwaway database (`tests/harness.py`) and drive it over HTTP.
3. **Try risky changes on a copy of the data first.** Anything touching the database or the lab Word forms is tried on a throwaway copy, never on live data.
4. **For something that needs realistic data, use staging** (below).

### What CI checks on every PR

The single job `test` runs, in order:

1. the server compiles;
2. **a fresh database boots and restarts** (`tests/test_fresh_db.py`);
3. **migrations upgrade older databases without losing data** (`tests/test_migrations.py`): for each past version listed in `tests/legacy_commits.txt`, build that version's database, boot the current code on a copy, restart it, and check logins, integrity, no lost rows, no new foreign-key damage;
4. the rest of the suite (login and access rules, backup and restore, and so on).

**After each release, add the commit that is now live to `tests/legacy_commits.txt`**, so the next change is checked against what is actually deployed.

A green check means the app starts, upgrades old data and passes those tests. It does **not** prove the SGS forms, requisitions, production log or release sign-off work: step 1 above still matters.

## Deploying

Live auto-deploys: **merging a PR into `main` is the deploy.** There is no deploy command.

1. Push the branch and open a PR (`git push -u origin <branch>`, then `gh pr create --base main`, or the green button on GitHub). Claude does both when you ask.
2. Read the **Files changed** tab, wait for the green `test` check, then click **Merge**.
3. In the Render dashboard, open `kelpworks-erp` and watch the deploy log (a few minutes). If the new version fails to start, the old one keeps running.
4. Bring your computer up to date: `git checkout main` then `git pull origin main`.

Merge only changes you are ready to have live. A red `test` run on `main` means fix it with a new PR straight away.

### After a deploy

- Hard refresh the browser (Ctrl+F5) so it loads the new page code.
- If a lab Word form changed, open Admin > Labs & analyses on **live** and click "Use the ready-made KelpWorks form" for that lab: deploying code does not replace form files already stored on the live server.
- Log in on live, open the screen you changed, and confirm nothing shows an error.
- Add the new live commit to `tests/legacy_commits.txt` (next PR).

## Trying a change on staging first

Use this for migrations, anything that depends on real data, or when you want to see it work before it is live. Full details: [staging.md](staging.md).

Do this **before** merging the PR (merging deploys to live). The PR is only merged once it looks right on staging.

1. **Wait for the green `test` check** on the PR. There is no point deploying a branch that fails the tests.
2. **Make sure staging has realistic data.** If it is stale, refresh it first (see [Refreshing staging from live](staging.md#refreshing-staging-from-live-each-time)). Staging keeps its data between deploys, so this is only needed when you want fresher data.
3. **Point staging at the branch.** Render dashboard > `kelpworks-erp-staging` > Settings > Build & Deploy > **Branch**: change `main` to the PR's branch (for example `hardening/phase1_release_integrity`) and save. Nothing deploys yet, because auto-deploy is off.
4. **Deploy it.** On the service, open the **Manual Deploy** menu and choose **Deploy latest commit**. Watch the deploy log until the service is live. The migrations run at startup on the copy of live data, so this also tests that the upgrade works on real data.
5. **Test it on the staging address** (amber STAGING banner). Sign in with your **live email** and the **staging password**, and try what the PR changes. The PR description lists what to check.
6. **Decide.**
   - Looks good: **merge the PR on GitHub.** That deploys to live; nothing else is needed for live.
   - Problem found: do **not** merge. Tell Claude what you saw; it fixes the branch and pushes, and the PR updates itself. Repeat step 4 (**Deploy latest commit**) to test the new commit.
7. **Set staging's Branch back to `main`** (Settings > Build & Deploy > Branch), so a later manual deploy does not redeploy the old branch. Use Manual Deploy afterwards if you want staging to match live again.

Menu names are as of writing and may be worded slightly differently in the Render dashboard.

In Claude Code, the matching prompts are: "push and open a pull request" (before step 1), "is the test check passing on the PR?" (step 1), and "the check failed, please fix it" or "staging showed this problem: ..." (step 6).

Staging never auto-deploys, and its Restore function exists only there: the live site cannot overwrite itself.

## If something goes wrong

- **Undo a deploy:** open the merged PR on GitHub and click **Revert**. That creates a new PR; check it is green and merge it. Data is not affected.
- **A migration went wrong at startup:** the failed start rolled back by itself, so the data is as it was before the deploy; fix forward (a new PR) or revert the PR. Every
  upgrade of an existing database is preceded by a compressed copy at `/var/data/backups/pre-migrate-<date>.db.gz` (the last three are kept). To go back to one: stop the service
  (or suspend it in Render), open the Render shell, run `gunzip -c /var/data/backups/pre-migrate-<date>.db.gz > /var/data/kelp_erp.db`, delete `/var/data/kelp_erp.db-wal` and
  `/var/data/kelp_erp.db-shm`, then deploy the previous version. Anything entered since that snapshot is lost, so use this only when the data itself was damaged.
- **Protect live data:** never edit the live database directly; never copy a local or staging database over it. The Render disk holds the only copy: take regular disk snapshots or download full backups (Admin > Download full backup).
- **Never set** `KELP_ERP_ENV`, `KELP_ERP_ALLOW_RESTORE` or `KELP_ERP_STAGING_PASSWORD` on the live service.

## Rules

- No direct pushes to `main`. Work on a branch, merge through a PR.
- Merge only when the `test` check is green.
- Fixes that harden the app use `hardening/<name>` branches.
- The shipped app stays standard-library only; `requirements-dev.txt` is for tests and CI.

**Open item:** the "red blocks merge" rule is not enforced by GitHub yet. A repository admin needs to add a branch protection rule for `main` that requires a pull request and the `test` status check. Until then it relies on merging only green PRs.
