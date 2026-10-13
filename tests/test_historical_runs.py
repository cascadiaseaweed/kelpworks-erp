"""Historical run entry: past runs keep their original lot number and date, need not be complete, and choose their own inventory and release treatment."""
import pytest

from factories import feedstock_detail, harvest_totes, lab_report, release_run, sql
from harness import Server

IBC = "New 1,000 L IBC Tote"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    plain, _ = srv.make_user(role="user")
    admin = srv.admin_client()
    return {"admin": admin, "pm": pm, "qm": qm, "plain": plain, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"]}


def enable(admin, on=1):
    assert admin.put("/api/settings/historical_entry_enabled", {"value": on}).status == 200


def start(client, lot, date, release="grandfather", deduct=False, sku="KELPIVEX", extra=None):
    body = dict({"sku": sku, "runDate": date, "operators": "NW", "location": "Cold", "toteIds": [], "feedstockDetails": {},
                 "historical": {"processingLot": lot, "release": release, "deductInventory": deduct}}, **(extra or {}))
    return client.post("/api/production/drafts", body)


def finish(admin, lot, date, species="SL", pack=((IBC, 1),), **kw):
    """Create a historical run, give it totes and packaging, and finalize it. Returns (run id, finalize response)."""
    r = start(admin, lot, date, **kw)
    r.raise_for_status()
    rid = r.json["run"]["id"]
    totes = harvest_totes(admin, species=species, date="2026-05-20")
    body = {"sku": kw.get("sku", "KELPIVEX"), "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": date, "operators": "NW", "location": "Cold"}
    admin.put("/api/production/drafts/%d" % rid, body).raise_for_status()
    for unit, qty in pack:
        admin.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": unit, "qty": qty}).raise_for_status()
    return rid, admin.post("/api/production/drafts/%d/finalize" % rid, body)


def item_id(admin, name):
    return [c["id"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def on_hand(admin, name):
    return [c["onHand"] for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


# ---- who may, and what is checked ----

def test_it_is_off_until_an_administrator_switches_it_on_and_only_administrators_can_use_it(srv, people):
    admin = people["admin"]
    r = start(admin, "PR-20260601-001", "2026-06-01")
    assert r.status == 403 and r.json["code"] == "historical_off"
    enable(admin)
    try:
        assert start(people["pm"], "PR-20260601-002", "2026-06-01").status == 403                 # a manager is not an administrator
        assert start(people["plain"], "PR-20260601-003", "2026-06-01").status == 403
        assert start(admin, "PR-20260601-004", "2026-06-01").status == 200
    finally:
        enable(admin, 0)


def test_the_lot_and_the_date_are_checked(srv, people):
    admin = people["admin"]
    enable(admin)
    try:
        assert start(admin, "", "2026-06-01").status == 400
        assert start(admin, "bad lot with spaces", "2026-06-01").status == 400
        assert start(admin, "PR-20260602-010", "").status == 400
        assert start(admin, "PR-20260602-010", "2999-01-01").status == 400                         # not in the future
        assert start(admin, "PR-20260602-011", "2026-06-02", release="whenever").status == 400
        assert start(admin, "PR-20260602-012", "2026-06-02").status == 200
        dup = start(admin, "pr-20260602-012", "2026-06-02")                                        # the same number, any case
        assert dup.status == 409 and dup.json["code"] == "lot_exists"
    finally:
        enable(admin, 0)


# ---- the record ----

def test_a_historical_run_keeps_its_lot_and_date_and_finalizes_without_every_required_field(srv, people):
    admin = people["admin"]
    enable(admin)
    try:
        rid, f = finish(admin, "PR-20260610-007", "2026-06-10")
        assert f.ok, f.text
        assert f.json["processingLot"] == "PR-20260610-007"
        row = sql(srv, "SELECT processing_lot, run_date, finalized_at, status, release_state, is_historical, hist_note FROM production_runs WHERE id=?", (rid,))[0]
        assert row[:6] == ("PR-20260610-007", "2026-06-10", "2026-06-10T12:00:00Z", "completed", "legacy", 1)
        assert row[6]                                                                              # what was missing is written down
        assert sql(srv, "SELECT created_at FROM fg_lots WHERE run_id=?", (rid,))[0][0] == "2026-06-10T12:00:00Z"
        run = [r for r in admin.get("/api/production").json["runs"] if r["id"] == rid][0]
        assert run["historical"]["release"] == "grandfather" and run["historical"]["enteredBy"] and run["historical"]["note"]
        ev = sql(srv, "SELECT user_name, capacity, meaning FROM release_events WHERE run_id=? AND event_type='historical_entry'", (rid,))
        assert len(ev) == 1 and ev[0][1] == "Administrator" and "Grandfathered" in ev[0][2] and "2026-06-10" in ev[0][2]
        assert admin.get("/api/release/verify").json["ok"] is True
    finally:
        enable(admin, 0)


def test_no_default_sample_points_are_created_for_a_historical_run(srv, people):
    admin = people["admin"]
    enable(admin)
    try:
        rid = start(admin, "PR-20260611-008", "2026-06-11").json["run"]["id"]
        assert sql(srv, "SELECT COUNT(*) FROM run_sample_points WHERE run_id=?", (rid,))[0][0] == 0
        normal = admin.post("/api/production/drafts", {"sku": "KELPIVEX", "runDate": "2026-06-11", "toteIds": [], "feedstockDetails": {}}).json["run"]["id"]
        assert sql(srv, "SELECT COUNT(*) FROM run_sample_points WHERE run_id=?", (normal,))[0][0] > 0
    finally:
        enable(admin, 0)


# ---- inventory ----

def test_a_run_that_leaves_inventory_alone_moves_no_stock_and_a_counted_run_does(srv, people):
    admin = people["admin"]
    enable(admin)
    try:
        cid = item_id(admin, IBC)
        admin.post("/api/consumables/%d/receive" % cid, {"qty": 10, "supplierLot": "OPENING", "receivedDate": "2026-05-01"}).raise_for_status()
        before = on_hand(admin, IBC)
        rid, f = finish(admin, "PR-20260612-020", "2026-06-12", deduct=False)
        assert f.ok, f.text
        assert on_hand(admin, IBC) == before
        assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE ref='PR-20260612-020'")[0][0] == 0
        rid2, f2 = finish(admin, "PR-20260613-021", "2026-06-13", deduct=True)
        assert f2.ok, f2.text
        assert on_hand(admin, IBC) == before - 1
        assert sql(srv, "SELECT COUNT(*) FROM lot_uses WHERE ref='PR-20260613-021'")[0][0] == 1
    finally:
        enable(admin, 0)


def test_a_counted_run_draws_only_on_stock_received_by_its_date(srv, people):
    admin = people["admin"]
    enable(admin)
    try:
        cid = item_id(admin, "1 L bottle")
        admin.post("/api/consumables/%d/receive" % cid, {"qty": 50, "supplierLot": "EARLY-BOTTLES", "receivedDate": "2026-05-01", "expiryDate": "2026-06-05"}).raise_for_status()
        admin.post("/api/consumables/%d/receive" % cid, {"qty": 50, "supplierLot": "JULY-BOTTLES", "receivedDate": "2026-07-01"}).raise_for_status()
        admin.post("/api/consumables/%d/receive" % cid, {"qty": 50, "supplierLot": "MAY-BOTTLES", "receivedDate": "2026-05-15", "expiryDate": "2027-05-15"}).raise_for_status()
        rid, f = finish(admin, "PR-20260620-030", "2026-06-20", deduct=True, pack=((IBC, 1), ("1 L bottle", 6)))
        assert f.ok, f.text
        used = {r[0]: r[1] for r in sql(srv, "SELECT l.supplier_lot, SUM(u.qty) FROM lot_uses u JOIN consumable_lots l ON l.id=u.lot_id "
                                              "WHERE u.ref='PR-20260620-030' AND u.consumable_id=? GROUP BY l.supplier_lot", (cid,))}
        assert used == {"MAY-BOTTLES": 6}                  # not the July delivery (not received yet), and not the lot that had already expired on 20 June
    finally:
        enable(admin, 0)


# ---- release ----

def test_a_grandfathered_run_is_on_hand_ships_with_a_past_date_and_is_not_held_by_later_lab_results(srv, people):
    admin, qm = people["admin"], people["qm"]
    enable(admin)
    try:
        rid, f = finish(admin, "PR-20260614-040", "2026-06-14")
        assert f.ok, f.text
        lots = sql(srv, "SELECT id, status FROM fg_lots WHERE run_id=?", (rid,))
        assert [l[1] for l in lots] == ["on_hand"]
        cust = admin.post("/api/customers", {"name": "Historical Farm"}).json["customers"]
        cid = [c["id"] for c in cust if c["name"] == "Historical Farm"][0]
        sh = admin.post("/api/shipments", {"customerId": cid, "shipDate": "2026-06-30", "lines": [{"fgLotId": lots[0][0], "qty": 1}]})
        assert sh.status == 200 and sh.json["shipment"]["shipDate"] == "2026-06-30"
        # a lab result entered later (even a failing one) is recorded but does not hold the lots of a grandfathered run
        att = lab_report(qm, rid)
        r = qm.post("/api/production/%d/lab-results" % rid, {"labName": "L", "reportNumber": "H1", "attachmentId": att, "results": [{"specCode": "salm", "value": "Positive"}]})
        assert r.status == 200, r.text
        assert sql(srv, "SELECT COUNT(*) FROM release_events WHERE run_id=? AND event_type='lab_result_hold'", (rid,))[0][0] == 0
        assert sql(srv, "SELECT status FROM fg_lots WHERE id=?", (lots[0][0],))[0][0] in ("on_hand", "sold")
        issues = admin.get("/api/integrity").json["issues"]
        assert not [i for i in issues if i["run"] == "PR-20260614-040" and i["severity"] == "error"], issues
    finally:
        enable(admin, 0)


def test_a_run_sent_for_normal_release_is_pending_review_and_goes_through_the_gate(srv, people):
    admin, pm, qm = people["admin"], people["pm"], people["qm"]
    enable(admin)
    try:
        rid, f = finish(admin, "PR-20260615-050", "2026-06-15", release="review")
        assert f.ok, f.text
        assert sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0] == "pending_review"
        assert [l[0] for l in sql(srv, "SELECT status FROM fg_lots WHERE run_id=?", (rid,))] == ["pending_release"]
        assert {e[0] for e in sql(srv, "SELECT event_type FROM release_events WHERE run_id=?", (rid,))} == {"submitted", "historical_entry"}
        sql(srv, "UPDATE production_runs SET packaging_tds_pct=1.5 WHERE id=?", (rid,))
        release_run(srv, rid, pm, people["pm_pw"], qm, people["qm_pw"])                           # the normal review, lab results and Quality release
        assert [l[0] for l in sql(srv, "SELECT status FROM fg_lots WHERE run_id=?", (rid,))] == ["on_hand"]
    finally:
        enable(admin, 0)


def test_a_normal_run_still_needs_every_required_field(srv, people):
    admin = people["admin"]
    totes = harvest_totes(admin)
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-06-16", "operators": "NW", "location": "Cold"}
    rid = admin.post("/api/production/drafts", body).json["run"]["id"]
    admin.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": IBC, "qty": 1})
    r = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert r.status == 400 and "required fields are missing" in r.text


def test_received_dates_cannot_be_in_the_future(srv, people):
    admin = people["admin"]
    cid = item_id(admin, "50 mL falcon tube")
    assert admin.post("/api/consumables/%d/receive" % cid, {"qty": 5, "supplierLot": "FUTURE", "receivedDate": "2999-01-01"}).status == 400
