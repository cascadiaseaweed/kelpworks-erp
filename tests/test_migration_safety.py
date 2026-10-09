"""Migration safety (risk review batch 5: R-07, R-16, R-17, R-24, R-29).

A start that fails or is killed half-way must leave the database as it was; every upgrade is preceded by a snapshot; one-time changes happen once;
a fresh database is complete; a stop request ends the server cleanly; and a database that holds REAL data written by an older release upgrades intact.
"""
import gzip
import io
import os
import signal
import sqlite3
import subprocess
import sys
import tarfile

import pytest

import kelp_erp_server as K
from factories import finished_run, harvest_totes, finalize_run, release_run, sql
from harness import ROOT, Server
from test_migrations import build_legacy_db


def db_rows(path, query, args=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(query, args).fetchall()
    finally:
        conn.close()


def columns(path, table):
    return {r[1] for r in db_rows(path, "PRAGMA table_info(%s)" % table)}


@pytest.fixture
def in_process(tmp_path, monkeypatch):
    """Point the server module at a database in tmp_path so init_db() can be called (and made to fail) inside the test process."""
    monkeypatch.setattr(K, "DB_PATH", str(tmp_path / "kelp.db"))
    monkeypatch.setattr(K, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(K, "SOP_DIR", str(tmp_path / "sop_documents"))
    monkeypatch.setattr(K, "LAB_DIR", str(tmp_path / "lab_templates"))
    monkeypatch.setattr(K, "ADMIN_PASSWORD", "test-admin-pass-1")
    monkeypatch.setattr(K, "INITIAL_USER_PASSWORD", "initial-pass-123")
    return tmp_path


# ---- R-07: atomic start ----

def test_a_failed_start_leaves_the_database_as_it_was(in_process, tmp_path_factory, monkeypatch):
    old = build_legacy_db("64a35b4", tmp_path_factory.mktemp("legacy"))              # written by the release before the SGS update
    target = str(in_process / "kelp.db")
    with open(old, "rb") as src, open(target, "wb") as dst:
        dst.write(src.read())
    before = (columns(target, "users"), db_rows(target, "SELECT COUNT(*) FROM users"), db_rows(target, "SELECT COUNT(*) FROM tote_lots"))
    assert "token_version" not in before[0]

    def boom(conn):
        raise RuntimeError("a late start-up step fails")
    monkeypatch.setattr(K, "ensure_requisition_contact", boom)
    with pytest.raises(RuntimeError, match="late start-up step"):
        K.init_db()
    # the new column (a migration made EARLY in the start) was rolled back with everything else
    after = (columns(target, "users"), db_rows(target, "SELECT COUNT(*) FROM users"), db_rows(target, "SELECT COUNT(*) FROM tote_lots"))
    assert after == before
    assert db_rows(target, "SELECT COUNT(*) FROM app_flags WHERE key='code_fingerprint'") == [(0,)]

    monkeypatch.undo()                                                               # the next start (code fixed) redoes the whole upgrade
    monkeypatch.setattr(K, "DB_PATH", target)
    monkeypatch.setattr(K, "UPLOAD_DIR", str(in_process / "uploads"))
    K.init_db()
    assert "token_version" in columns(target, "users")
    assert db_rows(target, "SELECT COUNT(*) FROM users") == before[1]


def test_a_repair_that_fails_does_not_stop_the_start(in_process, monkeypatch):
    def boom(conn, *a, **k):
        raise RuntimeError("bad row")
    monkeypatch.setattr(K, "sync_pending_samples", boom)
    K.init_db()                                                                      # logged and skipped, not fatal
    assert db_rows(str(in_process / "kelp.db"), "SELECT COUNT(*) FROM users")[0][0] > 0
    assert db_rows(str(in_process / "kelp.db"), "SELECT COUNT(*) FROM app_flags WHERE key='code_fingerprint'") == [(1,)]


def test_an_essential_step_that_fails_stops_the_start(in_process, monkeypatch):
    monkeypatch.setattr(K, "ensure_users", lambda conn: (_ for _ in ()).throw(RuntimeError("cannot create users")))
    with pytest.raises(RuntimeError):
        K.init_db()
    assert db_rows(str(in_process / "kelp.db"), "SELECT COUNT(*) FROM users")[0][0] == 0      # nothing half-done was kept


# ---- R-07: snapshot before migrating ----

def test_the_database_is_snapshotted_before_new_code_migrates_it(tmp_path_factory):
    old = build_legacy_db("64a35b4", tmp_path_factory.mktemp("legacy"))
    users_before = db_rows(old, "SELECT COUNT(*) FROM users")
    with Server(db_path=old) as srv:
        folder = os.path.join(srv.tmp, "backups")
        snaps = sorted(os.listdir(folder))
        assert len(snaps) == 1 and snaps[0].startswith("pre-migrate-") and snaps[0].endswith(".db.gz")
        restored = os.path.join(srv.tmp, "restored.db")
        with gzip.open(os.path.join(folder, snaps[0]), "rb") as f, open(restored, "wb") as out:
            out.write(f.read())
        assert "token_version" not in columns(restored, "users")                      # it is the OLD database, before the migration
        assert db_rows(restored, "SELECT COUNT(*) FROM users") == users_before
        assert db_rows(restored, "PRAGMA integrity_check") == [("ok",)]
        srv.restart()                                                                 # same code again: nothing new to protect
        assert len(os.listdir(folder)) == 1


def test_a_new_database_is_not_snapshotted():
    with Server() as srv:
        assert not os.path.isdir(os.path.join(srv.tmp, "backups")) or os.listdir(os.path.join(srv.tmp, "backups")) == []


def test_only_the_last_three_snapshots_are_kept(in_process):
    K.init_db()
    conn = K.db()
    try:
        for i in range(5):
            assert K.snapshot_before_migrating(conn, stamp="20260101-00000%d" % i)
    finally:
        conn.close()
    kept = sorted(f for f in os.listdir(K.backups_dir()) if f.startswith("pre-migrate-"))
    assert kept == ["pre-migrate-20260101-00000%d.db.gz" % i for i in (2, 3, 4)]               # the oldest two were removed
    assert not [f for f in os.listdir(K.backups_dir()) if f.endswith(".tmp")]                   # no half-written copy is left


# ---- R-16: one-time changes happen once ----

def test_an_administrators_setting_survives_restarts():
    with Server() as srv:
        admin = srv.admin_client()
        assert admin.put("/api/settings/preproc_target_solids_pct", {"value": 10}).status == 200      # a deliberate choice, equal to the old default
        for _ in range(2):
            srv.restart()
            admin = srv.admin_client()
            settings = admin.get("/api/settings").json["settings"]
            value = settings["preproc_target_solids_pct"] if isinstance(settings, dict) else [x for x in settings if x["key"] == "preproc_target_solids_pct"][0]
            assert float(value["value"] if isinstance(value, dict) else value) == 10.0


def test_a_metal_spec_unit_an_administrator_changes_is_not_reverted(tmp_path_factory):
    old = build_legacy_db("64a35b4", tmp_path_factory.mktemp("legacy"))
    with Server(db_path=old) as srv:
        sql(srv, "UPDATE coa_specs SET unit='ppm', max_val=99, limit_kg_ha=15 WHERE code='as'")
        srv.restart()
        srv.restart()
        assert sql(srv, "SELECT unit, max_val FROM coa_specs WHERE code='as'") == [("ppm", 99.0)]


def test_restarts_do_not_churn_the_consumables_table(tmp_path_factory):
    old = build_legacy_db("e56f9bf", tmp_path_factory.mktemp("legacy"))                # an older database that still has the retired container_units rows
    with Server(db_path=old) as srv:
        first = sql(srv, "SELECT seq FROM sqlite_sequence WHERE name='consumables'")
        srv.restart()
        srv.restart()
        assert sql(srv, "SELECT seq FROM sqlite_sequence WHERE name='consumables'") == first          # it used to grow at every start


# ---- R-17: a new database is complete ----

def test_a_new_database_has_the_reagent_types(fresh_server):
    rows = dict(sql(fresh_server, "SELECT name, reagent_type FROM consumables WHERE name IN ('Citric Acid','Potassium Sorbate','Sodium Benzoate')"))
    assert rows == {"Citric Acid": "Citric Acid", "Potassium Sorbate": "Potassium Sorbate", "Sodium Benzoate": "Sodium Benzoate"}
    fresh_server.restart()
    assert dict(sql(fresh_server, "SELECT name, reagent_type FROM consumables WHERE name='Citric Acid'")) == {"Citric Acid": "Citric Acid"}


# ---- R-24: a stop request ends the server cleanly ----

@pytest.mark.skipif(os.name == "nt", reason="SIGTERM is a hard kill on Windows; this runs on the Linux CI")
def test_a_stop_request_exits_cleanly():
    with Server() as srv:
        srv.proc.send_signal(signal.SIGTERM)
        assert srv.proc.wait(timeout=20) == 0
        log = srv.log_tail(50)
        assert "Stop requested" in log and "Server stopped" in log


# ---- R-29: real data written by an older release upgrades intact ----

def checkout(commit, dest):
    tar = subprocess.run(["git", "archive", "--format=tar", commit], cwd=ROOT, capture_output=True)
    if tar.returncode != 0:
        if os.environ.get("CI"):
            pytest.fail("commit %s is missing (CI must fetch full history)" % commit)
        pytest.skip("commit %s is not in this clone" % commit)
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as t:
        t.extractall(dest)
    return str(dest)


@pytest.mark.parametrize("commit", ["d0a85f8", "65147e1"])
def test_data_written_by_an_older_release_upgrades_intact(commit, tmp_path):
    """Run the OLD release, put real work in it (totes, a released run, lab results, a shipment, a document), then start the CURRENT code on that data."""
    tree = checkout(commit, tmp_path / "old_tree")
    with Server(root=tree) as old:
        admin = old.admin_client()
        pm, pm_info = old.make_user(isProductionManager=True)
        qm, qm_info = old.make_user(isQualityManager=True)
        rid = finished_run(admin)
        lots = release_run(old, rid, pm, pm_info["password"], qm, qm_info["password"])
        customer = admin.post("/api/customers", {"name": "Upgrade Test Customer"}).json["customers"][-1]["id"]
        sql(old, "UPDATE fg_lots SET qty=10 WHERE id=?", (lots[0]["id"],))
        shipped = admin.post("/api/shipments", {"customerId": customer, "shipmentNo": "UP-1", "lines": [{"fgLotId": lots[0]["id"], "qty": 4}]})
        assert shipped.ok, shipped.text
        tote_count = sql(old, "SELECT COUNT(*) FROM tote_lots")[0][0]
        events_before = sql(old, "SELECT COUNT(*) FROM release_events")[0][0]
        saved = old.export(str(tmp_path / "carried"))

    with Server(db_path=saved) as new:
        admin = new.admin_client()
        run = [r for r in admin.get("/api/production").json["runs"] if r["id"] == rid][0]
        assert run["status"] == "completed"
        assert sql(new, "SELECT COUNT(*) FROM tote_lots")[0][0] == tote_count
        assert sql(new, "SELECT COUNT(*) FROM release_events")[0][0] >= events_before           # the audit trail is kept (a rebaseline may add entries)
        assert admin.get("/api/release/verify").json["ok"] is True                              # and its chain still verifies
        detail = admin.get("/api/release/runs/%d" % rid).json
        assert detail["state"] == "released" and detail["logMatchesReview"] is True              # the signed log still matches: nothing was silently voided
        fg = sql(new, "SELECT qty, status FROM fg_lots WHERE id=?", (lots[0]["id"],))[0]
        assert fg == (6.0, "on_hand")                                                            # 10 less the 4 shipped
        assert sql(new, "SELECT COUNT(*) FROM shipment_lines WHERE fg_lot_number=?", (lots[0]["number"],))[0][0] == 1
        assert sql(new, "SELECT COUNT(*) FROM lab_results WHERE run_id=? AND voided_at IS NULL", (rid,))[0][0] >= 5
        assert admin.get("/api/integrity").status == 200
        new.restart()                                                                            # and a second start on the migrated data is clean
        assert new.admin_client().get("/api/me").status == 200
