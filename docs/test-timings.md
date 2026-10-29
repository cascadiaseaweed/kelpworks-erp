# Test suite timings (KW-70)

Measured 2026-10-28 on the development PC (Windows 11, 12 logical CPUs, Python 3.14) at `main` 3402daa, 961 tests in 67 files. Repeat the measurement with:

```bash
python -m pytest --durations=0 -q -p no:cacheprovider > timings.txt
```

## Where the time went (one test at a time, before KW-70)

The full suite took **19 min 38 s** (1,178 s). The per-test times add up to 1,169 s, so nothing was hidden: the tests ran strictly one after another.

| Phase | Seconds | Share |
|---|---|---|
| Tests (call) | 856 | 73% |
| Fixtures: booting servers and building data (setup) | 311 | 27% |

| File | Seconds | Tests |
|---|---|---|
| `test_migrations.py` (an upgrade check for every release in `legacy_commits.txt`) | 279 | 63 |
| `test_import_historical.py` | 69 | 46 |
| `test_container_rename.py` | 47 | 32 |
| `test_secrets_sessions.py` | 47 | 25 |
| `test_staging.py` | 47 | 42 |
| `test_records_archive.py` | 45 | 34 |
| `test_migration_safety.py` | 44 | 16 |
| `test_item_keys.py` | 32 | 22 |
| `test_hardening_small.py` | 32 | 11 |
| `test_release_status.py` | 32 | 18 |
| the other 57 files | 495 | |

The top 5 files were 42% of the time, the top 10 files 58%, the top 20 75%. There is no single slow test (the slowest is 9 s; the median is small): the cost is about 1,000 small tests that each boot a real server, plus the 63 upgrade checks. Each upgrade check spent about 1.8 s building the old database and about 2.3 s upgrading and restarting it.

## What KW-70 changed

| | Before | After |
|---|---|---|
| Whole suite, this PC | 19 min 38 s | **4 min 18 s** in parallel (the final run, 1,011 tests, all passed); 5 min 42 s the first time, with a cold cache |
| Whole suite, one test at a time | 19 min 38 s | 16 min 23 s (the cached old databases alone save about 100 s) |
| Upgrade checks (`test_migrations.py`) | 279 s | 104 s cold, 39 to 49 s with the cached old databases |
| Quick tier (core set of 10 files), this PC | (none) | **55 s** in parallel, 2 min 18 s one test at a time |
| Quick tier + 3 feature test files | (none) | about 1 min 10 s in parallel |

CI (GitHub, 4 CPUs): the job took **about 9 min 24 s** before KW-70 (the push run for `3402daa`) and **3 min 32 s** on its first run after it (PR 124, 2026-10-29; the cache of the older-release databases was empty and was saved at the end, so later runs
should be faster; the same code on `main` took 3 min 36 s). That run: the upgrade checks 60 s, the rest of the suite 134 s, 1,014 tests, all passed; the Node syntax check and the timing report ran. The same files take far less time on the runner than on the development PC
(728 s of test time against about 1,800 s), so judge the files by their share of the suite, not by seconds, as the report does.

**Incident:** PR 123 (the first KW-70 merge) went in with a workflow file GitHub could not read, so no `test` check ran on it or on `main` until PR 124 fixed it. `tests/test_tier_upkeep.py` now flags that kind of error.

How each change helps:
- **Parallel runs** (`pytest-xdist`, a dev-only tool): the upgrade checks are independent of each other and spread over every worker; the rest of the suite keeps each file's tests together on one worker (`--dist loadfile`).
- **Cache of older-release databases** (`.legacy_cache/`, git-ignored; CI keeps the folder between runs): the database an old commit builds never changes for a given commit, so it is built once. The upgrade and the restart (the part that is actually being tested) always run, on a copy.
- **Quick tier** (`tools/quick_tests.py`): the core tests plus the test files related to what you changed, without the heavy files marked `slow`.

## Keeping this current

The numbers above are a snapshot. The live version is the report `tools/tier_health.py` prints after every full run (`py tools/quick_tests.py --all`) and puts in the job summary of the `test` check on every pull request. It names the test files that should be marked or unmarked `slow` and warns when the core set grows past its budget (see `docs/release-guide.md`). Repeat the manual measurement above only when you want a fresh table here.

## What is still slow (ideas for later, not done)

- Most of the remaining time is server start-up: 43 test files boot their own server. Sharing one server per file where the tests allow it would cut more, at the cost of tests that depend on each other's data.
- The parallel run uses up to 8 workers (`tools/quick_tests.py`). The number was not tuned: each worker starts its own servers, so the best count on a given machine is worth measuring before changing it. On the 4-CPU CI machine the gain is smaller than on this PC.
- The staging, restore and records-archive tests are slow because they restore whole databases.
