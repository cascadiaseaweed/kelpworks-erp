"""KW-51: a tote rejected in the feedstock step goes to QAQC Hold and the run finalizes (even if the finalize request still lists the rejected tote); and an administrator can free a tote
that is stuck as work in progress, with the change attributed in the tote's Feedstock Stability log and in the change log."""
import json

import pytest

from factories import feedstock_detail, finalize_run, harvest_totes, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    return {"admin": srv.admin_client(), "plain": plain}


def reject(admin, rid, tote):
    r = admin.post("/api/production/%d/feedstock/%d/save" % (rid, tote), dict(feedstock_detail(), decision="rejected", rejectionReason="off odour"))
    assert r.status == 200 and r.json["rejected"] is True, r.text


def finalize_body(totes, details):
    return {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}


def test_a_rejected_tote_goes_to_hold_and_the_run_finalizes_without_it(srv, people):
    admin = people["admin"]
    good, bad = harvest_totes(admin, 2)
    rid, _ = finalize_run(admin, [good, bad], finalize=False)
    reject(admin, rid, bad)
    assert sql(srv, "SELECT status, location, run_id FROM tote_lots WHERE id=?", (bad,)) == [("hold", "QAQC Hold", None)]
    r = admin.post("/api/production/drafts/%d/finalize" % rid, finalize_body([good], {str(good): feedstock_detail()}))
    assert r.status == 200, r.text
    assert sql(srv, "SELECT status FROM tote_lots WHERE id IN (?,?) ORDER BY id", (good, bad)) == [("consumed",), ("hold",)]


def test_the_finalize_still_works_when_the_request_lists_the_rejected_tote(srv, people):
    admin = people["admin"]
    good, bad = harvest_totes(admin, 2)
    rid, _ = finalize_run(admin, [good, bad], finalize=False)
    reject(admin, rid, bad)
    details = {str(good): feedstock_detail(), str(bad): dict(feedstock_detail(), decision="rejected", rejectionReason="off odour")}
    r = admin.post("/api/production/drafts/%d/finalize" % rid, finalize_body([good, bad], details))
    assert r.status == 200, r.text
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (bad,)) == [("hold", None)]                 # still on hold, not part of the run
    assert sql(srv, "SELECT COUNT(*) FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (rid, bad)) == [(0,)]
    # a tote on hold that was NOT rejected from this run is still refused
    other, = harvest_totes(admin, 1)
    sql(srv, "UPDATE tote_lots SET status='hold' WHERE id=?", (other,))
    third, = harvest_totes(admin, 1)
    rid2, _ = finalize_run(admin, [third], finalize=False)
    r = admin.post("/api/production/drafts/%d/finalize" % rid2, finalize_body([other], {str(other): feedstock_detail()}))
    assert r.status == 400 and "not available" in r.json["error"]


def test_an_administrator_frees_a_tote_that_is_stuck_with_no_run(srv, people):
    admin, plain = people["admin"], people["plain"]
    tote, = harvest_totes(admin, 1)
    sql(srv, "UPDATE tote_lots SET status='wip', run_id=NULL WHERE id=?", (tote,))                 # as if its run vanished
    assert plain.post("/api/totes/%d/free" % tote, {"reason": "not allowed"}).status == 403
    assert admin.post("/api/totes/%d/free" % tote, {"reason": ""}).status == 400                     # a reason is required
    assert admin.post("/api/totes/%d/free" % tote, {"status": "disposed", "reason": "no such choice"}).status == 400
    r = admin.post("/api/totes/%d/free" % tote, {"status": "in_stock", "reason": "run was discarded, never released"})
    assert r.status == 200, r.text
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (tote,)) == [("in_stock", None)]
    log = sql(srv, "SELECT field, old_value, new_value FROM tote_stability_log WHERE tote_lot_id=? AND field IN ('Status','Admin note') ORDER BY id DESC LIMIT 2", (tote,))
    assert ("Admin note", None, "run was discarded, never released") in log or ("Admin note", "", "run was discarded, never released") in log
    assert sql(srv, "SELECT area, key, new_value FROM config_log WHERE area='tote_status' ORDER BY id DESC LIMIT 1")[0][1:] == (sql(srv, "SELECT lot_number FROM tote_lots WHERE id=?", (tote,))[0][0], "in_stock")
    assert admin.post("/api/totes/%d/free" % tote, {"reason": "again, to be sure"}).status == 409        # it is not stuck any more


def test_freeing_a_tote_of_a_run_in_progress_takes_it_out_of_that_run(srv, people):
    admin = people["admin"]
    keep, stuck = harvest_totes(admin, 2)
    rid, _ = finalize_run(admin, [keep, stuck], finalize=False)                                      # both are locked to the draft
    assert admin.post("/api/totes/%d/free" % stuck, {"status": "hold", "reason": "operator left it in the run by mistake"}).status == 200
    assert sql(srv, "SELECT status, location, run_id FROM tote_lots WHERE id=?", (stuck,)) == [("hold", "QAQC Hold", None)]
    assert not sql(srv, "SELECT 1 FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (rid, stuck))
    dd = json.loads(sql(srv, "SELECT draft_data FROM production_runs WHERE id=?", (rid,))[0][0])
    assert stuck not in dd["toteIds"] and str(stuck) not in dd["feedstockDetails"]
    r = admin.post("/api/production/drafts/%d/finalize" % rid, finalize_body([keep], {str(keep): feedstock_detail()}))
    assert r.status == 200, r.text


def test_a_consumed_tote_cannot_be_freed(srv, people):
    admin = people["admin"]
    totes = harvest_totes(admin, 1)
    rid, f = finalize_run(admin, totes)
    assert f.status == 200
    r = admin.post("/api/totes/%d/free" % totes[0], {"reason": "it was used in a finished run"})
    assert r.status == 409 and r.json["code"] == "not_stuck"
