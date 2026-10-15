"""Feedstock % TDS: an optional reading on a tote, set in the Update feedstock conditions window and tracked in the Feedstock Stability log like pH and ORP."""
import pytest

from factories import harvest_totes, sql
from harness import Server

FIELD = "Feedstock TDS (%)"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def tote(admin):
    return harvest_totes(admin, n=1)[0]


def characterize(admin, tid, **body):
    return admin.post("/api/totes/%d/characterize" % tid, dict({"decision": "accepted"}, **body))


def tds_log(admin, tid):
    return [e for e in admin.get("/api/totes/%d/ph" % tid).json["stabilityLog"] if e["field"] == FIELD]


def test_a_tote_has_no_tds_until_one_is_entered(srv, admin):
    tid = tote(admin)
    d = admin.get("/api/totes/%d/ph" % tid).json
    assert d["tdsPct"] is None and d["latestCharacterization"]["tdsPct"] is None
    assert tds_log(admin, tid) == []


def test_entering_a_tds_sets_it_on_the_tote_and_logs_who_and_when(srv, admin):
    tid = tote(admin)
    assert characterize(admin, tid, tdsPct=12.5, tdsDate="2026-10-10").status == 200
    d = admin.get("/api/totes/%d/ph" % tid).json
    assert d["tdsPct"] == 12.5 and d["tdsUpdated"] == "2026-10-10" and d["latestCharacterization"]["tdsPct"] == 12.5
    log = tds_log(admin, tid)
    assert len(log) == 1 and not log[0]["oldValue"] and log[0]["newValue"] == "12.5" and log[0]["by"] and log[0]["at"]
    assert [t for t in admin.get("/api/totes").json["totes"] if t["id"] == tid][0]["tdsPct"] == 12.5


def test_every_change_is_logged_from_and_to_and_an_unchanged_value_is_not(srv, admin):
    tid = tote(admin)
    characterize(admin, tid, tdsPct=12.5).raise_for_status()
    characterize(admin, tid, tdsPct=13.1).raise_for_status()
    characterize(admin, tid, tdsPct=13.1, notes="saved again").raise_for_status()                # same value: no new line
    log = tds_log(admin, tid)
    assert [(e["oldValue"] or None, e["newValue"]) for e in log] == [("12.5", "13.1"), (None, "12.5")]
    assert admin.get("/api/totes/%d/ph" % tid).json["tdsPct"] == 13.1


def test_leaving_it_blank_changes_nothing_and_a_zero_is_a_real_value(srv, admin):
    tid = tote(admin)
    characterize(admin, tid, tdsPct=9).raise_for_status()
    characterize(admin, tid, tdsPct=None, notes="no TDS this time").raise_for_status()
    characterize(admin, tid, tdsPct="").raise_for_status()
    assert admin.get("/api/totes/%d/ph" % tid).json["tdsPct"] == 9 and len(tds_log(admin, tid)) == 1
    characterize(admin, tid, tdsPct=0).raise_for_status()                                         # zero is a measurement, not "blank"
    assert admin.get("/api/totes/%d/ph" % tid).json["tdsPct"] == 0 and tds_log(admin, tid)[0]["newValue"] == "0"


@pytest.mark.parametrize("bad", [-1, 100.5, 101, "abc"])
def test_a_tds_must_be_a_percentage_and_a_refused_value_leaves_no_trace(srv, admin, bad):
    tid = tote(admin)
    r = characterize(admin, tid, tdsPct=bad)
    assert r.status == 400 and "between 0 and 100" in r.json["error"]
    assert admin.get("/api/totes/%d/ph" % tid).json["tdsPct"] is None and tds_log(admin, tid) == []


def test_the_reading_is_independent_of_ph_and_orp(srv, admin):
    tid = tote(admin)
    assert admin.post("/api/totes/%d/ph" % tid, {"ph": 3.9, "orp": 10}).status == 200
    characterize(admin, tid, tdsPct=11).raise_for_status()
    d = admin.get("/api/totes/%d/ph" % tid).json
    assert (d["ph"], d["orp"], d["tdsPct"]) == (3.9, 10, 11)
    fields = {e["field"] for e in d["stabilityLog"]}
    assert {"pH", "ORP (mV)", FIELD} <= fields


def test_a_production_run_characterization_is_unaffected(srv, admin):
    """The run's own feedstock card does not carry TDS: a tdsPct sent with a run's details is ignored and the tote keeps what it had."""
    from factories import feedstock_detail, finalize_run
    tid = tote(admin)
    characterize(admin, tid, tdsPct=7).raise_for_status()
    rid, f = finalize_run(admin, [tid])
    assert f.ok, f.text
    assert sql(srv, "SELECT tds_pct FROM tote_lots WHERE id=?", (tid,))[0][0] == 7
