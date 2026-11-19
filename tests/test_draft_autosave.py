"""KW-101: a production run in progress remembers the run header, the tote selection and every tote card's feedstock entries (PUT /api/production/drafts/<id>/autosave), so a
time-out or a dead tablet does not lose what was typed. The autosave only REMEMBERS (draft_data): it never locks a tote to the run, never rejects one, writes no Feedstock Stability
log line and moves no stock; that stays with the tote card's own Save, Save & close and Finalize."""
import re
from pathlib import Path

import pytest

from factories import feedstock_detail, finished_run, harvest_totes, sql
from harness import Server

APP_JS = (Path(__file__).resolve().parent.parent / "public" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def new_draft(admin, **extra):
    body = dict({"sku": "KELPIVEX", "toteIds": [], "feedstockDetails": {}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}, **extra)
    r = admin.post("/api/production/drafts", body)
    assert r.status == 200, r.text
    return r.json["run"]["id"]


def autosave(admin, rid, **body):
    return admin.put("/api/production/drafts/%d/autosave" % rid, body)


def draft(admin, rid):
    r = admin.get("/api/production/drafts/%d" % rid)
    assert r.status == 200, r.text
    return r.json["run"]


def counts(srv, totes):
    marks = ",".join("?" * len(totes))
    return {
        "log": sql(srv, "SELECT COUNT(*) FROM tote_stability_log WHERE tote_lot_id IN (%s)" % marks, totes)[0][0],
        "inputs": sql(srv, "SELECT COUNT(*) FROM run_inputs WHERE tote_lot_id IN (%s)" % marks, totes)[0][0],
        "txns": sql(srv, "SELECT COUNT(*) FROM consumable_txns")[0][0],
        "totes": sql(srv, "SELECT id, status, location, run_id FROM tote_lots WHERE id IN (%s) ORDER BY id" % marks, totes),
    }


def test_the_entries_and_the_selection_are_remembered_and_come_back_on_resume(srv, admin):
    a, b = harvest_totes(admin, 2)
    rid = new_draft(admin)
    typed = {str(a): {"ph": 4.1, "orp": 120, "odour": "Mild", "notes": "dark edge", "decision": "", "surfacePhotoId": "123"},
             str(b): {"ph": 3.9, "decision": "accepted", "weightKg": 187.5}}
    r = autosave(admin, rid, toteIds=[a, b], feedstockDetails=typed)
    assert r.status == 200 and r.json["ok"] is True and r.json["toteIds"] == [a, b], r.text
    run = draft(admin, rid)
    assert run["toteIds"] == [a, b]
    assert run["feedstockDetails"][str(a)] == typed[str(a)] and run["feedstockDetails"][str(b)] == typed[str(b)]       # photo link, blank decision and notes included
    assert run["feedstockDetails"][str(a)]["decision"] == ""                                                           # nothing is defaulted
    assert any(d["id"] == rid and d["toteIds"] == [a, b] for d in admin.get("/api/production/drafts").json["drafts"])  # the In progress list shows them too


def test_it_only_remembers_nothing_is_locked_logged_or_moved(srv, admin):
    a, b = harvest_totes(admin, 2)
    rid = new_draft(admin)
    before = counts(srv, [a, b])
    r = autosave(admin, rid, toteIds=[a, b], feedstockDetails={str(a): feedstock_detail(), str(b): dict(feedstock_detail(), ph=4.4)})
    assert r.status == 200, r.text
    after = counts(srv, [a, b])
    assert after == before                                                                                       # no log line, no run input, no stock line
    assert [t[1] for t in after["totes"]] == ["in_stock", "in_stock"] and all(t[3] is None for t in after["totes"])  # not WIP, not tied to the run


def test_a_decision_still_being_made_never_rejects_or_moves_the_tote(srv, admin):
    a, b = harvest_totes(admin, 2)
    rid = new_draft(admin)
    typed = {str(a): feedstock_detail(), str(b): dict(feedstock_detail(), decision="rejected", rejectionReason="off odour")}
    before = counts(srv, [a, b])
    assert autosave(admin, rid, toteIds=[a, b], feedstockDetails=typed).status == 200
    assert counts(srv, [a, b]) == before                                                                         # still in stock, location unchanged
    assert draft(admin, rid)["feedstockDetails"][str(b)]["decision"] == "rejected"                               # but what was typed is remembered
    # the explicit save keeps doing what it always did: the rejected tote is pulled out and put on QAQC Hold
    r = admin.put("/api/production/drafts/%d" % rid, {"sku": "KELPIVEX", "toteIds": [a, b], "feedstockDetails": typed, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"})
    assert r.status == 200 and r.json["rejectedToteIds"] == [b], r.text
    assert sql(srv, "SELECT status, location FROM tote_lots WHERE id=?", (b,)) == [("hold", "QAQC Hold")]
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (a,)) == [("wip", rid)]
    assert draft(admin, rid)["toteIds"] == [a]


def test_the_run_header_is_remembered_and_a_blank_date_is_not_replaced_by_today(srv, admin):
    rid = new_draft(admin, notes="first")
    r = autosave(admin, rid, location="Cold", operators="NW", notes="second", runDate="2026-05-06", sku="KELPIVEX", toteIds=[], feedstockDetails={})
    assert r.status == 200, r.text
    run = draft(admin, rid)
    assert (run["runDate"], run["notes"], run["location"], run["operators"], run["sku"]) == ("2026-05-06", "second", "Cold", "NW", "KELPIVEX")
    target = run.get("targetTds")
    r = autosave(admin, rid, runDate="", toteIds=[], feedstockDetails={})                                        # the date field was cleared in the browser
    assert r.status == 200 and draft(admin, rid)["runDate"] == "2026-05-06"
    r = autosave(admin, rid, notes="third")                                                                      # a part left out keeps its stored value
    assert r.status == 200
    run = draft(admin, rid)
    assert run["notes"] == "third" and run["location"] == "Cold" and run["operators"] == "NW" and run["targetTds"] == target
    assert autosave(admin, rid, runDate="06/05/2026").status == 400


def test_a_tote_this_draft_has_locked_is_never_dropped_by_a_stale_window(srv, admin):
    a, b = harvest_totes(admin, 2)
    rid = new_draft(admin)
    r = admin.post("/api/production/%d/feedstock/%d/save" % (rid, a), dict(feedstock_detail(), ph=3.8))            # the tote card's own Save: a is now WIP on this run
    assert r.status == 200 and r.json["rejected"] is False, r.text
    r = autosave(admin, rid, toteIds=[b], feedstockDetails={str(b): feedstock_detail()})                         # a window that does not know about a
    assert r.status == 200 and sorted(r.json["toteIds"]) == sorted([a, b]), r.text
    assert sorted(draft(admin, rid)["toteIds"]) == sorted([a, b])
    assert sql(srv, "SELECT status, run_id FROM tote_lots WHERE id=?", (a,)) == [("wip", rid)]


def test_entries_already_remembered_for_a_tote_survive_a_payload_that_leaves_them_out(srv, admin):
    a, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert autosave(admin, rid, toteIds=[a], feedstockDetails={str(a): {"ph": 4.2, "notes": "kept"}}).status == 200
    assert autosave(admin, rid, toteIds=[a], feedstockDetails={}).status == 200
    assert draft(admin, rid)["feedstockDetails"][str(a)] == {"ph": 4.2, "notes": "kept"}
    assert autosave(admin, rid, toteIds=[], feedstockDetails={}).status == 200                                   # un-ticked: its entries go with it
    run = draft(admin, rid)
    assert run["toteIds"] == [] and run["feedstockDetails"] == {}


def test_the_autosave_is_refused_for_a_finalized_run_an_unknown_draft_bad_input_and_a_stranger(srv, admin):
    done = finished_run(admin)
    r = autosave(admin, done, location="Hot", toteIds=[], feedstockDetails={})
    assert r.status == 409 and r.json.get("code") == "not_a_draft"
    assert autosave(admin, 987654, toteIds=[], feedstockDetails={}).status == 404
    rid = new_draft(admin)
    assert autosave(admin, rid, toteIds=[987654], feedstockDetails={}).status == 400                             # a tote that does not exist
    assert autosave(admin, rid, toteIds=["x"], feedstockDetails={}).status == 400
    assert autosave(admin, rid, toteIds=[], feedstockDetails=[1, 2]).status == 400
    assert autosave(admin, rid, toteIds=[], feedstockDetails={"1": "text"}).status == 400
    assert srv.client().put("/api/production/drafts/%d/autosave" % rid, {"toteIds": [], "feedstockDetails": {}}).status == 401


def test_the_autosave_does_not_touch_the_finalize(srv, admin):
    """What was remembered does not change what Finalize does: that still uses the request body and the explicit lock-in."""
    a, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert autosave(admin, rid, toteIds=[a], feedstockDetails={str(a): {"ph": 9.9}}).status == 200
    assert sql(srv, "SELECT COUNT(*) FROM run_inputs WHERE run_id=?", (rid,)) == [(0,)]
    assert sql(srv, "SELECT status FROM production_runs WHERE id=?", (rid,)) == [("draft",)]


# ---- the screen: what the browser does is checked by reading the source (the behaviour itself in the browser check) ----

def test_every_section_save_goes_through_ensureRunId_which_saves_the_entries_first():
    m = re.search(r"async function ensureRunId\(\) \{(.*?)\n  \}", APP_JS, re.S)
    assert m and "autosaveNow({ interactive: true })" in m.group(1) and "'expired'" in m.group(1)
    assert "buildHomogenizationSection(ensureRunId," in APP_JS and "buildExtractionSection(ensureRunId," in APP_JS


def test_the_idle_timeout_saves_the_run_window_entries_before_signing_out():
    assert "runWindow.kwFlush = () => autosaveNow({ interactive: false })" in APP_JS
    m = re.search(r"async function idleSignOut\(minutes\) \{(.*?)\n\}", APP_JS, re.S)
    assert m and "bg.kwFlush" in m.group(1) and m.group(1).index("kwFlush") < m.group(1).index("pressSectionSaves")


def test_an_expired_session_does_not_wipe_the_window_while_autosaving():
    assert "extra && extra.keepSession" in APP_JS and "err.code = 'session_expired'" in APP_JS
    m = re.search(r"async function autosaveNow\(o\) \{(.*?)\n  \}\n", APP_JS, re.S)
    assert m and "{ keepSession: true }" in m.group(1) and "reSignIn()" in m.group(1) and "Not saved:" in m.group(1)
    assert "function reSignIn()" in APP_JS and "different account" in APP_JS


def test_a_card_reporting_its_starting_values_is_not_a_change():
    assert "feedstockBuilding = true;" in APP_JS and "if (!feedstockBuilding) scheduleAutosave();" in APP_JS and "feedstockBuilding = false;" in APP_JS
