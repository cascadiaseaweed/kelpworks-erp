"""Historical readings: the reading date on Update feedstock conditions. The reading with the newest date is the value shown; an older one is kept in the history only."""
import datetime

import pytest

from factories import harvest_totes, sql
from harness import Server

TODAY = datetime.date.today()


def day(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def new_tote(srv, admin):
    tid = harvest_totes(admin, n=1)[0]
    sql(srv, "UPDATE tote_lots SET ph=NULL, ph_updated=NULL, orp=NULL, orp_updated=NULL, tds_pct=NULL, tds_updated=NULL WHERE id=?", (tid,))
    return tid


def reading(admin, tid, ph=None, orp=None, date=None, note=None):
    body = {k: v for k, v in (("ph", ph), ("orp", orp), ("date", date), ("note", note)) if v is not None}
    return admin.post("/api/totes/%d/ph" % tid, body)


def state(admin, tid):
    return admin.get("/api/totes/%d/ph" % tid).json


def rows(admin, tid, field):
    return [e for e in state(admin, tid)["stabilityLog"] if e["field"] == field]


def test_a_reading_with_an_older_date_does_not_replace_the_current_value(srv, admin):
    tid = new_tote(srv, admin)
    assert reading(admin, tid, ph=3.8).status == 200                                      # today
    r = reading(admin, tid, ph=4.6, date=day(7))
    assert r.status == 200 and r.json["historical"] == ["pH"]
    s = state(admin, tid)
    assert (s["ph"], s["phUpdated"]) == (3.8, TODAY.isoformat())
    log = rows(admin, tid, "pH")
    assert len(log) == 2
    old = log[0]                                                                          # newest ENTRY first
    assert old["readingDate"] == day(7) and old["newValue"] == "4.6" and not old["oldValue"] and "Historical reading" in old["note"] and "3.8" in old["note"]
    assert log[1]["readingDate"] == TODAY.isoformat() and log[1]["newValue"] == "3.8"


def test_a_newer_reading_replaces_it_and_the_newest_date_always_wins(srv, admin):
    tid = new_tote(srv, admin)
    reading(admin, tid, ph=4.0, date=day(30)).raise_for_status()                          # the first reading is current however old it is
    assert (state(admin, tid)["ph"], state(admin, tid)["phUpdated"]) == (4.0, day(30))
    assert reading(admin, tid, ph=3.9, date=day(10)).json["historical"] == []
    assert state(admin, tid)["ph"] == 3.9
    assert reading(admin, tid, ph=4.4, date=day(20)).json["historical"] == ["pH"]         # entered later, but taken earlier
    assert state(admin, tid)["ph"] == 3.9 and state(admin, tid)["phUpdated"] == day(10)
    reading(admin, tid, ph=3.7, date=day(10)).raise_for_status()                          # the same day: the later entry wins
    assert state(admin, tid)["ph"] == 3.7
    assert [e["readingDate"] for e in rows(admin, tid, "pH")] == [day(10), day(20), day(10), day(30)]


def test_orp_follows_the_same_rule_independently_of_ph(srv, admin):
    tid = new_tote(srv, admin)
    reading(admin, tid, ph=3.8, orp=120).raise_for_status()
    r = reading(admin, tid, orp=-50, date=day(3))
    assert r.json["historical"] == ["ORP (mV)"]
    s = state(admin, tid)
    assert s["orp"] == 120 and s["ph"] == 3.8
    both = reading(admin, tid, ph=4.1, orp=95, date=day(2))                                # both older than today's readings
    assert both.json["historical"] == ["pH", "ORP (mV)"] and state(admin, tid)["ph"] == 3.8


def test_a_reading_date_in_the_future_is_refused(srv, admin):
    tid = new_tote(srv, admin)
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    assert reading(admin, tid, ph=3.8, date=tomorrow).status == 400
    assert reading(admin, tid, ph=3.8, date="not a date").status == 400
    assert state(admin, tid)["ph"] is None and rows(admin, tid, "pH") == []


def characterize(admin, tid, **body):
    return admin.post("/api/totes/%d/characterize" % tid, dict({"decision": "accepted"}, **body))


def test_tds_and_odour_readings_carry_the_reading_date(srv, admin):
    tid = new_tote(srv, admin)
    assert characterize(admin, tid, tdsPct=12, odour="marine, sweet", odourIntensity="Mild", readingDate=day(1)).status == 200
    s = state(admin, tid)
    assert (s["tdsPct"], s["tdsUpdated"]) == (12, day(1))
    assert {e["field"]: e["readingDate"] for e in s["stabilityLog"] if e["field"] in ("Feedstock TDS (%)", "Odour", "Odour intensity")} == \
        {"Feedstock TDS (%)": day(1), "Odour": day(1), "Odour intensity": day(1)}
    # an older reading of each: kept in the history, nothing current changes
    r = characterize(admin, tid, tdsPct=9, odour="butyric", odourIntensity="Strong", readingDate=day(6))
    assert sorted(r.json["historical"]) == ["Feedstock TDS (%)", "Odour", "Odour intensity"]
    s = state(admin, tid)
    assert (s["tdsPct"], s["tdsUpdated"]) == (12, day(1))
    assert (s["latestCharacterization"]["odour"], s["latestCharacterization"]["odourIntensity"]) == ("marine, sweet", "Mild")
    assert len(rows(admin, tid, "Odour")) == 2
    # a newer one replaces them
    r = characterize(admin, tid, tdsPct=11, odour="sweet", odourIntensity="Medium", readingDate=TODAY.isoformat())
    assert r.json["historical"] == []
    s = state(admin, tid)
    assert (s["tdsPct"], s["latestCharacterization"]["odour"], s["latestCharacterization"]["odourIntensity"]) == (11, "sweet", "Medium")


def test_a_value_the_window_prefills_is_never_logged_again_whatever_the_date(srv, admin):
    tid = new_tote(srv, admin)
    characterize(admin, tid, tdsPct=12, odour="sweet").raise_for_status()
    characterize(admin, tid, tdsPct=12, odour="sweet").raise_for_status()                 # saving the window again with the same values
    r = characterize(admin, tid, tdsPct=12, odour="sweet", readingDate=day(9))            # ... or with an earlier reading date: only what changed is a reading
    assert r.json["historical"] == []
    assert len(rows(admin, tid, "Feedstock TDS (%)")) == 1 and len(rows(admin, tid, "Odour")) == 1
    r = characterize(admin, tid, tdsPct=12, odour="butyric", readingDate=day(9))          # the odour is different: that one is a historical reading, the TDS is not logged again
    assert r.json["historical"] == ["Odour"] and len(rows(admin, tid, "Feedstock TDS (%)")) == 1


def test_grading_uses_the_newest_reading(srv, admin):
    """A grade rule on odour intensity: an older, stronger reading entered today must not change the grade."""
    rule = {"field": "odour_intensity", "op": "ge", "value": "Strong"}
    put = admin.put("/api/grade-rules", {"defaultGrade": "Good", "rules": [{"grade": "Poor", "groups": [[rule]]}]})
    assert put.status == 200, put.text
    tid = new_tote(srv, admin)
    characterize(admin, tid, odour="sweet", odourIntensity="Mild", readingDate=TODAY.isoformat()).raise_for_status()
    grade = lambda: [t for t in admin.get("/api/totes").json["totes"] if t["id"] == tid][0]["grade"]
    assert grade() == "Good"
    characterize(admin, tid, odour="butyric", odourIntensity="Strong", readingDate=day(20)).raise_for_status()
    assert grade() == "Good"                                                              # an older reading, entered today: the current odour is still Mild
    characterize(admin, tid, odour="butyric", odourIntensity="Strong", readingDate=TODAY.isoformat()).raise_for_status()
    assert grade() == "Poor"
    admin.put("/api/grade-rules", {"defaultGrade": "Ungraded", "rules": []}).raise_for_status()


# ---- the received date ------------------------------------------------------------------------------------------------------------

def test_the_received_date_can_be_corrected_with_a_reason_and_is_logged(srv, admin):
    tid = harvest_totes(admin, n=1, date="2026-05-04")[0]
    sql(srv, "UPDATE tote_lots SET received_date='2026-05-05' WHERE id=?", (tid,))
    s = state(admin, tid)
    assert s["receivedDate"] == "2026-05-05" and s["harvestDate"] == "2026-05-04"
    assert admin.post("/api/totes/%d/received-date" % tid, {"receivedDate": "2026-05-09"}).status == 400                   # a filled-in date needs a reason
    r = admin.post("/api/totes/%d/received-date" % tid, {"receivedDate": "2026-05-09", "reason": "the truck was a day late"})
    assert r.status == 200 and state(admin, tid)["receivedDate"] == "2026-05-09"
    row = [e for e in state(admin, tid)["stabilityLog"] if e["field"] == "Received date"][0]
    assert (row["oldValue"], row["newValue"], row["note"]) == ("2026-05-05", "2026-05-09", "the truck was a day late") and row["by"]
    assert [t["receivedDate"] for t in admin.get("/api/totes").json["totes"] if t["id"] == tid] == ["2026-05-09"]
    same = admin.post("/api/totes/%d/received-date" % tid, {"receivedDate": "2026-05-09"})                                   # unchanged: nothing logged, no reason needed
    assert same.status == 200 and len([e for e in state(admin, tid)["stabilityLog"] if e["field"] == "Received date"]) == 1


def test_a_missing_received_date_can_be_filled_in_without_a_reason(srv, admin):
    tid = harvest_totes(admin, n=1, date="2026-05-04")[0]
    sql(srv, "UPDATE tote_lots SET received_date=NULL WHERE id=?", (tid,))
    assert admin.post("/api/totes/%d/received-date" % tid, {"receivedDate": "2026-05-06"}).status == 200
    assert state(admin, tid)["receivedDate"] == "2026-05-06"


@pytest.mark.parametrize("value", ["", "abc", "2026-05-03", "2999-01-01"])
def test_a_bad_received_date_is_refused(srv, admin, value):
    tid = harvest_totes(admin, n=1, date="2026-05-04")[0]
    r = admin.post("/api/totes/%d/received-date" % tid, {"receivedDate": value, "reason": "x"})
    assert r.status == 400                                                                                                       # blank, not a date, before the harvest date, or in the future
