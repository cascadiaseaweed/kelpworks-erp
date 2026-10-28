"""KW-94: the release workflow made visible -- a status line, a checklist, the reason a lot is on hold, and a guarded "Release from hold".

The story that raised it: a required lab result was voided AFTER release, the lots went on hold, the run still said "Released" and nobody could see why.
"""
import base64
import pathlib

import pytest

from factories import MICROBIAL, finished_run, lab_report, lots_of, release_ready_results, release_run, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    am, am_info = srv.make_user(role="user", canAmendLog=True)
    return {"plain": plain, "pm": pm, "qm": qm, "amender": am, "admin": srv.admin_client(),
            "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "am_pw": am_info["password"]}


def released(srv, people):
    rid = finished_run(people["admin"])
    lots = release_run(srv, rid, people["pm"], people["pm_pw"], people["qm"], people["qm_pw"])
    assert lots and all(l["status"] == "on_hand" for l in lots), lots
    return rid, lots


def detail(client, rid):
    r = client.get("/api/release/runs/%d" % rid)
    assert r.status == 200, r.text
    return r.json


def listed(client, rid):
    return [x for x in client.get("/api/release").json["runs"] if x["id"] == rid][0]


def checks(d):
    return {c["key"]: c["state"] for c in d["checklist"]}


def void_salmonella(people, rid, reason="wrong sample"):
    results = people["qm"].get("/api/production/%d/lab-results" % rid).json["results"]
    salmonella = [x for x in results if x["specCode"] == "salm" and not x.get("voidedAt")][0]
    r = people["qm"].post("/api/production/%d/lab-results/%d/void" % (rid, salmonella["id"]), {"reason": reason})
    assert r.status == 200, r.text


def replacement_result(people, rid):
    r = people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "T-9", "attachmentId": lab_report(people["qm"], rid),
                                                                    "results": [{"specCode": "salm", "value": MICROBIAL["salm"]}]})
    assert r.status == 200, r.text


def held_after_void(srv, people):
    """A released run whose Salmonella result was voided: the lots are on hold, the run still says released."""
    rid, lots = released(srv, people)
    void_salmonella(people, rid)
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}
    return rid, lots


def lot_status(srv, lot_id):
    return sql(srv, "SELECT status FROM fg_lots WHERE id=?", (lot_id,))[0][0]


def events(srv, rid, event_type):
    return sql(srv, "SELECT user_name, capacity, comment, detail FROM release_events WHERE run_id=? AND event_type=? ORDER BY id", (rid, event_type))


# ---- the status line, state by state ----

def test_status_line_follows_the_workflow(srv, people):
    qm = people["qm"]
    rid = finished_run(people["admin"])
    d = detail(qm, rid)
    assert d["releaseStatus"]["stage"] == "Pending review" and d["releaseStatus"]["waitingOn"] == "Production Manager"
    assert checks(d)["review"] == "fail" and checks(d)["log"] == "na"
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    d = detail(qm, rid)
    assert d["releaseStatus"]["stage"] == "Awaiting QA release" and d["releaseStatus"]["waitingOn"] == "Quality Manager"
    assert "not met" in d["releaseStatus"]["next"] and "Salmonella" in d["releaseStatus"]["next"]       # nothing is on file yet: the status says what is missing
    assert checks(d)["review"] == "ok" and checks(d)["gate"] == "fail"
    release_ready_results(srv, qm, rid)
    d = detail(qm, rid)
    assert checks(d)["gate"] == "ok" and "Every release-gate test is met" in d["releaseStatus"]["next"]
    qm.post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"], "comment": "Test data: TDS and pH are not set to specification"}).raise_for_status()
    d = detail(qm, rid)
    assert d["releaseStatus"]["stage"] == "Released" and d["releaseStatus"]["tone"] == "ok" and d["releaseStatus"]["heldLots"] == 0
    assert set(checks(d).values()) == {"ok"}                                                       # a clean released run: every check is green
    assert listed(qm, rid)["releaseStatus"]["stage"] == "Released" and {c["state"] for c in listed(qm, rid)["checklist"]} == {"ok"}


def test_the_comment_check_is_amber_until_the_lab_results_are_in(srv, people):
    """The production log's own pH / TDS must not turn "out-of-specification results have a comment" green: it stays amber until the results are known."""
    qm = people["qm"]
    rid = finished_run(people["admin"])
    sql(srv, "UPDATE production_runs SET packaging_qc_ph=3.5, packaging_tds_pct=1.5 WHERE id=?", (rid,))       # the log alone shows nothing out of specification
    c = [x for x in detail(qm, rid)["checklist"] if x["key"] == "comment"][0]
    assert c["state"] == "warn" and "Results still to come" in c["reason"] and "Salmonella" in c["reason"] and c["action"] == "lab"
    # results on file but no lab report attached: still not known
    results = [{"specCode": k, "value": v} for k, v in dict(MICROBIAL).items()]
    people["qm"].post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "T-0", "results": results})
    c = [x for x in detail(qm, rid)["checklist"] if x["key"] == "comment"][0]
    assert c["state"] == "warn" and "Results still to come" in c["reason"]                                        # the metals are still missing
    release_ready_results(srv, qm, rid)                                                                          # every gated result is in, with its report
    c = [x for x in detail(qm, rid)["checklist"] if x["key"] == "comment"][0]
    assert c["state"] == "ok" and "No out-of-specification" in c["reason"]
    # a result voided after release is open again
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    qm.post("/api/release/runs/%d/release" % rid, {"decision": "release", "password": people["qm_pw"]}).raise_for_status()
    assert [x for x in detail(qm, rid)["checklist"] if x["key"] == "comment"][0]["state"] == "ok"
    void_salmonella(people, rid)
    c = [x for x in detail(qm, rid)["checklist"] if x["key"] == "comment"][0]
    assert c["state"] == "warn" and "Salmonella" in c["reason"]
    assert [x for x in listed(qm, rid)["checklist"] if x["key"] == "comment"][0]["state"] == "warn"                # the list agrees with the window


def test_returned_and_rejected_runs_say_who_and_why(srv, people):
    rid = finished_run(people["admin"])
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "return", "comment": "Tank 5A level missing", "password": people["pm_pw"]}).raise_for_status()
    s = detail(people["qm"], rid)["releaseStatus"]
    assert s["stage"] == "Returned for correction" and "Tank 5A level missing" in s["cause"] and s["waitingOn"] == "Production team"
    rid2 = finished_run(people["admin"])
    people["pm"].post("/api/release/runs/%d/review" % rid2, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    people["qm"].post("/api/release/runs/%d/release" % rid2, {"decision": "reject", "comment": "Odour not acceptable", "password": people["qm_pw"]}).raise_for_status()
    d = detail(people["qm"], rid2)
    assert d["releaseStatus"]["stage"] == "Rejected - on hold" and "Odour not acceptable" in d["releaseStatus"]["cause"] and "Reopen" in d["releaseStatus"]["next"]
    info = [l["holdInfo"] for l in d["lots"]][0]
    assert info["kind"] == "rejected" and "Odour not acceptable" in info["text"] and info["by"]


def test_an_amendment_is_shown_as_the_reason_the_run_is_waiting(srv, people):
    rid, lots = released(srv, people)
    r = people["amender"].post("/api/production/%d/amendments" % rid, {"category": "late_entry", "reason": "adding the missing pH reading", "password": people["am_pw"]})
    assert r.status == 200, r.text
    d = detail(people["qm"], rid)
    assert d["releaseStatus"]["stage"] == "Under amendment" and "adding the missing pH reading" in d["releaseStatus"]["cause"]
    assert checks(d)["amendment"] == "fail" and checks(d)["review"] == "fail"


# ---- the PR-20260727-001 story: voided after release ----

def test_a_lab_result_voided_after_release_is_explained_everywhere(srv, people):
    rid, lots = held_after_void(srv, people)
    qm = people["qm"]
    d = detail(qm, rid)
    s = d["releaseStatus"]
    assert d["state"] == "released"                                                                # the run still says released ...
    assert s["stage"].startswith("Released - ") and "on hold" in s["stage"] and s["heldLots"] == len(lots) and s["tone"] == "danger"
    assert "Salmonella" in s["cause"] and "Lab result changed after release" in s["cause"] and s["waitingOn"] == "Quality Manager"     # ... but now says why
    assert s["conformsNow"] is False and "no result on file" in s["next"]
    assert checks(d)["gate"] == "fail" and checks(d)["hold"] == "fail"
    assert "Salmonella" in [c for c in d["checklist"] if c["key"] == "hold"][0]["reason"]
    info = d["lots"][0]["holdInfo"]
    assert info["kind"] == "lab" and info["at"] and info["conformsNow"] is False
    assert info["by"] and "Salmonella" in info["summary"]
    # the same reason on the Finished Goods page, for a plain user too, and nowhere on lots that are not on hold
    fg = {x["id"]: x for x in people["plain"].get("/api/fg").json["fg"]}
    assert all(fg[l["id"]]["holdInfo"]["kind"] == "lab" for l in lots)
    other, _ = released(srv, people)
    fg = {x["id"]: x for x in people["plain"].get("/api/fg").json["fg"]}
    assert all("holdInfo" not in fg[l["id"]] for l in lots_of(srv, other))
    assert people["plain"].get("/api/release/runs/%d/recall" % rid).json["lots"][0]["holdInfo"]["kind"] == "lab"
    # the list: the run appears with its status and the red gate check
    row = listed(qm, rid)
    assert row["releaseStatus"]["heldLots"] == len(lots) and [c for c in row["checklist"] if c["key"] == "gate"][0]["state"] == "fail"
    assert [h["lot"] for h in row["heldLots"]] == [l["number"] for l in lots]


def test_a_replacement_result_makes_the_gate_pass_but_does_not_lift_the_hold(srv, people):
    rid, lots = held_after_void(srv, people)
    replacement_result(people, rid)
    d = detail(people["qm"], rid)
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}                                    # lifting a hold is a deliberate act
    assert d["releaseStatus"]["conformsNow"] is True and checks(d)["gate"] == "ok" and checks(d)["hold"] == "fail"       # the gate is met; the one thing left is the lift
    assert "can release the lots from hold" in d["releaseStatus"]["next"]
    assert d["lots"][0]["holdInfo"]["conformsNow"] is True


# ---- Release from hold ----

def test_release_from_hold_is_blocked_until_the_gate_is_met_then_works(srv, people):
    rid, lots = held_after_void(srv, people)
    qm = people["qm"]
    url = "/api/release/runs/%d/release-from-hold" % rid
    r = qm.post(url, {"reason": "The replacement result is in"})
    assert r.status == 409 and r.json["code"] == "release_gate" and "Salmonella" in r.json["error"]
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"} and len(events(srv, rid, "fg_lot_edited")) == 0
    replacement_result(people, rid)
    assert qm.post(url, {"reason": ""}).status == 400                                              # a reason is required
    assert qm.post(url, {"reason": "ok"}).status == 400                                            # ... of at least three characters
    r = qm.post(url, {"reason": "Replacement Salmonella result is negative"})
    assert r.status == 200, r.text
    assert {l["status"] for l in lots_of(srv, rid)} == {"on_hand"}
    ev = events(srv, rid, "fg_lot_edited")
    assert len(ev) == len(lots) and all(e[1] == "Quality Manager" and "Replacement Salmonella" in e[2] and '"via":"release_from_hold"' in e[3].replace(" ", "") for e in ev)
    assert r.json["releaseStatus"]["stage"] == "Released" and r.json["heldLots"] == []
    assert people["admin"].get("/api/release/verify").json["ok"] is True                           # the audit chain still verifies
    assert qm.post(url, {"reason": "again"}).status == 409                                         # nothing is left on hold


def test_only_a_quality_manager_can_release_from_hold(srv, people):
    rid, lots = held_after_void(srv, people)
    replacement_result(people, rid)
    url = "/api/release/runs/%d/release-from-hold" % rid
    for who in ("plain", "pm", "amender", "admin"):
        assert people[who].post(url, {"reason": "looks fine to me"}).status == 403, who
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}


def test_release_from_hold_takes_only_the_lots_asked_for_and_checks_them(srv, people):
    rid, lots = held_after_void(srv, people)
    replacement_result(people, rid)
    url = "/api/release/runs/%d/release-from-hold" % rid
    other_rid, other_lots = released(srv, people)
    assert people["qm"].post(url, {"reason": "test", "lotIds": [other_lots[0]["id"]]}).status == 400      # a lot of another run
    assert people["qm"].post(url, {"reason": "test", "lotIds": ["x"]}).status == 400
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}
    r = people["qm"].post(url, {"reason": "release one lot", "lotIds": [lots[0]["id"]]})
    assert r.status == 200, r.text
    statuses = {l["id"]: l["status"] for l in lots_of(srv, rid)}
    assert statuses[lots[0]["id"]] == "on_hand"
    assert all(s == "hold" for i, s in statuses.items() if i != lots[0]["id"])


def test_a_rejected_runs_lots_are_not_lifted_this_way(srv, people):
    rid = finished_run(people["admin"])
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    people["qm"].post("/api/release/runs/%d/release" % rid, {"decision": "reject", "comment": "Odour", "password": people["qm_pw"]}).raise_for_status()
    r = people["qm"].post("/api/release/runs/%d/release-from-hold" % rid, {"reason": "try anyway"})
    assert r.status == 409 and "reopen" in r.json["error"].lower()
    assert {l["status"] for l in lots_of(srv, rid)} == {"hold"}


# ---- the hand edit follows the same rule ----

def test_lifting_a_hold_by_hand_needs_the_gate_to_pass_on_a_released_run(srv, people):
    rid, lots = held_after_void(srv, people)
    lid = lots[0]["id"]
    r = people["qm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "customer needs it"})
    assert r.status == 409 and r.json["code"] == "release_gate" and lot_status(srv, lid) == "hold"
    replacement_result(people, rid)
    r = people["qm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "replacement result is in"})
    assert r.status == 200, r.text
    assert lot_status(srv, lid) == "on_hand" and "holdInfo" not in r.json["fg"]


def test_a_manual_hold_shows_its_reason_and_a_lift_clears_it(srv, people):
    rid, lots = released(srv, people)
    lid = lots[0]["id"]
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "hold", "reason": "Customer complaint about odour"}).status == 200
    d = detail(people["plain"], rid)
    info = [l for l in d["lots"] if l["id"] == lid][0]["holdInfo"]
    assert info["kind"] == "manual" and "Customer complaint about odour" in info["text"] and info["conformsNow"] is True      # the gate is met, the hold is the Quality Manager's call
    assert d["releaseStatus"]["stage"].startswith("Released - ") and "Customer complaint about odour" in d["releaseStatus"]["cause"]
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "Complaint closed"}).status == 200          # the gate passes: the hand edit still works
    d = detail(people["plain"], rid)
    assert d["releaseStatus"]["stage"] == "Released" and all("holdInfo" not in l for l in d["lots"])
    # held again: the NEWEST cause is the one shown
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "hold", "reason": "Second complaint"}).status == 200
    info = [l for l in detail(people["plain"], rid)["lots"] if l["id"] == lid][0]["holdInfo"]
    assert "Second complaint" in info["text"] and "Customer complaint" not in info["text"]


def test_a_hold_with_no_event_says_so_instead_of_guessing(srv, people):
    rid, lots = released(srv, people)
    sql(srv, "UPDATE fg_lots SET status='hold' WHERE id=?", (lots[0]["id"],))
    info = [l for l in detail(people["qm"], rid)["lots"] if l["id"] == lots[0]["id"]][0]["holdInfo"]
    assert info["kind"] == "unknown" and "No reason recorded" in info["text"] and info["by"] is None


def test_grandfathered_runs_and_runless_lots_are_not_gated(srv, people):
    rid, lots = released(srv, people)
    sql(srv, "UPDATE production_runs SET release_state='legacy' WHERE id=?", (rid,))              # a run from before the release workflow: no results to judge
    lid = lots[0]["id"]
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "hold", "reason": "suspect"}).status == 200
    assert people["qm"].put("/api/fg/%d" % lid, {"status": "on_hand", "reason": "checked"}).status == 200
    assert detail(people["qm"], rid)["releaseStatus"]["conformsNow"] is True                       # an older (pre-gate) release is still judged by the older rule, as before
    sql(srv, "UPDATE production_runs SET is_historical=1 WHERE id=?", (rid,))                      # grandfathered when it was entered: the question does not apply at all
    assert detail(people["qm"], rid)["releaseStatus"]["conformsNow"] is None


# ---- nothing is changed by looking ----

def test_reading_the_screens_changes_nothing(srv, people):
    rid, lots = held_after_void(srv, people)
    before = (sql(srv, "SELECT COUNT(*) FROM release_events")[0][0], sql(srv, "SELECT COUNT(*) FROM consumable_txns")[0][0],
              sql(srv, "SELECT release_review_hash, release_state FROM production_runs WHERE id=?", (rid,))[0],
              [tuple(r) for r in sql(srv, "SELECT id, status, qty FROM fg_lots ORDER BY id")])
    for path in ("/api/release", "/api/release/runs/%d" % rid, "/api/fg", "/api/release/runs/%d/recall" % rid):
        assert people["qm"].get(path).status == 200, path
    after = (sql(srv, "SELECT COUNT(*) FROM release_events")[0][0], sql(srv, "SELECT COUNT(*) FROM consumable_txns")[0][0],
             sql(srv, "SELECT release_review_hash, release_state FROM production_runs WHERE id=?", (rid,))[0],
             [tuple(r) for r in sql(srv, "SELECT id, status, qty FROM fg_lots ORDER BY id")])
    assert before == after
    assert people["qm"].get("/api/release/runs/%d" % rid).json["logMatchesReview"] is True


# ---- the screens (static checks: app.js is a static file) ----

def flat(name):
    return " ".join((PUBLIC / name).read_text(encoding="utf-8").split())


def test_the_screens_draw_the_status_the_checklist_and_the_hold_reason():
    js = flat("app.js")
    for needle in ("function releaseStatusBlock(", "function releaseChecklistView(", "function releaseCheckDots(", "function holdWhy(", "const fgStatusWithWhy",
                   "releaseStatusBlock(d.releaseStatus)", "'Release checklist'", "function releaseFromHoldBox()", "'/release-from-hold'",
                   "'Released, but lots are on hold — needs attention'", "releaseCheckDots(x.checklist)", "' to do'", "fgStatusWithWhy(f)", "holdWhy(l.holdInfo)", "f.holdInfo.summary"):
        assert needle in js, needle
    # the Release-from-hold box is only built for a Quality Manager, and its button starts disabled unless the gate is met
    assert "me.canRelease) body.append(releaseFromHoldBox())" in js and "btn.disabled = !gateMet" in js
    css = flat("styles.css")
    for needle in (".stage-step.fail", ".release-status", ".hold-why", ".chk-dot.ok", ".chk-dot.fail"):
        assert needle in css, needle
