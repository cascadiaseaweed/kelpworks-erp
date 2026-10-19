"""KW-29 / KW-28: a submitted amendment sends the run back to the Product Release queue with its earlier sign-off voided, and the notes a reviewer must see
(the amendment reason and the amender's note) reach the run and the release screens."""
import pytest

from factories import finished_run, lots_of, release_run, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    am, am_info = srv.make_user(role="user", canAmendLog=True)
    return {"pm": pm, "qm": qm, "amender": am, "admin": srv.admin_client(),
            "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "am_pw": am_info["password"]}


def queue(client):
    return {r["id"]: r for r in client.get("/api/release").json["runs"]}


def amend(people, rid, note="Homogenizer tripped twice; restarted at 10:40", comment="Please check the Extraction times"):
    am = people["amender"]
    opened = am.post("/api/production/%d/amendments" % rid, {"category": "data_entry_error", "reason": "Machine failure was not written down", "password": people["am_pw"]})
    assert opened.status == 200, opened.text
    aid = opened.json["amendment"]["id"]
    assert am.put("/api/production/%d" % rid, {"notes": note}).status == 200
    done = am.post("/api/production/%d/amendments/%d/submit" % (rid, aid), {"comment": comment})
    assert done.status == 200, done.text
    return aid


def test_a_submitted_amendment_puts_a_released_run_back_in_the_review_queue(srv, people):
    rid = finished_run(people["admin"])
    lots = release_run(srv, rid, people["pm"], people["pm_pw"], people["qm"], people["qm_pw"])
    assert queue(people["pm"])[rid]["state"] == "released" and queue(people["pm"])[rid]["releasedBy"]
    amend(people, rid)
    row = queue(people["pm"])[rid]
    assert row["state"] == "pending_review"                                             # in "Awaiting production-log review"
    assert not row["reviewedBy"] and not row["releasedBy"] and not row["releasedAt"]    # the earlier sign-offs no longer stand and are not shown as current
    assert sql(srv, "SELECT release_state, release_review_hash FROM production_runs WHERE id=?", (rid,))[0] == ("pending_review", None)
    assert all(l["status"] == "pending_release" for l in lots_of(srv, rid))             # not shippable until it is reviewed and released again
    types = [e["type"] for e in people["pm"].get("/api/release/runs/%d" % rid).json["events"]]
    assert types[-2:] == ["amendment_opened", "amendment_submitted"]


def test_an_open_amendment_is_listed_as_under_amendment_until_it_is_submitted(srv, people):
    rid = finished_run(people["admin"])
    people["amender"].post("/api/production/%d/amendments" % rid, {"category": "late_entry", "reason": "adding the missing pH reading"}).raise_for_status()
    assert queue(people["pm"])[rid]["state"] == "amending"                              # saving the log alone does not send it to review


def test_the_notes_a_reviewer_needs_reach_the_run_and_the_release_screen(srv, people):
    rid = finished_run(people["admin"])
    amend(people, rid, note="Separator jammed at 11:05, cleaned and resumed")
    run = [r for r in people["pm"].get("/api/production").json["runs"] if r["id"] == rid][0]
    assert run["notes"] == "Separator jammed at 11:05, cleaned and resumed"
    rev = [r for r in run["revisions"] if r["kind"] == "amendment"][-1]
    assert rev["reason"] == "Machine failure was not written down" and rev["comment"] == "Please check the Extraction times"
    shown = people["pm"].get("/api/release/runs/%d" % rid).json["run"]                 # what the review window builds its Notes panel from
    assert shown["notes"] == run["notes"] and [r["comment"] for r in shown["revisions"] if r["kind"] == "amendment"] == ["Please check the Extraction times"]
