"""KW-103: a tote cannot be added to a production run until its average harvest weight is recorded, and the run keeps its own Average harvest weight (kg) for each tote
(run_inputs.avg_harvest_weight_kg). The run's input kg is the sum of those values; a finalized run's value changes only under an amendment, and then also moves the run's input kg
(and so the harvest conversion rate) and the tote's own recorded average. A run finalized before keeps its signed log: the key shows only when it has a value."""
import pytest

import kelp_erp_server as K
from factories import feedstock_detail, finished_run, harvest_totes, sql, stage_payloads
from harness import Server

AVG = "Average harvest weight (kg)"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def unweighted(srv, admin, n=1, value=None):
    totes = harvest_totes(admin, n)
    for t in totes:
        sql(srv, "UPDATE tote_lots SET avg_weight_kg=? WHERE id=?", (value, t))
    return totes


def new_draft(client, **extra):
    body = dict({"sku": "KELPIVEX", "toteIds": [], "feedstockDetails": {}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}, **extra)
    r = client.post("/api/production/drafts", body)
    assert r.status == 200, r.text
    return r.json["run"]["id"]


def lock_in(client, rid, tote, **extra):
    return client.post("/api/production/%d/feedstock/%d/save" % (rid, tote), dict(feedstock_detail(), **extra))


def run_of(client, rid):
    return [r for r in client.get("/api/production").json["runs"] if r["id"] == rid][0]


def tote_avg(srv, tote):
    return sql(srv, "SELECT avg_weight_kg FROM tote_lots WHERE id=?", (tote,))[0][0]


def run_avg(srv, rid):
    return [r[0] for r in sql(srv, "SELECT avg_harvest_weight_kg FROM run_inputs WHERE run_id=? ORDER BY tote_lot_id", (rid,))]


def finalize_with(client, totes, values=None):
    """Draft a run on these totes with the given Average harvest weight per tote (None = the card sent none), fill the log and finalize. Returns (run id, finalize response)."""
    details = {}
    for i, t in enumerate(totes):
        d = feedstock_detail()
        if values and values[i] is not None:
            d["avgHarvestWeightKg"] = values[i]
        details[str(t)] = d
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    rid = client.post("/api/production/drafts", body).json["run"]["id"]
    for stage, payload in stage_payloads().items():
        client.put("/api/production/%d/stages/%s" % (rid, stage), payload)
    client.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": "New 1,000 L IBC Tote", "qty": 1}).raise_for_status()
    return rid, client.post("/api/production/drafts/%d/finalize" % rid, body)


def amender(srv, rid, reason="the harvest weight was wrong"):
    am, info = srv.make_user(role="user", canAmendLog=True)
    opened = am.post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": reason, "password": info["password"]})
    assert opened.status == 200, opened.text
    return am, opened.json["amendment"]["id"]


# ---- the rule -------------------------------------------------------------------------------------------------------------------------------------------------- #

def test_a_tote_with_no_average_harvest_weight_cannot_be_added_to_a_run_by_any_path(srv, admin):
    t, = unweighted(srv, admin)
    lot = sql(srv, "SELECT lot_number FROM tote_lots WHERE id=?", (t,))[0][0]
    rid = new_draft(admin)
    r = lock_in(admin, rid, t)                                                                                     # the card's own Save
    assert r.status == 400 and r.json["code"] == "harvest_weight_missing" and lot in r.json["error"], r.text
    body = {"sku": "KELPIVEX", "toteIds": [t], "feedstockDetails": {str(t): feedstock_detail()}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.put("/api/production/drafts/%d" % rid, body)                                                           # Save & close
    assert r.status == 400 and r.json["code"] == "harvest_weight_missing"
    r = admin.post("/api/production/drafts/%d/finalize" % rid, body)                                                 # Finalize
    assert r.status == 400 and r.json["code"] == "harvest_weight_missing"
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (t,)) == [("in_stock", None)]                 # nothing was locked or changed
    assert sql(srv, "SELECT COUNT(*) FROM run_inputs WHERE tote_lot_id=?", (t,)) == [(0,)]


def test_zero_counts_as_no_weight_and_recording_one_lets_the_tote_in(srv, admin):
    t, = unweighted(srv, admin, value=0)
    rid = new_draft(admin)
    assert lock_in(admin, rid, t).status == 400
    assert admin.post("/api/totes/set-bulk", {"ids": [t], "avgWeightKg": 210, "note": "weighed"}).status == 200      # Feedstock Inventory > Set values for selected
    r = lock_in(admin, rid, t)
    assert r.status == 200 and r.json["rejected"] is False, r.text
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (t,)) == [("wip", rid)]


def test_a_tote_already_locked_to_the_draft_before_the_rule_keeps_working(srv, admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert lock_in(admin, rid, t).status == 200
    sql(srv, "UPDATE tote_lots SET avg_weight_kg=NULL WHERE id=?", (t,))                                              # as if the draft pre-dates the rule
    assert lock_in(admin, rid, t).status == 200
    body = {"sku": "KELPIVEX", "toteIds": [t], "feedstockDetails": {str(t): feedstock_detail()}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    assert admin.put("/api/production/drafts/%d" % rid, body).status == 200


def test_a_historical_run_is_exempt(srv, admin):
    assert admin.put("/api/settings/historical_entry_enabled", {"value": 1}).status == 200
    try:
        t, = unweighted(srv, admin)
        rid = new_draft(admin, runDate="2026-05-06", historical={"processingLot": "PR-20260506-901", "release": "grandfather", "deductInventory": False})
        r = lock_in(admin, rid, t)
        assert r.status == 200 and r.json["rejected"] is False, r.text
    finally:
        admin.put("/api/settings/historical_entry_enabled", {"value": 0})


def test_check_in_can_still_leave_the_weight_blank(srv, admin):
    t, = unweighted(srv, admin)
    assert tote_avg(srv, t) is None and sql(srv, "SELECT status FROM tote_lots WHERE id=?", (t,)) == [("in_stock",)]


# ---- the run's own value -------------------------------------------------------------------------------------------------------------------------------- #

def test_the_run_starts_from_the_totes_average_and_stores_its_own_value(srv, admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert lock_in(admin, rid, t).status == 200                                                                      # the card sent no value
    assert run_avg(srv, rid) == [200.0] and tote_avg(srv, t) == 200.0
    assert sql(srv, "SELECT COUNT(*) FROM tote_stability_log WHERE tote_lot_id=? AND field=?", (t, AVG)) == [(0,)]


def test_a_value_changed_in_the_draft_is_also_changed_on_the_tote_and_logged(srv, admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert lock_in(admin, rid, t, avgHarvestWeightKg=150.5).status == 200
    assert run_avg(srv, rid) == [150.5] and tote_avg(srv, t) == 150.5
    line = sql(srv, "SELECT old_value, new_value, run_id, user_name FROM tote_stability_log WHERE tote_lot_id=? AND field=?", (t, AVG))
    assert len(line) == 1 and line[0][1] == "150.5" and line[0][2] == rid and line[0][3]
    for bad in (0, -5, "abc", 5001):
        r = lock_in(admin, rid, t, avgHarvestWeightKg=bad)
        assert r.status == 400 and r.json["code"] == "bad_harvest_weight", (bad, r.text)
    assert run_avg(srv, rid) == [150.5] and tote_avg(srv, t) == 150.5                                                # a bad value changed nothing


def test_the_runs_input_kg_is_the_sum_of_its_own_values(srv, admin):
    a, b = harvest_totes(admin, 2)
    rid, f = finalize_with(admin, [a, b], [150, 250])
    assert f.status == 200, f.text
    assert run_of(admin, rid)["inputKg"] == 400.0
    assert run_avg(srv, rid) == [150.0, 250.0] and (tote_avg(srv, a), tote_avg(srv, b)) == (150.0, 250.0)
    assert [i["avgHarvestWeightKg"] for i in run_of(admin, rid)["inputs"]] == [150.0, 250.0]
    c, d = harvest_totes(admin, 2)
    rid2, f2 = finalize_with(admin, [c, d])                                                                          # no values sent: the totes' averages
    assert f2.status == 200 and run_of(admin, rid2)["inputKg"] == 400.0


def test_a_rejected_tote_adds_nothing_to_the_input(srv, admin):
    a, b = harvest_totes(admin, 2)
    details = {str(a): feedstock_detail(), str(b): dict(feedstock_detail(), decision="rejected", rejectionReason="off odour", avgHarvestWeightKg=999)}
    body = {"sku": "KELPIVEX", "toteIds": [a, b], "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    rid = admin.post("/api/production/drafts", body).json["run"]["id"]
    for stage, payload in stage_payloads().items():
        admin.put("/api/production/%d/stages/%s" % (rid, stage), payload)
    admin.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": "New 1,000 L IBC Tote", "qty": 1}).raise_for_status()
    f = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert f.status == 200, f.text
    assert run_of(admin, rid)["inputKg"] == 200.0


# ---- required-field progress ---------------------------------------------------------------------------------------------------------------------------- #

def test_the_value_is_a_required_field_until_the_entries_are_locked_in(srv, admin):
    assert "avgHarvestWeightKg" in admin.get("/api/refdata").json["requiredFields"]["feedstock"]
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert admin.put("/api/production/drafts/%d/autosave" % rid, {"toteIds": [t], "feedstockDetails": {}}).status == 200
    feed = [s for s in admin.get("/api/production/%d/progress" % rid).json["progress"]["sections"] if s["key"] == "feedstock"][0]
    assert any(m.endswith(": " + AVG) for m in feed["missing"]), feed["missing"]
    assert lock_in(admin, rid, t).status == 200
    feed = [s for s in admin.get("/api/production/%d/progress" % rid).json["progress"]["sections"] if s["key"] == "feedstock"][0]
    assert not any(m.endswith(": " + AVG) for m in feed["missing"]), feed["missing"]


# ---- a finalized run ------------------------------------------------------------------------------------------------------------------------------------ #

def test_a_finalized_runs_value_changes_only_under_an_amendment_and_moves_the_input_kg(srv, admin):
    rid = finished_run(admin)
    run = run_of(admin, rid)
    assert run["inputKg"] == 200.0 and run["inputs"][0]["avgHarvestWeightKg"] == 200.0
    iid, tote = run["inputs"][0]["id"], sql(srv, "SELECT tote_lot_id FROM run_inputs WHERE id=?", (run["inputs"][0]["id"],))[0][0]
    r = admin.put("/api/production/%d/inputs/%d" % (rid, iid), {"avgHarvestWeightKg": 321})
    assert r.status == 409 and r.json["code"] == "amendment_required"                                                # the log is locked
    am, aid = amender(srv, rid)
    assert am.put("/api/production/%d/inputs/%d" % (rid, iid), {"avgHarvestWeightKg": 0}).status == 400
    r = am.put("/api/production/%d/inputs/%d" % (rid, iid), {"avgHarvestWeightKg": 321})
    assert r.status == 200, r.text
    run = run_of(admin, rid)
    assert run["inputKg"] == 321.0 and run["inputs"][0]["avgHarvestWeightKg"] == 321.0                              # the input kg follows (and with it the harvest conversion)
    assert tote_avg(srv, tote) == 321.0
    assert sql(srv, "SELECT new_value, run_id FROM tote_stability_log WHERE tote_lot_id=? AND field=? ORDER BY id DESC LIMIT 1", (tote, AVG)) == [("321", rid)]
    changes = am.get("/api/production/%d/amendments/%d/preview" % (rid, aid)).json["changes"]
    fields = " | ".join(c["field"].lower() for c in changes)
    assert "harvest" in fields and "input" in fields, changes                                                       # the revision shows the value and the input kg
    assert (_c := [c for c in changes if "input" in c["field"].lower()][0])["old"] == "200" and _c["new"] == "321"


def test_a_run_finalized_before_the_rule_keeps_its_signed_log_and_its_input_kg(srv, admin):
    rid = finished_run(admin)
    sql(srv, "UPDATE run_inputs SET avg_harvest_weight_kg=NULL WHERE run_id=?", (rid,))
    sql(srv, "UPDATE production_runs SET harvest_weight_rule=0 WHERE id=?", (rid,))                                  # as if it was finalized before this release
    run = run_of(admin, rid)
    assert "avgHarvestWeightKg" not in run["inputs"][0] and run["inputKg"] == 200.0                                  # no key in the signed log, input kg as it was
    feed = [s for s in run["progress"]["sections"] if s["key"] == "feedstock"][0]
    assert feed["done"] is True and not any(AVG in m for m in feed["missing"])                                       # not asked for it
    am, _aid = amender(srv, rid)
    iid = run["inputs"][0]["id"]
    assert am.put("/api/production/%d/inputs/%d" % (rid, iid), {"notes": "re-read the label"}).status == 200
    run = run_of(admin, rid)
    assert "avgHarvestWeightKg" not in run["inputs"][0] and run["inputKg"] == 200.0                                  # an unrelated amendment adds nothing and moves nothing
    assert am.put("/api/production/%d/inputs/%d" % (rid, iid), {"avgHarvestWeightKg": 180}).status == 200
    run = run_of(admin, rid)
    assert run["inputs"][0]["avgHarvestWeightKg"] == 180.0 and run["inputKg"] == 180.0


def test_the_new_field_is_a_declared_required_key():
    assert [k for k, _ in K.REQUIRED_FEEDSTOCK_HARVEST] == ["avgHarvestWeightKg"]
    assert "avgHarvestWeightKg" in K.required_keys_by_stage()["feedstock"]
