"""KW-30: the Product Release review shows what QA checks first: every test on the Certificate of Analysis with its specification, result, PASS / FAIL and the lab report that
backs it (`coaRows` in GET /api/release/runs/:id), with the release gate beside it; everything else on the screen is folded away."""
import pathlib

import pytest

from factories import finished_run, lab_report
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    return {"admin": srv.admin_client(), "qm": qm}


def test_the_review_lists_every_certificate_test_with_its_result_and_report(srv, people):
    admin, qm = people["admin"], people["qm"]
    rid = finished_run(admin)
    d = admin.get("/api/release/runs/%d" % rid).json
    rows = {x["code"]: x for x in d["coaRows"]}
    assert {"tds", "ph", "apc", "salm", "as"} <= set(rows)                                          # every listed test, with or without a result yet
    assert rows["tds"]["status"] in ("pass", "fail") and rows["tds"]["basis"] == "run"            # the run's own readings need no lab report
    assert rows["apc"]["status"] is None and rows["apc"]["mustPass"] is True                     # a required test with no result yet
    att = lab_report(qm, rid)
    r = qm.post("/api/production/%d/lab-results" % rid, {"labName": "Test Lab", "reportNumber": "T-9", "attachmentId": att,
                                                          "results": [{"specCode": "apc", "value": "<10"}, {"specCode": "salm", "value": "Negative"}]})
    assert r.status == 200, r.text
    rows = {x["code"]: x for x in admin.get("/api/release/runs/%d" % rid).json["coaRows"]}
    assert rows["apc"]["status"] == "pass" and rows["apc"]["attachmentId"] == att and rows["apc"]["reportNumber"] == "T-9" and rows["apc"]["labName"] == "Test Lab"
    assert rows["salm"]["status"] == "pass"


def test_the_screen_puts_the_lab_results_before_the_sign_off_and_folds_the_rest():
    flat = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
    a = flat.index("Lab results — every result on the Certificate of Analysis must pass")
    b = flat.index("signBox('Production-log review sign-off'")
    assert a < b
    assert "fold('Run notes'" in flat and "fold('Finished goods lots" in flat and "fold('Production log summary'" in flat
