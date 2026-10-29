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

## Working through the change log (KW items)

The team's list of requested changes is the Excel workbook `LOG_KelpWorks_ChangeControl.xlsx`. The master is `C:\KelpWorks\ChangeControl\` (outside OneDrive; edit it there in Excel); a READ-ONLY copy is published to SharePoint (QAQC > KelpWorks ERP > Change_Control) after every tool write; backups (newest 30) are kept locally and in `Change_Control\Backups`; Claude (chat) leaves notes in `Change_Control\Inbox` instead of writing the workbook. `python tools/changelog_write.py` is the ONLY writer: `--id KW-nn Status=...` records an item's Status / Notes / PR # / Released Date / QM-001 Ref / QM-001 Impact (it refuses Released until the QM-001 impact is recorded, see below; it checks column ownership, Approved to Start, open Excel, stale files, takes a backup and verifies after saving), `--decision` records an Open Decisions answer, `--apply-inbox` applies the notes, `--publish` refreshes the SharePoint copy after you edited the master, `--backups` / `--restore` recover. `docs/backlog/` holds a CSV snapshot that Claude Code reads (`python tools/export_backlog.py` refreshes it from the master). The step-by-step procedure for people is `docs/sop-change-requests.md`. Every item carries a Type and Touches (`docs/backlog/README.md`). Work one item (or one bundle of related items) per branch with `/kw-item KW-24` (`.claude/commands/kw-item.md`): it applies the inbox, reads the item,
asks you about any open decision, creates `updates/kw-<number>-<name>` from the latest `main`, plans (you approve the plan for "Plan mode" items), builds with tests and a browser check, and stops
before committing. Everything after that is the normal flow below. Merge one branch before starting another that touches the same area, so conflicts stay small. When an item is live, set its
Status in the workbook; the live commit goes into `tests/legacy_commits.txt`.

**QM-001 impact (KW-71).** Every item says what it does to the Quality Manual: the plan states it, the pull request template has a line for it, and `changelog_write.py` refuses Released until the item's `QM-001 Impact` is recorded
(`None - <reason>` always passes; any other class also needs a `QM-001 Ref` from the `QM-001 Changes` register, drafted with `--qm-entry`). The Quality Manager decides the register entries in Excel; once approved, `python tools/qm_draft_revision.py --rev <rev> --manual <draft copy>`
writes a change list and a tracked-changes copy of the DRAFT manual into the drafts folder (never the controlled copy). `python tools/changelog_write.py --qm-report` shows what is still open. Full procedure: `docs/qm001-change-tracking.md`.

## Testing a change

1. **Click through it locally.** Open the screen you changed on `localhost:8002` and check it works with no errors in the browser. This is the main test; the automated tests cannot judge a Word form or a screen.
2. **Run the automated tests.**
   ```bash
   python -m pip install -r requirements-dev.txt    # once: pytest and pytest-xdist, dev-only tools (the shipped app has no dependencies)
   py tools/quick_tests.py                          # the quick tier: about 1 to 2 minutes
   ```
   The tests start the real server on a throwaway database (`tests/harness.py`) and drive it over HTTP.

   Three tiers, and when to use each:

   | Tier | Command | Use it |
   |---|---|---|
   | Quick | `py tools/quick_tests.py` | While you build, and before you open a pull request. Runs the core tests, the test files you changed and the ones related to what you changed (`--list` shows which and why; `--also name` adds one). |
   | Full, parallel | `py tools/quick_tests.py --all` | When a change touches migrations, release controls, security or the signed log, or when you want what CI will run (about 5 minutes on a many-core PC). |
   | Full, one at a time | `python -m pytest` | Slowest (about 20 minutes). Use it to chase a failure that only shows up in parallel. |

   The quick tier skips the heavy files marked `slow` (migrations from older releases, staging / restore, records archive, historical import ...) unless you changed them. It does not replace CI.
   It runs the whole suite instead, by itself, when you changed the shared test machinery (`tests/harness.py`, `conftest.py`, `factories.py`, `pytest.ini`, the CI workflow) or your change reaches
   many test files. Timings and what is still slow: `docs/test-timings.md`.

   **Keeping the tiers healthy (automated).** Every full run (`py tools/quick_tests.py --all` locally, and every pull request in CI) ends with a timing report. CI shows it in the job summary
   of the `test` check, with a warning only when something needs attention:

   | The report says | What to do |
   |---|---|
   | a file is slow and not marked `slow` | run `py tools/quick_tests.py --all`, then `py tools/tier_health.py --apply`; review and commit the marker |
   | a file is marked `slow` but is now quick | the same command removes the marker |
   | the core set is over its share of the suite (15%) | trim `CORE` in `tools/quick_tests.py` (a judgement) or speed up those tests |

   Nothing else needs doing when tests or features are added: new test files and new code are picked up from the diff. The one decision a person owns is what belongs in `CORE`.

   **Keeping the core set right.** `CORE` (the list at the top of `tools/quick_tests.py`) is the small set of test files the quick tier always runs. A file belongs there only if it guards a rule
   *any* change can break (not one tied to one feature: the diff already selects those), fails fast and clearly, and is cheap. Today: a fresh database boots, the API answers, sessions and security,
   release gate and integrity, uploads, request limits, the integrity check, the clean-backup table lists (`test_clean_backup`: a new table must be in `KEEP` or `CLEAR`) and one migration check
   (`test_upgrade_latest`: the release that is live now still upgrades). `PATH_RULES` (next to it) adds test files for changed files the selector cannot read, such as `public/styles.css`.
   Review the list when one of these happens, not on a calendar:

   | Trigger | What to do |
   |---|---|
   | A new cross-cutting guard test appears (one that "fails if a new table / setting / key is not listed") | Add its file to `CORE`, or a `PATH_RULES` entry if only one kind of file triggers it. |
   | CI fails on a test file the quick tier did not run for that pull request (the timing report in the job summary says so: "failed here but the quick tier would not have run it") | Add that file to `CORE`, or add a `PATH_RULES` entry for the kind of change. This is the main way the list stays right. |
   | The timing report says the core set is over its share of the suite (15%) | Trim it, or speed up its slowest tests. |
   | A core file turns out to be slow or no longer guards anything | Move it out (and, if slow, `py tools/tier_health.py --apply` marks it). |

   The steps: edit `CORE` / `PATH_RULES`, run `py tools/quick_tests.py --list` to see the choice, `py tools/quick_tests.py --all` to see the new core share in the report, and commit the change with its reason in the pull request.
   `tests/test_tier_tools.py` and `tests/test_tier_upkeep.py` fail if a name in `CORE` or `PATH_RULES` is not a real test file, or if a core file is marked slow.

   **JavaScript syntax.** CI runs `node --check` on every `public/*.js` before the tests (the app itself still has no build step and needs no Node). Nothing in the Python tests can see a JavaScript syntax
   error, and one blanks the whole screen. Locally there is no Node on the PC, so the browser check in step 1 above covers it.
3. **Try risky changes on a copy of the data first.** Anything touching the database or the lab Word forms is tried on a throwaway copy, never on live data.
4. **For something that needs realistic data, use staging** (below).

### What CI checks on every PR

The single job `test` runs, in order:

1. the server compiles;
2. **a fresh database boots and restarts** (`tests/test_fresh_db.py`);
3. **migrations upgrade older databases without losing data** (`tests/test_migrations.py`): for each past version listed in `tests/legacy_commits.txt`, build that version's database, boot the current code on a copy, restart it, and check logins, integrity, no lost rows, no new foreign-key damage;
4. the rest of the suite (login and access rules, backup and restore, and so on).

CI runs **every** test (nothing is skipped as `slow`), in parallel workers, and keeps the databases of the older releases between runs (`.legacy_cache`) so the upgrade check does not rebuild them. The full suite is still the required check.

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

Staging never auto-deploys, and its Restore button exists only there: the live site cannot overwrite itself from inside the app (a live restore is a deliberate start-up step: `docs/restore-runbook.md`).

## If something goes wrong

- **Undo a deploy:** open the merged PR on GitHub and click **Revert**. That creates a new PR; check it is green and merge it. Data is not affected.
- **A migration went wrong at startup:** the failed start rolled back by itself, so the data is as it was before the deploy; fix forward (a new PR) or revert the PR. Every
  upgrade of an existing database is preceded by a compressed copy at `/var/data/backups/pre-migrate-<date>.db.gz` (the last three are kept). To go back to one: stop the service
  (or suspend it in Render), open the Render shell, run `gunzip -c /var/data/backups/pre-migrate-<date>.db.gz > /var/data/kelp_erp.db`, delete `/var/data/kelp_erp.db-wal` and
  `/var/data/kelp_erp.db-shm`, then deploy the previous version. Anything entered since that snapshot is lost, so use this only when the data itself was damaged.
- **Backups:** the server writes a full backup (database + documents) every night to `/var/data/backups/nightly/` (the newest 3 stay on the disk) and an office PC copies it, and every run's documents, into the
  SharePoint library `KelpWorks-Records` (setup, retention and restore notes: `docs/records-archive.md`). Check Admin > Records archive & backups after a deploy: the nightly backup should say **on**.
- **Restore the live data from a backup:** follow `docs/restore-runbook.md` (a one-time start-up restore that keeps a copy of what it replaces). Only for damaged or wrong data.
- **Reset live to a clean start (all records cleared, configuration kept):** follow `docs/clean-cut-over.md`.
- **Protect live data:** never edit the live database directly; never copy a local or staging database over it. The Render disk holds the only copy: take regular disk snapshots or download full backups (Admin > Download full backup).
- **Disk guard:** once `python -c "import os;print(os.path.ismount('/var/data'))"` prints `True` in the Render shell, set `KELP_ERP_REQUIRE_MOUNT=/var/data` so a missing disk stops the service instead of creating a new empty database.
- **Never set** `KELP_ERP_ENV`, `KELP_ERP_ALLOW_RESTORE` or `KELP_ERP_STAGING_PASSWORD` on the live service.

## Rules

- No direct pushes to `main`. Work on a branch, merge through a PR.
- Merge only when the `test` check is green.
- Fixes that harden the app use `hardening/<name>` branches.
- The shipped app stays standard-library only; `requirements-dev.txt` is for tests and CI.

**Open item:** the "red blocks merge" rule is not enforced by GitHub yet. A repository admin needs to add a branch protection rule for `main` that requires a pull request and the `test` status check. Until then it relies on merging only green PRs.
