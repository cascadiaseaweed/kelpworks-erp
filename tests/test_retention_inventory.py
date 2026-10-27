"""The Retention inventory (Samples tab) lists the Retention samples of finalized runs with their discard-by date. It answered HTTP 500 (shown as an empty list) for any Retention sample
after KW-81 added a second add_months() that refused a collection time ("2026-05-04T12:00"): one add_months now serves the whole program."""
import pytest

import kelp_erp_server as K
from factories import finished_run
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


def test_add_months_reads_a_date_with_a_time_and_clamps_the_month_end():
    assert K.add_months("2026-05-04T12:00", 24) == "2028-05-04"
    assert K.add_months("2026-05-04T12:00:33Z", 24) == "2028-05-04"
    assert K.add_months("2026-10-24", 36) == "2029-10-24"
    assert K.add_months("2026-01-31", 1) == "2026-02-28" and K.add_months("2024-01-31", 1) == "2024-02-29"
    assert K.add_months("2026-12-15", 1) == "2027-01-15" and K.add_months("2026-05-04", 0.0) == "2026-05-04" and K.add_months("2026-05-04", "12") == "2027-05-04"
    assert K.add_months("2026-05-04", 2.5 * 12) == "2028-11-04"
    assert K.add_months("not a date", 12) is None and K.add_months(None, 12) is None and K.add_months("2026-05-04", "x") is None
    assert len([n for n, _ in __import__("inspect").getmembers(K, __import__("inspect").isfunction) if n == "add_months"]) == 1


def test_the_program_defines_add_months_only_once():
    import re
    src = open(K.__file__, encoding="utf-8").read()
    assert len(re.findall(r"^def add_months\(", src, re.M)) == 1


def test_retention_samples_are_listed_with_their_discard_by_date_and_never_break_the_other_views(srv):
    admin = srv.admin_client()
    rid = finished_run(admin)                                                              # a new run is finalized with its default samples, four of them Retention
    ret = admin.get("/api/samples?retention=1&runId=%d" % rid)
    assert ret.status == 200, ret.text
    ret = ret.json["samples"]
    assert len(ret) == 4 and all(s["description"] == "Retention" and s["isRetention"] for s in ret)
    for s in ret:
        assert s["discardBy"] == K.add_months(s["collectedAt"], 24) and len(s["discardBy"]) == 10
    cat = admin.get("/api/samples?retention=0&runId=%d" % rid)
    assert cat.status == 200 and all(s["description"] != "Retention" for s in cat.json["samples"]) and cat.json["samples"]
    every = admin.get("/api/samples?runId=%d" % rid)
    assert every.status == 200 and len(every.json["samples"]) == len(ret) + len(cat.json["samples"])
    assert admin.get("/api/samples?retention=1").status == 200 and admin.get("/api/cart").status == 200
    sid = ret[0]["id"]
    assert admin.post("/api/cart", {"sampleId": sid, "labId": None, "analyses": []}).status in (200, 400, 404)         # the cart route itself is covered elsewhere; here it must not be a 500
    assert admin.get("/api/cart").status == 200
