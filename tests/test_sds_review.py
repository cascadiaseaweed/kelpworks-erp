"""KW-88: a review of a safety data sheet is recorded separately from the manufacturer's issue date. An administrator or a Quality Manager records it; review due = the later of the issue date and the
last review + the interval; every review is append-only history; a new issue from the manufacturer starts again; and the Suppliers list counts the linked safety data sheets."""
import datetime
import os

import pytest

import kelp_erp_server as K
from factories import sql
from harness import ROOT, Server
from test_documents_library import KEY, accept, docs, put, remove, waiting

_n = [0]


def today(days=0):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    return {"admin": srv.admin_client(), "qm": srv.make_user(role="user", isQualityManager=True)[0], "pm": srv.make_user(role="user", isProductionManager=True)[0],
            "plain": srv.make_user(role="user")[0]}


def uniq(prefix):
    _n[0] += 1
    return "%s%d" % (prefix, _n[0])


def add_sheet(srv, admin, issue, supp=None, chem=None):
    chem, supp = chem or uniq("Revchem"), supp or uniq("Revsupp")
    name = "SDS_%s_%s_%s.pdf" % (chem, supp, issue)
    put(srv, "sds", name, ("%%PDF %s" % name).encode())
    assert accept(admin, waiting(admin, "sds", change="new", filename=name)[0]).status == 200
    return name, [s for s in docs(admin)["sds"] if s["chemical"] == chem][0]


def sheet(client, sid):
    return [s for s in docs(client)["sds"] if s["id"] == sid][0]


def history(srv, sid):
    return sql(srv, "SELECT field, old_value, new_value, user_name FROM document_edits WHERE kind='sds' AND doc_id=? ORDER BY id", (sid,))


def review(client, sid, on=None, note=None):
    return client.post("/api/sds-documents/%d/review" % sid, {"reviewedOn": on or today(), "note": note})


# ---- the rule ----
def test_review_due_is_the_later_of_the_issue_date_and_the_review_plus_the_interval():
    t = datetime.date(2026, 10, 26)
    assert K.sds_review("2024-01-01", 3, 60, t) == ("2027-01-01", "ok")                                     # no review: as before
    assert K.sds_review("2024-01-01", 3, 60, t, "2025-06-01") == ("2028-06-01", "ok")
    assert K.sds_review("2024-01-01", 3, 60, t, "2023-06-01") == ("2027-01-01", "ok")                       # a review older than the issue changes nothing
    assert K.sds_review("2020-05-01", 3, 60, t) == ("2023-05-01", "overdue")
    assert K.sds_review("2020-05-01", 3, 60, t, "2026-10-01") == ("2029-10-01", "ok")
    assert K.sds_review(None, 3, 60, t, "2026-10-01") == (None, None)


# ---- who may record ----
def test_an_administrator_or_a_quality_manager_can_record_a_review_nobody_else(srv, people):
    a = people["admin"]
    _, s = add_sheet(srv, a, "2020-05-01")
    assert s["reviewStatus"] == "overdue" and s["lastReviewedOn"] is None
    for who in ("pm", "plain"):
        assert review(people[who], s["id"]).status == 403
    assert srv.client().post("/api/sds-documents/%d/review" % s["id"], {"reviewedOn": today()}).status == 401
    r = review(people["qm"], s["id"], note="Checked the manufacturer's site")
    assert r.status == 200 and r.json["sds"]["lastReviewedOn"] == today() and r.json["sds"]["lastReviewNote"] == "Checked the manufacturer's site"
    assert r.json["sds"]["reviewStatus"] == "ok" and r.json["sds"]["issueDate"] == "2020-05-01"              # the manufacturer's date is untouched
    assert r.json["sds"]["reviewDue"] == str(int(today()[:4]) + 3) + today()[4:] or r.json["sds"]["reviewDue"].startswith(str(int(today()[:4]) + 3))
    assert review(a, s["id"], today(-1)).status == 200                                                        # an administrator may too
    assert a.put("/api/sds-documents/%d" % s["id"], {"notes": "x"}).status == 200 and people["qm"].put("/api/sds-documents/%d" % s["id"], {"notes": "x"}).status == 403      # the rest stays administrator-only


def test_the_date_rules(srv, people):
    a = people["admin"]
    name, s = add_sheet(srv, a, "2025-03-01")
    assert review(a, s["id"], today(2)).status == 400                                                         # not in the future
    assert review(a, s["id"], "2025-02-28").status == 400                                                     # not before the issue date
    assert review(a, s["id"], "soon").status == 400 and a.post("/api/sds-documents/%d/review" % s["id"], {"reviewedOn": ""}).status == 400 and a.post("/api/sds-documents/%d/review" % s["id"], {}).status == 400
    assert review(a, s["id"], "2025-03-01").status == 200                                                     # the issue date itself is fine
    assert review(a, 999999).status == 404
    remove(srv, "sds", name)
    gone = [i for i in waiting(a, "sds", change="missing") if "SDS_" in (i.get("filename") or "") and s["chemical"] in i["filename"]]
    assert gone and accept(a, gone[0]).status == 200
    assert sheet(a, s["id"])["status"] == "withdrawn" and review(a, s["id"]).status == 409                    # a withdrawn sheet cannot be reviewed


# ---- the history ----
def test_every_review_is_append_only_history_with_who_when_and_the_new_due_date(srv, people):
    a, qm = people["admin"], people["qm"]
    _, s = add_sheet(srv, a, "2021-01-15")
    sid = s["id"]
    base = len(history(srv, sid))
    assert review(qm, sid, "2026-09-01", "First check").status == 200
    h = history(srv, sid)[base:]
    assert [(x[0], x[1], x[2]) for x in h] == [("Reviewed on", "-", "2026-09-01"), ("Review due", "2024-01-15", "2029-09-01"), ("Review note", "-", "First check")]
    assert {x[3] for x in h} == {sql(srv, "SELECT name FROM users WHERE is_quality_manager=1 LIMIT 1")[0][0]}
    assert review(a, sid, "2026-10-01").status == 200                                                         # a second review appends; the first stays
    h2 = history(srv, sid)[base + 3:]
    assert [(x[0], x[1], x[2]) for x in h2] == [("Reviewed on", "2026-09-01", "2026-10-01"), ("Review due", "2029-09-01", "2029-10-01")]
    assert review(a, sid, "2026-09-15").status == 200                                                         # a correction to an earlier date is allowed and logged
    assert sheet(a, sid)["lastReviewedOn"] == "2026-09-15" and len(history(srv, sid)) == base + 3 + 2 + 2
    n = len(history(srv, sid))
    assert review(a, sid, "2026-09-15").status == 200 and len(history(srv, sid)) == n                         # the same again: nothing new to record
    assert a.get("/api/documents/sds/%d/history" % sid).status == 200


# ---- a new issue from the manufacturer ----
def test_a_new_issue_starts_again_but_the_same_issue_coming_back_keeps_the_review(srv, people):
    a = people["admin"]
    chem, supp = uniq("Issuechem"), uniq("Issuesupp")
    old, s = add_sheet(srv, a, "2023-04-01", supp, chem)
    sid = s["id"]
    assert review(a, sid, "2026-06-01", "Checked").status == 200
    remove(srv, "sds", old)                                                                                   # withdrawn, then the SAME issue returns: the review still applies
    gone = [i for i in waiting(a, "sds", change="missing") if chem in (i.get("filename") or "")]
    assert accept(a, gone[0]).status == 200
    put(srv, "sds", old, ("%%PDF %s" % old).encode())
    back = waiting(a, "sds", change="revision", filename=old)
    assert back and accept(a, back[0]).status == 200
    assert sheet(a, sid)["lastReviewedOn"] == "2026-06-01" and sheet(a, sid)["status"] == "current"
    newer = "SDS_%s_%s_2026-08-01.pdf" % (chem, supp)
    remove(srv, "sds", old)
    put(srv, "sds", newer, b"%PDF newer issue")
    rev = waiting(a, "sds", change="revision", filename=newer)
    assert rev and accept(a, rev[0]).status == 200
    s2 = sheet(a, sid)
    assert s2["issueDate"] == "2026-08-01" and s2["lastReviewedOn"] is None and s2["reviewDue"] == "2029-08-01"
    h = history(srv, sid)
    assert ("Review cleared (new issue)", "reviewed 2026-06-01", "issue dated 2026-08-01") in [(x[0], x[1], x[2]) for x in h]
    assert any(x[0] == "Reviewed on" and x[2] == "2026-06-01" for x in h)                                    # the earlier review is still in the history


# ---- on the supplier ----
def test_the_suppliers_documents_number_counts_current_linked_sheets_and_a_review_clears_the_overdue_flag(srv, people):
    a = people["admin"]
    supp = uniq("Countco")
    sid = a.post("/api/suppliers", {"name": supp, "type": "Distributor"}).json["id"]
    d0 = a.get("/api/suppliers/%d" % sid).json
    assert d0["nDocs"] == 0 and d0["nSds"] == 0
    old_name, old = add_sheet(srv, a, "2019-05-20", supp)                                                   # overdue
    _, fresh = add_sheet(srv, a, today(-30), supp)
    assert old["supplierId"] == sid and fresh["supplierId"] == sid
    d1 = a.get("/api/suppliers/%d" % sid).json
    assert d1["nSds"] == 2 and d1["nDocs"] == 0 and "sds_review_overdue" in d1["flags"]
    listed = [x for x in a.get("/api/suppliers?q=%s" % supp).json["suppliers"] if x["id"] == sid][0]
    assert listed["nSds"] == 2 and listed["nDocs"] == 0
    assert review(people["qm"], old["id"], today()).status == 200                                            # reviewed: no longer overdue
    d2 = a.get("/api/suppliers/%d" % sid).json
    assert "sds_review_overdue" not in d2["flags"] and {x["chemical"]: x["lastReviewedOn"] for x in d2["sds"]}[old["chemical"]] == today()
    remove(srv, "sds", old_name)                                                                              # a withdrawn sheet is not counted
    gone = [i for i in waiting(a, "sds", change="missing") if old["chemical"] in (i.get("filename") or "")]
    assert accept(a, gone[0]).status == 200
    d3 = a.get("/api/suppliers/%d" % sid).json
    assert d3["nSds"] == 1 and len(d3["sds"]) == 2


def test_the_pages_carry_the_record_review_action_the_split_count_and_the_new_formula():
    js = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    assert "function recordSdsReview(" in js and "'Record review…'" in js and "'Last reviewed'" in js
    assert "String(s.nDocs + (s.nSds || 0)) + (s.nSds ? ' (' + s.nSds + ' SDS)' : '')" in js
    assert "Review due = the later of the issue date and the last reviewed date, + review interval (years)." in js
