"""Data-integrity cleanup (risk review batch 7: R-26, R-27, R-28, R-30, R-31, R-32 and small leftovers).

A database rule that a request trips is a 409, never a 500; packaging rows that name the same container finalize into ONE lot; the IBC count and the
output litres are worked out one way; a finalized run's accepted / rejected decision and reagent totals have one owner; quantities must be real
numbers; every foreign key has an index.
"""
import sqlite3

import pytest

from factories import feedstock_detail, finalize_run, finished_run, harvest_totes, sql
from harness import Server
from test_migrations import build_legacy_db

IBC = "New 1,000 L IBC Tote"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def amender(srv):
    client, info = srv.make_user(role="user", canAmendLog=True)
    return client, info["password"]


def run_row(srv, rid):
    return sql(srv, "SELECT output_litres, ibc_used, citric_kg, sorbate_kg, nabenzoate_kg FROM production_runs WHERE id=?", (rid,))[0]


def fk_columns_without_an_index(path):
    conn = sqlite3.connect(path)
    try:
        missing = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            led = set()
            for idx in conn.execute("PRAGMA index_list(%s)" % t).fetchall():
                cols = conn.execute("PRAGMA index_info(%s)" % idx[1]).fetchall()
                if cols:
                    led.add(cols[0][2])
            pk = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t) if r[5]]
            if len(pk) == 1:
                led.add(pk[0])
            for fk in conn.execute("PRAGMA foreign_key_list(%s)" % t).fetchall():
                if fk[3] not in led:
                    missing.append((t, fk[3]))
        return missing
    finally:
        conn.close()


# ---- R-30: foreign-key columns are indexed ----

def test_every_foreign_key_column_has_an_index(fresh_server):
    assert fk_columns_without_an_index(fresh_server.db_path) == []


def test_an_older_database_gets_the_indexes_when_it_is_upgraded(tmp_path_factory):
    old = build_legacy_db("64a35b4", tmp_path_factory.mktemp("legacy"))
    assert fk_columns_without_an_index(old)                                           # the old release had none of them
    with Server(db_path=old) as s:
        assert fk_columns_without_an_index(s.db_path) == []
        s.restart()                                                                    # and starting again does not fail on the existing indexes
        assert fk_columns_without_an_index(s.db_path) == []


# ---- R-26: several rows naming one container ----

def test_two_packaging_rows_of_the_same_container_finalize_as_one_lot(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1), pack=((IBC, 1), (IBC, 2)))
    assert f.ok, f.text                                                                # it used to fail on the lot number being used twice (500)
    lots = sql(srv, "SELECT fg_lot_number, qty, litres_each FROM fg_lots WHERE run_id=?", (rid,))
    assert len(lots) == 1 and lots[0][1] == 3 and lots[0][2] == 1000
    out, ibc = run_row(srv, rid)[:2]
    assert out == 3000 and ibc == 3


def test_the_ibc_count_recognises_the_real_container_name(srv, admin):
    rid = finished_run(admin, pack=((IBC, 2),))
    assert run_row(srv, rid)[1] == 2                                                   # the old test for the exact text "IBC" always counted 0


# ---- R-27: output litres follow the lots ----

def test_output_litres_follow_the_lots_when_a_containers_size_changes(srv, admin, amender):
    client, password = amender
    rid = finished_run(admin, pack=((IBC, 1),))
    assert run_row(srv, rid)[0] == 1000
    cid = sql(srv, "SELECT id FROM consumables WHERE name=?", (IBC,))[0][0]
    try:
        assert admin.put("/api/consumables/%d" % cid, {"litresEach": 500}).status == 200
        assert client.post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "one more IBC was filled", "password": password}).status == 200
        assert client.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": IBC, "qty": 1}).status == 200
        out, ibc = run_row(srv, rid)[:2]
        lot_qty, lot_litres = sql(srv, "SELECT qty, litres_each FROM fg_lots WHERE run_id=?", (rid,))[0]
        assert (lot_qty, lot_litres) == (2, 1000)
        assert out == 2000 and ibc == 2                                                # 2 x the 1,000 L the lot was made with, not 2 x the new 500 L
    finally:
        admin.put("/api/consumables/%d" % cid, {"litresEach": 1000})


# ---- R-28 / R-31: a run's totes ----

def test_finalize_refuses_when_a_locked_tote_is_missing_from_the_request(srv, admin):
    totes = harvest_totes(admin, 2)
    rid, _ = finalize_run(admin, totes, finalize=False)                                # both totes are now locked to the draft
    body = {"sku": "KELPIVEX", "toteIds": totes[:1], "feedstockDetails": {str(totes[0]): feedstock_detail()}, "runDate": "2026-05-04"}
    r = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert r.status == 409 and "locked to this run" in r.json["error"], r.text
    assert sql(srv, "SELECT status FROM production_runs WHERE id=?", (rid,)) == [("draft",)]


def test_a_tote_that_was_inspected_for_a_run_cannot_be_deleted(srv, admin):
    totes = harvest_totes(admin, 2)
    rid, _ = finalize_run(admin, totes, finalize=False)
    details = {str(totes[0]): feedstock_detail(), str(totes[1]): dict(feedstock_detail(), decision="rejected", rejectionReason="smell")}
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.post("/api/production/drafts/%d/finalize" % rid, body).ok                # the second tote is rejected at finalize: it goes to QAQC Hold
    rejected = totes[1]
    assert sql(srv, "SELECT status FROM tote_lots WHERE id=?", (rejected,)) == [("hold",)]
    assert sql(srv, "SELECT COUNT(*) FROM run_inputs WHERE tote_lot_id=?", (rejected,))[0][0] == 1
    d = admin.delete("/api/totes/%d" % rejected)
    assert d.status == 409 and d.json["code"] == "tote_has_history", d.text            # it used to be a 500 (the row is still referenced)
    assert sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE id=?", (rejected,))[0][0] == 1
    spare = harvest_totes(admin, 1)[0]                                                 # a tote with no history is still deletable
    assert admin.delete("/api/totes/%d" % spare).status == 200


def test_the_accepted_or_rejected_decision_is_fixed_once_the_run_is_finalized(srv, admin, amender):
    client, password = amender
    rid = finished_run(admin)
    input_id, tote_id, decision = sql(srv, "SELECT id, tote_lot_id, decision FROM run_inputs WHERE run_id=?", (rid,))[0]
    assert decision == "accepted"
    assert client.post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "check the characterization", "password": password}).status == 200
    r = client.put("/api/production/%d/inputs/%d" % (rid, input_id), {"decision": "rejected", "rejectionReason": "changed mind"})
    assert r.status == 409 and r.json["code"] == "decision_locked", r.text
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (tote_id,)) == [("consumed", rid)]      # it used to move the tote to QAQC Hold and leave the run as it was
    assert client.put("/api/production/%d/inputs/%d" % (rid, input_id), {"decision": "accepted", "notes": "reviewed"}).status == 200      # the same decision, and the other details, can be amended


# ---- R-32: reagent totals have one owner ----

def test_run_edit_cannot_change_reagent_totals_behind_the_ledger(srv, admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    citric = sql(srv, "SELECT id, on_hand FROM consumables WHERE name='Citric Acid'")[0]
    before = run_row(srv, rid)[2:]                                                     # the Dilution & Preservation entries already set these
    r = admin.put("/api/production/%d" % rid, {"citricKg": 5, "sorbateKg": 3, "nabenzoateKg": 2, "notes": "edited"})
    assert r.status == 200 and r.json["changed"] == 1                                  # only the note changed
    assert run_row(srv, rid)[2:] == before                                             # the run's reagent totals are untouched
    assert sql(srv, "SELECT on_hand FROM consumables WHERE id=?", (citric[0],))[0][0] == citric[1]      # and no stock moved


# ---- R-26: one bad request is never a 500 ----

def test_a_database_rule_that_a_request_trips_is_a_409(admin):
    a = admin.post("/api/customers", {"name": "Dup Test A"}).json["customers"]
    b = admin.post("/api/customers", {"name": "Dup Test B"}).json["customers"]
    id_b = [c["id"] for c in b if c["name"] == "Dup Test B"][0]
    r = admin.put("/api/customers/%d" % id_b, {"name": "Dup Test A"})
    assert r.status == 409 and r.json["code"] == "conflict" and "already in use" in r.json["error"], r.text
    assert "customers.name" not in r.text                                              # no table or column names are shown
    assert admin.get("/api/customers").status == 200


# ---- quantities must be real numbers ----

def test_negative_and_non_numeric_quantities_are_refused(srv, admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    entry = admin.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": IBC, "qty": 1}).json["packagingEntries"][-1]["id"]
    for bad in (-3, "nan", "inf", "abc"):
        r = admin.put("/api/production/%d/packaging-entries/%d" % (rid, entry), {"qty": bad})
        assert r.status == 400, (bad, r.text)
    assert sql(srv, "SELECT qty FROM run_packaging_entries WHERE id=?", (entry,)) == [(1.0,)]
    assert admin.put("/api/production/%d/packaging-entries/%d" % (rid, entry), {"qty": 4}).status == 200


def test_a_sample_point_quantity_that_is_not_a_number_is_refused(srv, admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    sp = admin.post("/api/production/%d/sample-points" % rid, {"stage": "packaging"}).json["samplePoints"][-1]["id"]
    before = sql(srv, "SELECT qty FROM run_sample_points WHERE id=?", (sp,))
    for bad in ("abc", "nan", "inf"):
        assert admin.put("/api/production/%d/sample-points/%d" % (rid, sp), {"qty": bad}).status == 400, bad      # it used to be a 500
    assert sql(srv, "SELECT qty FROM run_sample_points WHERE id=?", (sp,)) == before
    assert admin.put("/api/production/%d/sample-points/%d" % (rid, sp), {"qty": 99}).status == 200
    assert sql(srv, "SELECT qty FROM run_sample_points WHERE id=?", (sp,)) == [(10,)]


def test_a_stock_adjustment_of_nan_is_refused(srv, admin):
    cid, before = sql(srv, "SELECT id, on_hand FROM consumables WHERE name='Citric Acid'")[0]
    for bad in ("nan", "inf", "-inf"):
        assert admin.post("/api/consumables/%d/adjust" % cid, {"delta": bad}).status == 400, bad               # nan used to be stored as the stock
    assert sql(srv, "SELECT on_hand FROM consumables WHERE id=?", (cid,))[0][0] == before


# ---- R-31: the database refuses values the rules do not allow ----

def test_the_database_refuses_an_unknown_status_or_negative_units(srv, admin):
    rid = finished_run(admin)
    lot = sql(srv, "SELECT id, status, qty FROM fg_lots WHERE run_id=?", (rid,))[0]
    tote = sql(srv, "SELECT id, status FROM tote_lots WHERE run_id=?", (rid,))[0]
    for statement, args in (("UPDATE fg_lots SET status='banana' WHERE id=?", (lot[0],)),
                            ("UPDATE fg_lots SET qty=-1 WHERE id=?", (lot[0],)),
                            ("UPDATE tote_lots SET status='lost' WHERE id=?", (tote[0],)),
                            ("UPDATE production_runs SET status='odd' WHERE id=?", (rid,)),
                            ("INSERT INTO fg_lots (fg_lot_number,sku_code,run_id,package_size,qty,litres_each,status,created_at)"
                             " VALUES ('BAD-1','KELPIVEX',?,'x',1,1,'nonsense','now')", (rid,))):
        with pytest.raises(sqlite3.IntegrityError, match="Not allowed"):
            sql(srv, statement, args)
    assert sql(srv, "SELECT status, qty FROM fg_lots WHERE id=?", (lot[0],)) == [(lot[1], lot[2])]
    assert sql(srv, "SELECT status FROM tote_lots WHERE id=?", (tote[0],)) == [(tote[1],)]
    sql(srv, "UPDATE fg_lots SET status=?, qty=? WHERE id=?", (lot[1], lot[2], lot[0]))                 # writing the value a row already has is fine


def test_an_upgraded_database_gets_the_guards_and_an_old_odd_row_does_not_block_other_edits(tmp_path_factory):
    old = build_legacy_db("64a35b4", tmp_path_factory.mktemp("legacy"))
    conn = sqlite3.connect(old)
    conn.execute("UPDATE tote_lots SET status='legacy_odd' WHERE id=(SELECT MIN(id) FROM tote_lots)")  # data the old release allowed
    conn.commit()
    conn.close()
    with Server(db_path=old) as s:
        names = {r[0] for r in sql(s, "SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"chk_fg_lots_status_ins", "chk_tote_lots_status_upd", "chk_fg_lots_qty_upd"} <= names
        sql(s, "UPDATE tote_lots SET notes='still editable' WHERE status='legacy_odd'")               # only a CHANGE of status is checked
        with pytest.raises(sqlite3.IntegrityError):
            sql(s, "UPDATE tote_lots SET status='another_odd_one' WHERE status='legacy_odd'")


# ---- a first start with unreadable reference data stops instead of building an empty system ----

def test_a_missing_seed_file_stops_the_first_start(tmp_path, monkeypatch):
    import kelp_erp_server as K
    monkeypatch.setattr(K, "DB_PATH", str(tmp_path / "kelp.db"))
    monkeypatch.setattr(K, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(K, "ADMIN_PASSWORD", "test-admin-pass-1")
    monkeypatch.setattr(K, "SEED_FILE", str(tmp_path / "no_such_seed.json"))
    with pytest.raises(RuntimeError, match="reference data"):
        K.init_db()
    conn = sqlite3.connect(str(tmp_path / "kelp.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0           # nothing half-built was kept: the next start seeds properly
    finally:
        conn.close()
