"""Lab results: a field sent as the wrong JSON type (a number where text is expected, a list, an object ...) is read as text or answered 400 with a
clear message -- never an AttributeError / 500 ("Something went wrong ... reference ...")."""
import pytest

from factories import finished_run, sql
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def qm(srv):
    client, _ = srv.make_user(role="user", isQualityManager=True)
    return client


@pytest.fixture(scope="module")
def rid(srv):
    return finished_run(srv.admin_client())


def post(qm, rid, body=None, **row):
    payload = {"labName": "L", "reportNumber": "R1", "results": [dict({"specCode": "potash", "value": "1"}, **row)]}
    payload.update(body or {})
    return qm.post("/api/production/%d/lab-results" % rid, payload)


def stored(srv, rid):
    return sql(srv, "SELECT value_text, value_num, unit, method FROM lab_results WHERE run_id=? ORDER BY id DESC LIMIT 1", (rid,))[0]


def test_a_json_number_is_read_as_its_text(srv, qm, rid):
    r = post(qm, rid, specCode="potash", value=0.5)
    assert r.status == 200, r.json
    assert stored(srv, rid)[:2] == ("0.5", 0.5)
    r = post(qm, rid, specCode="potash", value=12)
    assert r.status == 200, r.json
    assert stored(srv, rid)[:2] == ("12", 12.0)


def test_a_wrong_type_for_the_value_is_a_400_with_the_row_number(qm, rid):
    for bad in ([1], {"a": 1}, True, float("1e999")):
        r = qm.post("/api/production/%d/lab-results" % rid,
                    {"labName": "L", "reportNumber": "R1", "results": [{"specCode": "potash", "value": "1"}, {"specCode": "potash", "value": bad}]})
        assert r.status == 400, (bad, r.status, r.json)
        assert "Row 2: the value must be text such as 0.5, <10 or Negative" in r.json["error"]


def test_a_wrong_type_for_another_row_field_is_a_400_not_a_500(qm, rid):
    for field in ("unit", "method", "analyte", "specCode"):
        for bad in ([1], {"a": 1}, True):
            r = post(qm, rid, **{field: bad})
            assert r.status == 400, (field, bad, r.status, r.json)
            assert r.json["error"].startswith("Row 1:") and "must be text" in r.json["error"]


def test_a_number_is_accepted_for_the_text_fields_of_a_row(srv, qm, rid):
    r = post(qm, rid, specCode="", analyte="Custom test", unit=5, method=2540, value="3")      # no specification, so the typed unit is kept
    assert r.status == 200, r.json
    assert stored(srv, rid)[2:] == ("5", "2540")


def test_wrong_types_in_the_report_header_are_a_400(qm, rid):
    for body in ({"labName": [1]}, {"reportNumber": {"a": 1}}, {"reportNumber": True}, {"reportDate": 20260101}, {"sampleRef": ["x"]},
                 {"labId": [1]}, {"attachmentId": {"a": 1}}, {"results": "abc"}, {"results": {"specCode": "potash"}}, {"results": ["potash"]}):
        r = post(qm, rid, body)
        assert r.status == 400, (body, r.status, r.json)


def test_a_numeric_report_number_is_kept_as_text(srv, qm, rid):
    r = post(qm, rid, {"reportNumber": 20260101})
    assert r.status == 200, r.json
    assert sql(srv, "SELECT report_number FROM lab_results WHERE run_id=? ORDER BY id DESC LIMIT 1", (rid,))[0][0] == "20260101"
