"""Migration check: the CURRENT code must upgrade a database written by an OLDER version of the app, without losing data.

For each commit in tests/legacy_commits.txt: check that old version out, boot it on an empty database (seed data included), then boot the current
server on a COPY and check that it starts, keeps every row it had, leaves no new foreign-key damage, and can restart (migrations are idempotent).

Locally a commit missing from your clone is skipped; in CI (CI=true) it fails, so the check can never pass by silently testing nothing.
"""
import io
import os
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from harness import ROOT, Server

HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY_ADMIN, LEGACY_PASSWORD = "admin@kelp.local", "kelp1234"      # the seed admin every old version created


def _commits():
    out = []
    for line in open(os.path.join(HERE, "legacy_commits.txt"), encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(pytest.param(line.split()[0], id=" ".join(line.split()[:2])))
    return out


# Boots an old version on an empty database. Foreign keys are switched off for this one step only: a few historic versions cannot start on an
# EMPTY database (the bug fixed in PR #4), but the database they would have written is what we need.
BUILD_LEGACY = """
import sys
sys.path.insert(0, ".")
import kelp_erp_server as k
_db = k.db
def db():
    c = _db()
    c.execute("PRAGMA foreign_keys=OFF")
    return c
k.db = db
k.init_db()
print("legacy database built")
"""


@pytest.fixture(scope="module")
def legacy_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("legacy")


def build_legacy_db(commit, workdir):
    """Check `commit` out into workdir and boot it on a new database. Returns the database path."""
    tar = subprocess.run(["git", "archive", "--format=tar", commit], cwd=ROOT, capture_output=True)
    if tar.returncode != 0:
        msg = "commit %s is not in this clone (%s)" % (commit, tar.stderr.decode("utf-8", "replace").strip()[:120])
        if os.environ.get("CI"):
            pytest.fail(msg + " -- CI must fetch full history (fetch-depth: 0)")
        pytest.skip(msg)
    tree = os.path.join(str(workdir), commit)
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as t:
        t.extractall(tree)
    db = os.path.join(str(workdir), commit + ".db")
    env = dict(os.environ, KELP_ERP_DB=db, KELP_ERP_UPLOADS=os.path.join(str(workdir), commit + "_uploads"), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", BUILD_LEGACY], cwd=tree, env=env, capture_output=True)
    assert r.returncode == 0, "the old version (%s) did not build its database:\n%s" % (commit, r.stderr.decode("utf-8", "replace")[-800:])
    return db


# natural keys used to prove no row was lost (table -> column); only checked where both exist. Names that migrations rename ON PURPOSE
# (consumables: _rename_consumable) are left out: those tables are still covered by the row-count check.
KEYS = {"users": "email", "species": "code", "sites": "code", "fg_skus": "code", "tote_lots": "lot_number",
        "production_runs": "processing_lot", "customers": "name", "locations": "name"}


def snapshot(path):
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in tables}
        keys = {}
        for t, col in KEYS.items():
            if t in tables and col in [r[1] for r in conn.execute('PRAGMA table_info("%s")' % t)]:
                keys[t] = {r[0] for r in conn.execute('SELECT "%s" FROM "%s"' % (col, t))}
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        return {"tables": tables, "counts": counts, "keys": keys, "fk": fk, "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0]}
    finally:
        conn.close()


@pytest.mark.parametrize("commit", _commits())
def test_current_code_upgrades_an_older_database(commit, legacy_dir):
    old_db = build_legacy_db(commit, legacy_dir)
    before = snapshot(old_db)
    assert before["tables"], "the legacy database is empty"

    with Server(db_path=old_db) as srv:                      # boots the CURRENT code on a copy (raises with the server's own error if it can't)
        admin = srv.client()                                 # the legacy database keeps ITS admin account (existing logins must still work)
        assert admin.login(LEGACY_ADMIN, LEGACY_PASSWORD).status == 200
        assert admin.get("/api/refdata").status == 200
        srv.restart()                                        # a second boot on the migrated database must also work
        again = srv.client()
        assert again.login(LEGACY_ADMIN, LEGACY_PASSWORD).status == 200
        assert again.get("/api/me").status == 200
        after = snapshot(srv.db_path)

    assert after["integrity"] == "ok"
    for t in before["tables"]:
        if t in after["counts"]:                             # a table may be retired, but a kept table must not lose rows
            assert after["counts"][t] >= before["counts"][t], "%s lost rows: %d -> %d" % (t, before["counts"][t], after["counts"][t])
    for t, vals in before["keys"].items():
        missing = vals - after["keys"].get(t, set())
        assert not missing, "%s lost %s" % (t, sorted(missing)[:5])
    assert after["fk"] <= before["fk"], "the migration left foreign-key violations (%d -> %d)" % (before["fk"], after["fk"])
