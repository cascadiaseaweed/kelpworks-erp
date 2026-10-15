"""Pre-Processing pack-out: every pack-out row's containers come out of inventory when the batch is completed."""
import pytest

from factories import harvest_totes, sql
from harness import Server

IBC = "New 1,000 L IBC Tote"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def on_hand(srv, name):
    return sql(srv, "SELECT on_hand FROM consumables WHERE name=?", (name,))[0][0]


def other_container(srv):
    return sql(srv, "SELECT name FROM consumables WHERE is_container=1 AND name<>? AND COALESCE(is_sample_container,0)=0 ORDER BY id", (IBC,))[0][0]


def complete_batch(admin, rows, n_totes=2):
    ids = harvest_totes(admin, n=n_totes)
    for t in ids:                                                   # these are fresh check-ins: Coarse grind, in stock
        pass
    b = admin.post("/api/preproc", {}).json
    bid = b["id"]
    assert admin.post("/api/preproc/%d/inputs" % bid, {"toteIds": ids}).status == 200
    admin.put("/api/preproc/%d" % bid, {"batchDate": "2026-10-10", "operators": "QA", "location": "Cold", "measuredPh": 3.7, "targetPh": 3.7, "citricKg": 0}).raise_for_status()
    admin.put("/api/preproc/%d/packaging" % bid, {"rows": rows}).raise_for_status()
    r = admin.post("/api/preproc/%d/complete" % bid, {})
    return bid, r


def test_several_pack_out_rows_of_one_container_are_all_deducted(srv, admin):
    before = on_hand(srv, IBC)
    bid, r = complete_batch(admin, [{"container": IBC, "qty": 2, "litresEach": 500}, {"container": IBC, "qty": 3, "litresEach": 400}])
    assert r.status == 200, r.text
    assert on_hand(srv, IBC) == before - 5
    lines = sql(srv, "SELECT delta FROM consumable_txns WHERE ref=(SELECT batch_lot FROM preproc_batches WHERE id=?) AND reason='Pre-processing pack-out'", (bid,))
    assert sum(x[0] for x in lines) == -5


def test_pack_out_rows_with_different_containers_each_come_out_of_their_own_stock(srv, admin):
    other = other_container(srv)
    b1, b2 = on_hand(srv, IBC), on_hand(srv, other)
    bid, r = complete_batch(admin, [{"container": IBC, "qty": 2, "litresEach": 500}, {"container": other, "qty": 4, "litresEach": 20}])
    assert r.status == 200, r.text
    assert (on_hand(srv, IBC), on_hand(srv, other)) == (b1 - 2, b2 - 4)


def test_production_run_packaging_rows_are_all_deducted_too(srv, admin):
    from factories import finalize_run
    other = other_container(srv)
    b1, b2 = on_hand(srv, IBC), on_hand(srv, other)
    rid, f = finalize_run(admin, harvest_totes(admin, n=1), pack=((IBC, 1), (IBC, 2), (other, 3)))
    assert f.ok, f.text
    assert (on_hand(srv, IBC), on_hand(srv, other)) == (b1 - 3, b2 - 3)


# ---- the data integrity check covers completed pre-processing batches --------------------------------------------------------------

from harness import ADMIN_PASSWORD


def batch_issues(admin, lot=None):
    return [i for i in admin.get("/api/integrity").json["issues"] if i["area"] == "Pre-processing stock" and (lot is None or i["run"] == lot)]


def batch_lot(srv, bid):
    return sql(srv, "SELECT batch_lot FROM preproc_batches WHERE id=?", (bid,))[0][0]


def test_a_correctly_completed_batch_raises_nothing_in_the_integrity_check(srv, admin):
    other = other_container(srv)
    bid, r = complete_batch(admin, [{"container": IBC, "qty": 2, "litresEach": 500}, {"container": IBC, "qty": 1, "litresEach": 500}, {"container": other, "qty": 2, "litresEach": 20}])
    assert r.status == 200, r.text
    assert batch_issues(admin) == []


def test_a_missing_deduction_is_found_and_repaired_through_the_ledger(srv, admin):
    other = other_container(srv)
    bid, r = complete_batch(admin, [{"container": IBC, "qty": 2, "litresEach": 500}, {"container": other, "qty": 3, "litresEach": 20}])
    assert r.status == 200, r.text
    lot = batch_lot(srv, bid)
    # simulate the fault: the second container's deduction never happened (stock and ledger line gone)
    cid = sql(srv, "SELECT id FROM consumables WHERE name=?", (other,))[0][0]
    sql(srv, "DELETE FROM consumable_txns WHERE ref=? AND consumable_id=?", (lot, cid))
    sql(srv, "UPDATE consumables SET on_hand=on_hand+3 WHERE id=?", (cid,))
    found = batch_issues(admin, lot)
    assert len(found) == 1 and found[0]["severity"] == "error" and other in found[0]["message"] and found[0]["repair"] == "restock_preproc" and found[0]["batchId"] == bid
    before = on_hand(srv, other)
    assert admin.post("/api/integrity/repair", {"kind": "restock_preproc", "batchId": bid, "password": "wrong"}).status == 400
    assert on_hand(srv, other) == before
    r = admin.post("/api/integrity/repair", {"kind": "restock_preproc", "batchId": bid, "password": ADMIN_PASSWORD})
    assert r.status == 200 and batch_issues(admin, lot) == []
    assert on_hand(srv, other) == before - 3
    assert sql(srv, "SELECT delta, reason, user_name FROM consumable_txns WHERE ref=? AND consumable_id=?", (lot, cid))[0][:2] == (-3.0, "Integrity repair (pre-processing stock)")
    assert sql(srv, "SELECT COUNT(*) FROM config_log WHERE area='integrity' AND key=?", (lot,))[0][0] == 1
    assert admin.post("/api/integrity/repair", {"kind": "restock_preproc", "batchId": bid, "password": ADMIN_PASSWORD}).status == 409      # nothing left to repair


def test_an_extra_deduction_is_found_too(srv, admin):
    bid, r = complete_batch(admin, [{"container": IBC, "qty": 2, "litresEach": 500}])
    lot = batch_lot(srv, bid)
    cid = sql(srv, "SELECT id FROM consumables WHERE name=?", (IBC,))[0][0]
    sql(srv, "INSERT INTO consumable_txns (consumable_id,delta,reason,ref,created_at) VALUES (?,-1,'stray',?,'2026-10-10T00:00:00Z')", (cid, lot))
    sql(srv, "UPDATE consumables SET on_hand=on_hand-1 WHERE id=?", (cid,))
    assert len(batch_issues(admin, lot)) == 1
    assert admin.post("/api/integrity/repair", {"kind": "restock_preproc", "batchId": bid, "password": ADMIN_PASSWORD}).status == 200
    assert batch_issues(admin, lot) == []
