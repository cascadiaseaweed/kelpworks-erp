"""KW-70: the quick tier's one migration check. The current code must upgrade the database of the release that is live NOW (the last commit in
tests/legacy_commits.txt): it starts, keeps every row and key, adds no foreign-key damage and restarts.

tests/test_migrations.py runs the same check for EVERY release in that file (slow, so it is not part of the quick tier). This file is in the quick tier's core
set so that a change to the schema or to migrate() is checked against what is actually deployed before a pull request is opened. The database of the old
release is built once and cached (.legacy_cache/), so after the first run this takes a few seconds.
"""
from test_migrations import check_upgrade, newest_legacy_commit


def test_the_release_that_is_live_now_upgrades_to_the_current_code(tmp_path):
    check_upgrade(newest_legacy_commit(), tmp_path)
