"""KW-99 loss reports: feedstock, in-process or finished product that was lost.

A loss report is a RECORD (no edit, no delete): Open (anyone) -> Confirm (Production Manager) -> Close (Quality Manager), each signature re-checks the password and is a
hash-chained audit-trail event. A Production Manager who opens a report also confirms it. Nothing in a report touches stock, the ledger, or a tote / lot status. A run can be
finished as a loss (no packaging, no finished-goods lots, state `no_output`).
"""
import datetime
import json

import pytest

from factories import feedstock_detail, finished_run, harvest_totes, lots_of, sql
from harness import ADMIN_PASSWORD, Server

FOUND = "2026-05-02"
OCCURRED = "2026-05-01"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    qm2, qm2_info = srv.make_user(role="user", isQualityManager=True)
    both, both_info = srv.make_user(role="user", isProductionManager=True, isQualityManager=True)
    plain, plain_info = srv.make_user(role="user")
    return {"pm": pm, "qm": qm, "qm2": qm2, "both": both, "plain": plain, "admin": srv.admin_client(),
            "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "qm2_pw": qm2_info["password"], "both_pw": both_info["password"], "plain_pw": plain_info["password"]}


def cause_id(client, name="Operator error"):
    return next(c["id"] for c in client.get("/api/refdata").json["lossCauses"] if c["name"] == name)


def payload(client, password, **over):
    d = {"password": password, "stage": "Extraction", "lostKind": "feedstock", "qty": 3882, "occurredAt": OCCURRED, "foundAt": FOUND, "causeId": cause_id(client),
         "description": "Four totes were drained by mistake", "disposalRoute": "Liquid waste", "refText": "Lot 13, four totes"}
    d.update(over)
    return {k: v for k, v in d.items() if v is not None}


def open_loss(client, password, **over):
    r = client.post("/api/losses", payload(client, password, **over))
    r.raise_for_status()
    return r.json["loss"]


def confirm(client, password, lid, **kw):
    return client.post("/api/losses/%d/confirm" % lid, dict({"password": password}, **kw))


def close(client, password, lid, **kw):
    body = {"password": password, "nonconformance": "yes", "otherLotsAffected": "no", "capaRequired": False}
    if "capaRequired" not in kw and client.get("/api/losses/%d" % lid).json["repeat"]["needed"]:        # the tests share a database: a repeated cause and stage forces a corrective action
        body.update(capaRequired=True, capaText="Retrain", capaOwner="Plant manager", capaDue="2026-12-31")
    body.update(kw)
    return client.post("/api/losses/%d/close" % lid, body)


def setting(srv, key, value):
    sql(srv, "UPDATE settings SET value=? WHERE key=?", (value, key))


def stock_state(srv):
    """Everything a loss report must never touch: item stock, the inventory ledger, lot assignments, tote and finished-goods lot status and quantity."""
    return (sql(srv, "SELECT id, on_hand FROM consumables ORDER BY id"), sql(srv, "SELECT COUNT(*), COALESCE(SUM(delta),0) FROM consumable_txns"),
            sql(srv, "SELECT COUNT(*) FROM lot_uses"), sql(srv, "SELECT id, status, run_id FROM tote_lots ORDER BY id"),
            sql(srv, "SELECT id, status, qty FROM fg_lots ORDER BY id"), sql(srv, "SELECT id, qty_remaining FROM consumable_lots ORDER BY id"))


# ---- reference data and the settings ----

def test_the_code_lists_the_causes_and_the_four_settings_are_delivered(srv, people):
    ref = people["plain"].get("/api/refdata").json
    assert ref["lossStages"] == ["Feedstock", "Homogenization", "Extraction", "Separation", "Pasteurization", "Dilution & Preservation", "Packaging", "Storage", "Other"]
    assert ref["lossKinds"] == [{"code": "feedstock", "name": "Feedstock", "unit": "kg"}, {"code": "in_process", "name": "In-process product", "unit": "L"},
                                {"code": "finished", "name": "Finished product", "unit": "L"}]
    assert ref["lossDisposal"] == ["Liquid waste", "Solids waste", "Reprocess", "Other"]
    assert [c["name"] for c in ref["lossCauses"]] == ["Operator error", "Equipment failure", "Feedstock quality", "Process deviation", "Spill / leak", "Other"]
    assert isinstance(ref["lossOpenCount"], int)
    s = ref["settings"]
    assert {k: s[k]["value"] for k in ("loss_report_min_feedstock_kg", "loss_report_min_product_l", "loss_repeat_count", "loss_repeat_days")} == {
        "loss_report_min_feedstock_kg": 0, "loss_report_min_product_l": 0, "loss_repeat_count": 3, "loss_repeat_days": 180}


# ---- opening a report ----

def test_a_report_can_be_opened_against_a_run_a_tote_a_finished_goods_lot_or_free_text(srv, people):
    rid = finished_run(people["admin"])
    tote = harvest_totes(people["admin"], 1)[0]
    lot = lots_of(srv, rid)[0]
    plain, pw = people["plain"], people["plain_pw"]
    by_run = open_loss(plain, pw, runId=rid, refText=None)
    assert by_run["status"] == "open" and by_run["runId"] == rid and by_run["processingLot"].startswith("PR-")
    by_tote = open_loss(plain, pw, toteId=tote, refText=None)
    assert by_tote["toteId"] == tote and by_tote["toteLot"]
    by_lot = open_loss(plain, pw, fgLotId=lot["id"], lostKind="finished", stage="Storage", qty=250, refText=None)
    assert by_lot["fgLotId"] == lot["id"] and by_lot["fgLotNumber"] == lot["number"] and by_lot["unit"] == "L"
    free = open_loss(plain, pw)
    assert free["runId"] is None and free["refText"] == "Lot 13, four totes"
    for x in (by_run, by_tote, by_lot, free):
        assert x["reportNo"].startswith("LR-%s-" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"))
        assert x["openedBy"] and x["openedAt"] and x["ageDays"] == 0 and x["steps"] == [] and x["close"] is None and x["confirmedQty"] is None
    assert len({x["reportNo"] for x in (by_run, by_tote, by_lot, free)}) == 4


def test_the_report_number_counts_up_within_a_day(srv, people):
    a = open_loss(people["plain"], people["plain_pw"])
    b = open_loss(people["plain"], people["plain_pw"])
    assert a["reportNo"][:12] == b["reportNo"][:12] and int(b["reportNo"][-3:]) == int(a["reportNo"][-3:]) + 1


def test_the_unit_follows_the_kind_and_a_sent_unit_is_ignored(srv, people):
    f = open_loss(people["plain"], people["plain_pw"], lostKind="feedstock", unit="L")
    p = open_loss(people["plain"], people["plain_pw"], lostKind="in_process", unit="kg", stage="Separation")
    assert f["unit"] == "kg" and p["unit"] == "L"


@pytest.mark.parametrize("field", ["stage", "lostKind", "qty", "occurredAt", "foundAt", "causeId", "description", "disposalRoute"])
def test_every_required_field_is_required(srv, people, field):
    before = sql(srv, "SELECT COUNT(*) FROM loss_reports")[0][0]
    body = payload(people["plain"], people["plain_pw"])
    del body[field]
    r = people["plain"].post("/api/losses", body)
    assert r.status == 400, r.text
    assert sql(srv, "SELECT COUNT(*) FROM loss_reports")[0][0] == before


@pytest.mark.parametrize("over", [
    {"refText": None},                                      # nothing says what was lost: no link and no text
    {"stage": "Wherever"}, {"lostKind": "water"}, {"qty": 0}, {"qty": -5}, {"qty": "lots"}, {"qty": "nan"}, {"qty": "inf"},
    {"occurredAt": "2099-01-01"}, {"foundAt": "2099-01-01"}, {"occurredAt": "last week"}, {"occurredAt": "2026-13-40"}, {"foundAt": "2026-04-30"},   # found before it happened
    {"causeId": 99999}, {"description": "x"}, {"disposalRoute": "Ocean"}, {"disposalRoute": "Other"}, {"runId": 99999}, {"toteId": 99999}, {"fgLotId": 99999},
    {"correctsId": 99999},
])
def test_bad_values_are_refused(srv, people, over):
    r = people["plain"].post("/api/losses", payload(people["plain"], people["plain_pw"], **over))
    assert r.status == 400, (over, r.text)


def test_dates_may_carry_a_time_and_a_found_time_before_the_occurred_time_is_refused(srv, people):
    ok = open_loss(people["plain"], people["plain_pw"], occurredAt="2026-05-01T14:30", foundAt="2026-05-01T15:00")
    assert ok["occurredAt"] == "2026-05-01T14:30"
    r = people["plain"].post("/api/losses", payload(people["plain"], people["plain_pw"], occurredAt="2026-05-01T14:30", foundAt="2026-05-01T09:00"))
    assert r.status == 400
    assert open_loss(people["plain"], people["plain_pw"], occurredAt="2026-05-01T14:30", foundAt="2026-05-01")["foundAt"] == "2026-05-01"     # a date alone is compared by day


def test_disposal_other_needs_text_and_is_kept_only_for_other(srv, people):
    x = open_loss(people["plain"], people["plain_pw"], disposalRoute="Other", disposalOther="Composted on site")
    assert x["disposalRoute"] == "Other" and x["disposalOther"] == "Composted on site"
    assert open_loss(people["plain"], people["plain_pw"], disposalRoute="Liquid waste", disposalOther="ignored")["disposalOther"] is None


def test_an_inactive_cause_cannot_be_chosen_for_a_new_report(srv, people):
    new = people["admin"].post("/api/loss-causes", {"name": "Retired cause %d" % datetime.datetime.now().microsecond}).json
    cid = new["id"]
    assert people["admin"].put("/api/loss-causes/%d" % cid, {"active": False}).ok
    assert cid not in [c["id"] for c in people["plain"].get("/api/refdata").json["lossCauses"]]
    assert people["plain"].post("/api/losses", payload(people["plain"], people["plain_pw"], causeId=cid)).status == 400


def test_a_wrong_password_records_nothing(srv, people):
    before = sql(srv, "SELECT COUNT(*) FROM loss_reports")[0][0], sql(srv, "SELECT COUNT(*) FROM release_events")[0][0]
    r = people["plain"].post("/api/losses", payload(people["plain"], "not-the-password"))
    assert r.status == 400 and "assword" in r.json["error"]
    assert people["plain"].post("/api/losses", {k: v for k, v in payload(people["plain"], "x").items() if k != "password"}).status == 400
    assert (sql(srv, "SELECT COUNT(*) FROM loss_reports")[0][0], sql(srv, "SELECT COUNT(*) FROM release_events")[0][0]) == before


def test_every_signature_rechecks_the_password(srv, people):
    x = open_loss(people["pm"], people["pm_pw"], stage="Packaging")                      # confirmed at opening
    y = open_loss(people["plain"], people["plain_pw"], stage="Packaging")
    assert confirm(people["pm"], "wrong", y["id"]).status == 400
    assert close(people["qm"], "wrong", x["id"]).status == 400
    assert sql(srv, "SELECT status FROM loss_reports WHERE id=?", (y["id"],))[0][0] == "open"
    assert sql(srv, "SELECT status FROM loss_reports WHERE id=?", (x["id"],))[0][0] == "confirmed"


# ---- the thresholds ----

def test_the_threshold_defaults_to_every_loss_and_a_small_loss_is_still_accepted(srv, people):
    try:
        assert open_loss(people["plain"], people["plain_pw"], qty=0.5)["reportable"] is True               # default 0: every loss is reportable
        setting(srv, "loss_report_min_feedstock_kg", 100)
        setting(srv, "loss_report_min_product_l", 20)
        small = people["plain"].post("/api/losses", payload(people["plain"], people["plain_pw"], qty=50))
        assert small.ok and small.json["loss"]["reportable"] is False and small.json["loss"]["thresholdUsed"] == 100         # accepted, never refused
        assert "not required" in small.json["requiredNote"]
        assert open_loss(people["plain"], people["plain_pw"], qty=100)["reportable"] is False                     # "above" means greater than
        big = people["plain"].post("/api/losses", payload(people["plain"], people["plain_pw"], qty=100.5))
        assert big.json["loss"]["reportable"] is True and "required" in big.json["requiredNote"] and "not required" not in big.json["requiredNote"]
        prod = open_loss(people["plain"], people["plain_pw"], lostKind="in_process", qty=20)
        assert prod["reportable"] is False and prod["thresholdUsed"] == 20                                       # a separate threshold for product
        assert open_loss(people["plain"], people["plain_pw"], lostKind="finished", qty=21)["reportable"] is True
        assert people["plain"].get("/api/losses").json["thresholds"] == {"feedstockKg": 100, "productL": 20}
    finally:
        setting(srv, "loss_report_min_feedstock_kg", 0)
        setting(srv, "loss_report_min_product_l", 0)


# ---- confirm and close ----

def test_only_a_production_manager_confirms_and_only_a_quality_manager_closes(srv, people):
    x = open_loss(people["plain"], people["plain_pw"], stage="Separation")
    assert confirm(people["plain"], people["plain_pw"], x["id"]).status == 403
    assert confirm(people["qm"], people["qm_pw"], x["id"]).status == 403              # a Quality Manager is not a Production Manager
    assert confirm(people["admin"], ADMIN_PASSWORD, x["id"]).status == 403         # the admin role alone grants nothing
    assert close(people["qm"], people["qm_pw"], x["id"]).status == 409                 # not confirmed yet
    ok = confirm(people["pm"], people["pm_pw"], x["id"], note="Counted the totes")
    assert ok.ok and ok.json["loss"]["status"] == "confirmed"
    assert ok.json["loss"]["steps"][0]["step"] == "confirm" and ok.json["loss"]["steps"][0]["by"] and ok.json["loss"]["steps"][0]["note"] == "Counted the totes"
    assert confirm(people["pm"], people["pm_pw"], x["id"]).status == 409                # already confirmed
    assert close(people["pm"], people["pm_pw"], x["id"]).status == 403
    assert close(people["plain"], people["plain_pw"], x["id"]).status == 403
    done = close(people["qm"], people["qm_pw"], x["id"], note="Reviewed")
    assert done.ok and done.json["loss"]["status"] == "closed" and done.json["loss"]["ageDays"] is None
    assert done.json["loss"]["close"] == {"nonconformance": "yes", "otherLotsAffected": "no", "otherLotsNote": None, "capaRequired": False, "capaText": None, "capaOwner": None, "capaDue": None}


def test_a_production_manager_who_opens_a_report_also_confirms_it(srv, people):
    x = open_loss(people["pm"], people["pm_pw"], stage="Homogenization")
    assert x["status"] == "confirmed" and x["confirmedQty"] == 3882 and x["confirmedCauseName"] == "Operator error"
    assert [s["step"] for s in x["steps"]] == ["confirm"] and x["steps"][0]["note"] == "confirmed at opening by the reporting Production Manager"
    d = people["pm"].get("/api/losses/%d" % x["id"]).json
    assert [e["type"] for e in d["events"]] == ["loss_opened", "loss_confirmed"]
    assert d["events"][1]["comment"] == "confirmed at opening by the reporting Production Manager"
    assert open_loss(people["plain"], people["plain_pw"], stage="Homogenization")["status"] == "open"              # a non-manager's report waits


def test_the_confirmation_may_correct_the_quantity_and_cause_and_the_original_stays(srv, people):
    x = open_loss(people["plain"], people["plain_pw"], qty=3882, stage="Pasteurization")
    other = cause_id(people["pm"], "Equipment failure")
    r = confirm(people["pm"], people["pm_pw"], x["id"], qty=3500, causeId=other, note="Weighed again")
    assert r.ok
    loss = r.json["loss"]
    assert loss["qty"] == 3882 and loss["causeName"] == "Operator error"                                              # the report as opened
    assert loss["confirmedQty"] == 3500 and loss["confirmedCauseName"] == "Equipment failure"
    f = loss["steps"][0]["fields"]
    assert f["originalQty"] == 3882 and f["originalCauseName"] == "Operator error" and f["changed"] is True
    assert confirm(people["pm"], people["pm_pw"], open_loss(people["plain"], people["plain_pw"])["id"], qty=0).status == 400
    assert confirm(people["pm"], people["pm_pw"], open_loss(people["plain"], people["plain_pw"])["id"], causeId=99999).status == 400


def test_the_close_needs_the_assessment_and_a_complete_corrective_action(srv, people):
    x = open_loss(people["pm"], people["pm_pw"], stage="Dilution & Preservation")
    assert close(people["qm"], people["qm_pw"], x["id"], nonconformance=None).status == 400
    assert close(people["qm"], people["qm_pw"], x["id"], otherLotsAffected="maybe").status == 400
    assert close(people["qm"], people["qm_pw"], x["id"], capaRequired=None).status == 400
    assert close(people["qm"], people["qm_pw"], x["id"], capaRequired=True).status == 400                          # no text, owner or due date
    assert close(people["qm"], people["qm_pw"], x["id"], capaRequired=True, capaText="Re-train", capaOwner="N. W.", capaDue="soon").status == 400
    assert sql(srv, "SELECT status FROM loss_reports WHERE id=?", (x["id"],))[0][0] == "confirmed"
    ok = close(people["qm"], people["qm_pw"], x["id"], otherLotsAffected="yes", otherLotsNote="Lot 12 shared the line", capaRequired=True,
               capaText="Re-train the operators", capaOwner="Plant manager", capaDue="2026-12-31")
    assert ok.ok
    assert ok.json["loss"]["close"] == {"nonconformance": "yes", "otherLotsAffected": "yes", "otherLotsNote": "Lot 12 shared the line", "capaRequired": True,
                                        "capaText": "Re-train the operators", "capaOwner": "Plant manager", "capaDue": "2026-12-31"}


def test_the_same_person_cannot_confirm_and_close_when_the_setting_says_so(srv, people):
    try:
        setting(srv, "release_separate_signers", 1)
        x = open_loss(people["both"], people["both_pw"], stage="Feedstock")                                          # confirmed at opening by the same person
        r = close(people["both"], people["both_pw"], x["id"])
        assert r.status == 403 and r.json["code"] == "same_person"
        assert sql(srv, "SELECT status FROM loss_reports WHERE id=?", (x["id"],))[0][0] == "confirmed"
        assert close(people["qm"], people["qm_pw"], x["id"]).ok                                                       # another Quality Manager can
    finally:
        setting(srv, "release_separate_signers", 0)
    y = open_loss(people["both"], people["both_pw"], stage="Feedstock")                                              # with the setting off, one person may do both
    assert close(people["both"], people["both_pw"], y["id"]).ok


def test_a_closed_report_is_final_and_there_is_no_edit_or_delete(srv, people):
    x = open_loss(people["pm"], people["pm_pw"], stage="Storage")
    assert close(people["qm"], people["qm_pw"], x["id"]).ok
    snapshot = sql(srv, "SELECT * FROM loss_reports WHERE id=?", (x["id"],)), sql(srv, "SELECT COUNT(*) FROM loss_report_steps WHERE report_id=?", (x["id"],))
    r = confirm(people["pm"], people["pm_pw"], x["id"])
    assert r.status == 409 and r.json["code"] == "closed"
    assert close(people["qm"], people["qm_pw"], x["id"]).status == 409
    assert close(people["qm2"], people["qm2_pw"], x["id"]).status == 409
    for method in ("put", "delete"):
        path = "/api/losses/%d" % x["id"]
        res = people["admin"].delete(path) if method == "delete" else people["admin"].request(method.upper(), path, {"qty": 1})
        assert res.status in (404, 405), (method, res.status)
    assert people["admin"].post("/api/losses/%d/edit" % x["id"], {"qty": 1}).status in (404, 405)
    assert (sql(srv, "SELECT * FROM loss_reports WHERE id=?", (x["id"],)), sql(srv, "SELECT COUNT(*) FROM loss_report_steps WHERE report_id=?", (x["id"],))) == snapshot


def test_a_mistake_is_corrected_by_a_new_report_that_points_at_the_old_one(srv, people):
    old = open_loss(people["pm"], people["pm_pw"], qty=3000, stage="Storage")
    assert close(people["qm"], people["qm_pw"], old["id"]).ok
    new = open_loss(people["pm"], people["pm_pw"], qty=3882, stage="Storage", correctsId=old["id"])
    assert new["correctsId"] == old["id"] and new["correctsNo"] == old["reportNo"] and new["id"] != old["id"]
    assert people["qm"].get("/api/losses/%d" % old["id"]).json["loss"]["qty"] == 3000                              # the old one is untouched


# ---- the lists ----

def test_the_list_orders_open_reports_oldest_first_then_closed_newest_first_and_filters(srv, people):
    with Server() as s:
        pm, pm_info = s.make_user(role="user", isProductionManager=True)
        qm, qm_info = s.make_user(role="user", isQualityManager=True)
        plain, plain_info = s.make_user(role="user")
        ids = [open_loss(plain, plain_info["password"], stage="Storage", description="report %d" % i)["id"] for i in range(4)]
        confirm(pm, pm_info["password"], ids[1]).raise_for_status()
        confirm(pm, pm_info["password"], ids[2]).raise_for_status()
        close(qm, qm_info["password"], ids[2]).raise_for_status()
        close(qm, qm_info["password"], ids[3]).status                                                                  # 409 (not confirmed): stays open
        got = plain.get("/api/losses").json
        assert [x["id"] for x in got["losses"]] == [ids[0], ids[1], ids[3], ids[2]]
        assert all("steps" not in x for x in got["losses"])
        assert got["me"] == {"canConfirm": False, "canClose": False} and pm.get("/api/losses").json["me"] == {"canConfirm": True, "canClose": False}
        assert qm.get("/api/losses").json["me"] == {"canConfirm": False, "canClose": True}
        assert got["repeat"] == {"count": 3, "days": 180}
        assert [x["id"] for x in plain.get("/api/losses?status=closed").json["losses"]] == [ids[2]]
        assert [x["id"] for x in plain.get("/api/losses?status=open").json["losses"]] == [ids[0], ids[3]]
        assert plain.get("/api/losses?status=bogus").status == 400
        sql(s, "UPDATE loss_reports SET opened_at='2026-01-01T00:00:00Z' WHERE id=?", (ids[0],))
        assert plain.get("/api/losses?status=open").json["losses"][0]["ageDays"] > 100
        assert plain.get("/api/losses/99999").status == 404


def test_the_detail_has_the_events_the_repeat_status_and_the_recall_trace_only_for_a_run(srv, people):
    rid = finished_run(people["admin"])
    with_run = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None, stage="Packaging", lostKind="finished", qty=100)
    d = people["qm"].get("/api/losses/%d" % with_run["id"]).json
    assert d["recall"]["run"]["id"] == rid and "lots" in d["recall"] and "customers" in d["recall"]
    assert {"count", "needed", "windowDays", "cause", "stage"} <= set(d["repeat"]) and d["repeat"]["windowDays"] == 180
    assert d["me"] == {"canConfirm": False, "canClose": True}
    assert [e["type"] for e in d["events"]] == ["loss_opened", "loss_confirmed"]
    e = d["events"][0]
    assert {"type", "user", "capacity", "at", "comment", "detail", "entryHash"} <= set(e) and e["detail"]["reportNo"] == with_run["reportNo"] and e["detail"]["reportHash"]
    assert open_loss(people["pm"], people["pm_pw"])["id"] and people["qm"].get("/api/losses/%d" % (with_run["id"] + 1)).json["recall"] is None


def test_the_summary_counts_losses_per_run_with_confirmed_quantities(srv, people):
    rid = finished_run(people["admin"])
    a = open_loss(people["plain"], people["plain_pw"], runId=rid, refText=None, qty=100)
    open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None, lostKind="in_process", qty=40, stage="Separation")
    confirm(people["pm"], people["pm_pw"], a["id"], qty=90).raise_for_status()
    by = people["plain"].get("/api/losses/summary").json["byRun"][str(rid)]
    assert by == {"count": 2, "open": 2, "feedstockKg": 90, "productL": 40}
    assert close(people["qm"], people["qm_pw"], a["id"]).ok
    assert people["plain"].get("/api/losses/summary").json["byRun"][str(rid)]["open"] == 1


def test_the_stats_group_by_month_cause_and_stage(srv):
    with Server() as s:
        pm, pm_info = s.make_user(role="user", isProductionManager=True)
        pw = pm_info["password"]
        open_loss(pm, pw, occurredAt="2026-03-30", foundAt="2026-03-31", qty=100, stage="Extraction")
        open_loss(pm, pw, occurredAt="2026-03-02", foundAt="2026-03-03", qty=50, stage="Extraction")
        open_loss(pm, pw, occurredAt="2026-04-01", foundAt="2026-04-01", qty=30, stage="Extraction", lostKind="finished")
        open_loss(pm, pw, occurredAt="2026-04-05", foundAt="2026-04-06", qty=7, stage="Storage", causeId=cause_id(pm, "Spill / leak"), lostKind="in_process")
        rows = pm.get("/api/losses/stats").json["rows"]
        assert {"month": "2026-03", "cause": "Operator error", "stage": "Extraction", "kind": "feedstock", "unit": "kg", "count": 2, "qty": 150} in rows
        assert {"month": "2026-04", "cause": "Operator error", "stage": "Extraction", "kind": "finished", "unit": "L", "count": 1, "qty": 30} in rows
        assert {"month": "2026-04", "cause": "Spill / leak", "stage": "Storage", "kind": "in_process", "unit": "L", "count": 1, "qty": 7} in rows
        assert len(rows) == 3


# ---- the audit trail ----

def test_the_three_events_are_in_the_chain_with_the_report_hash_and_the_chain_stays_intact(srv, people):
    x = open_loss(people["plain"], people["plain_pw"], stage="Other")
    confirm(people["pm"], people["pm_pw"], x["id"]).raise_for_status()
    close(people["qm"], people["qm_pw"], x["id"]).raise_for_status()
    rows = sql(srv, "SELECT event_type, run_id, detail, capacity FROM release_events WHERE detail LIKE ? ORDER BY id", ('%' + x["reportNo"] + '%',))
    assert [r[0] for r in rows] == ["loss_opened", "loss_confirmed", "loss_closed"]
    assert all(r[1] == 0 for r in rows)                                                                  # no run: run id 0
    assert [r[3] for r in rows] == ["Reporter", "Production Manager", "Quality Manager"]
    details = [json.loads(r[2]) for r in rows]
    assert all(d["reportNo"] == x["reportNo"] and d["qty"] == 3882 and d["unit"] == "kg" and d["cause"] == "Operator error" and d["stage"] == "Other" for d in details)
    assert len({d["reportHash"] for d in details}) == 3                                                   # each event hashes the report as it stood then
    assert people["admin"].get("/api/release/verify").json["ok"] is True
    chk = people["admin"].get("/api/integrity").json
    assert not [i for i in chk["issues"] if i["area"] == "Loss reports"] and chk["chain"]["ok"]


def test_a_run_report_logs_its_run_id_and_shows_in_the_runs_events(srv, people):
    rid = finished_run(people["admin"])
    x = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None)
    assert {r[0] for r in sql(srv, "SELECT event_type FROM release_events WHERE run_id=? AND detail LIKE ?", (rid, '%' + x["reportNo"] + '%'))} == {"loss_opened", "loss_confirmed"}


def test_the_integrity_check_catches_a_report_edited_in_the_database(srv, people):
    x = open_loss(people["plain"], people["plain_pw"], stage="Other", qty=12)
    try:
        sql(srv, "UPDATE loss_reports SET qty=1 WHERE id=?", (x["id"],))
        issues = [i for i in people["admin"].get("/api/integrity").json["issues"] if i["area"] == "Loss reports"]
        assert len(issues) == 1 and issues[0]["severity"] == "error" and x["reportNo"] in issues[0]["message"]
        sql(srv, "UPDATE loss_reports SET qty=12 WHERE id=?", (x["id"],))
        assert not [i for i in people["admin"].get("/api/integrity").json["issues"] if i["area"] == "Loss reports"]
        sql(srv, "INSERT INTO loss_report_steps (report_id,step,by_name,fields_json,created_at) VALUES (?,?,?,?,?)", (x["id"], "confirm", "Someone", "{}", "2026-01-01T00:00:00Z"))
        assert [i for i in people["admin"].get("/api/integrity").json["issues"] if i["area"] == "Loss reports"]       # a step added by hand is caught too
    finally:
        sql(srv, "DELETE FROM loss_report_steps WHERE report_id=? AND by_name='Someone'", (x["id"],))
        sql(srv, "UPDATE loss_reports SET qty=12 WHERE id=?", (x["id"],))


# ---- nothing moves stock ----

def test_a_loss_report_never_moves_stock_the_ledger_or_a_lot_or_tote_status(srv, people):
    rid = finished_run(people["admin"])
    tote = harvest_totes(people["admin"], 1)[0]
    lot = lots_of(srv, rid)[0]
    before = stock_state(srv)
    for kw in ({"runId": rid, "refText": None}, {"toteId": tote, "refText": None}, {"fgLotId": lot["id"], "refText": None, "lostKind": "finished", "stage": "Storage"}, {}):
        x = open_loss(people["pm"], people["pm_pw"], **kw)
        assert close(people["qm"], people["qm_pw"], x["id"]).ok
        y = open_loss(people["plain"], people["plain_pw"], **kw)
        confirm(people["pm"], people["pm_pw"], y["id"], qty=1).raise_for_status()
    assert stock_state(srv) == before


# ---- finishing a run as a loss ----

def loss_draft(client, n_totes=1, with_stages=False):
    """A draft run on fresh totes with no packaging and (unless asked) no production-log entries."""
    totes = harvest_totes(client, n_totes)
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = client.post("/api/production/drafts", body)
    r.raise_for_status()
    return r.json["run"]["id"], totes, body


def test_a_run_with_no_output_finalizes_only_with_a_loss_report_for_that_run(srv, people):
    admin = people["admin"]
    rid, totes, body = loss_draft(admin)
    plain = admin.post("/api/production/drafts/%d/finalize" % rid, body)
    assert plain.status == 400 and "required fields" in plain.json["error"]                                      # without a loss report nothing changes
    other_rid, _t, _b = loss_draft(admin)
    other = open_loss(people["pm"], people["pm_pw"], runId=other_rid, refText=None)
    assert admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=other["id"])).status == 400        # a report for another run
    assert admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=99999)).status == 400
    assert admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=open_loss(people["plain"], people["plain_pw"])["id"])).status == 400   # a report with no run
    assert sql(srv, "SELECT status FROM production_runs WHERE id=?", (rid,))[0][0] == "draft"

    loss = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None, stage="Extraction", qty=800)
    stock_before = dict(sql(srv, "SELECT id, on_hand FROM consumables"))
    fg_before = sql(srv, "SELECT COUNT(*) FROM fg_lots")
    r = admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=loss["id"]))
    assert r.ok, r.text
    assert r.json["runId"] == rid and r.json["outputLitres"] == 0 and r.json["fgLots"] == [] and r.json["releaseState"] == "no_output"
    run = sql(srv, "SELECT status, release_state, output_litres, ibc_used, exclude_from_stats, exclude_reason, loss_report_id, loss_missing, release_review_hash FROM production_runs WHERE id=?", (rid,))[0]
    assert run[:4] == ("completed", "no_output", 0, 0) and run[4] == 1 and run[5] == "Loss report " + loss["reportNo"] and run[6] == loss["id"]
    assert run[7] and "Homogenization" in run[7] and run[8] is None                                               # what was missing is kept, not enforced
    assert sql(srv, "SELECT COUNT(*) FROM fg_lots WHERE run_id=?", (rid,))[0][0] == 0
    after = dict(sql(srv, "SELECT id, on_hand FROM consumables"))
    changed = {i: after[i] - stock_before[i] for i in after if after[i] != stock_before[i]}
    assert list(changed.values()) == [1.0] and sql(srv, "SELECT name FROM consumables WHERE id=?", (list(changed)[0],))[0][0].startswith("Used 1,000 L IBC")      # only the emptied IBC returns to the pool (as in any run): no packaging, no labels
    assert sql(srv, "SELECT COUNT(*) FROM fg_lots") == fg_before
    assert {s[0] for s in sql(srv, "SELECT status FROM tote_lots WHERE run_id=?", (rid,))} == {"consumed"}      # the run consumed its totes: the loss report moved nothing itself


def test_a_no_output_run_cannot_be_reviewed_or_released_and_is_never_offered_by_the_release_gate(srv, people):
    admin = people["admin"]
    rid, _t, body = loss_draft(admin)
    loss = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None)
    admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=loss["id"])).raise_for_status()
    for action, who, pw, extra in (("review", "pm", "pm_pw", {"decision": "approve"}), ("release", "qm", "qm_pw", {"decision": "release"}),
                                   ("reopen", "qm", "qm_pw", {"comment": "again"}), ("resubmit", "pm", "pm_pw", {"comment": "fixed"})):
        r = people[who].post("/api/release/runs/%d/%s" % (rid, action), dict(extra, password=people[pw]))
        assert r.status == 409 and r.json["code"] == "no_output" and "loss" in r.json["error"], (action, r.text)
    assert people["qm"].post("/api/release/runs/%d/release-from-hold" % rid, {"reason": "x"}).status == 409
    assert sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0] == "no_output"
    listed = {r["id"]: r for r in people["qm"].get("/api/release").json["runs"]}
    row = listed[rid]
    assert row["state"] == "no_output" and row["label"] == "No product (loss)" and row["lossReportId"] == loss["id"] and row["lossMissing"]
    assert row["losses"] == {"count": 1, "open": 1, "feedstockKg": 3882, "productL": 0} and row["lots"] == [] and row["releaseStatus"]["stage"] == "No product (loss)"
    detail = people["qm"].get("/api/release/runs/%d" % rid)
    assert detail.ok and detail.json["gate"] == [] and detail.json["state"] == "no_output"
    assert people["qm"].post("/api/production/%d/amendments" % rid, {"category": "other", "reason": "change the log"}).status in (403, 409)
    prod = {r["id"]: r for r in admin.get("/api/production").json["runs"]}[rid]
    assert prod["lossReportId"] == loss["id"] and prod["lossMissing"] and prod["release"]["label"] == "No product (loss)"


def test_a_no_output_run_is_excluded_from_yield_and_the_checks_and_charts_still_work(srv, people):
    admin = people["admin"]
    good = finished_run(admin)
    rid, _t, body = loss_draft(admin)
    loss = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None)
    admin.post("/api/production/drafts/%d/finalize" % rid, dict(body, lossReportId=loss["id"])).raise_for_status()
    plain = admin.get("/api/yield-usage").json
    assert rid not in [r["id"] for r in plain["runs"]] and good in [r["id"] for r in plain["runs"]] and plain["excludedCount"] >= 1        # left out of the statistics
    rows = {r["id"]: r for r in admin.get("/api/yield-usage?includeExcluded=1").json["runs"]}
    assert rows[rid]["excluded"] is True and "Loss report" in rows[rid]["excludeReason"] and rows[good]["excluded"] is False
    assert admin.get("/api/qc-charts").ok and admin.get("/api/dashboard").ok
    chk = admin.get("/api/integrity").json
    assert [i for i in chk["issues"] if i["runId"] == rid and i["severity"] == "error"] == []
    assert admin.get("/api/production/%d/summary.pdf" % rid).status in (200, 400, 409)
    assert admin.get("/api/release/runs/%d/recall" % rid).ok


def test_a_draft_that_has_a_loss_report_cannot_be_discarded(srv, people):
    rid, _t, _b = loss_draft(people["admin"])
    x = open_loss(people["plain"], people["plain_pw"], runId=rid, refText=None)
    r = people["admin"].delete("/api/production/drafts/%d" % rid)
    assert r.status == 409 and x["reportNo"] in r.json["error"]


def test_a_loss_report_does_not_change_the_signed_production_log(srv, people):
    rid = finished_run(people["admin"])
    people["pm"].post("/api/release/runs/%d/review" % rid, {"decision": "approve", "password": people["pm_pw"]}).raise_for_status()
    before = people["qm"].get("/api/release/runs/%d" % rid).json
    assert before["logMatchesReview"] is True and before["state"] == "pending_release"
    x = open_loss(people["pm"], people["pm_pw"], runId=rid, refText=None, lostKind="finished", stage="Packaging", qty=10)
    confirm_again = people["pm"].get("/api/losses/%d" % x["id"])
    assert confirm_again.ok
    after = people["qm"].get("/api/release/runs/%d" % rid).json
    assert after["currentLogHash"] == before["currentLogHash"] == after["reviewedLogHash"] and after["logMatchesReview"] is True
    assert after["state"] == "pending_release" and after["losses"]["count"] == 1
    assert close(people["qm"], people["qm_pw"], x["id"]).ok
    assert people["qm"].get("/api/release/runs/%d" % rid).json["logMatchesReview"] is True
    assert sql(srv, "SELECT release_state FROM production_runs WHERE id=?", (rid,))[0][0] == "pending_release"


# ---- the repeat rule ----

@pytest.fixture
def rep():
    """A server of its own (the repeat count looks at every report), with a Production Manager and a Quality Manager."""
    with Server() as s:
        pm, pm_info = s.make_user(role="user", isProductionManager=True)
        qm, qm_info = s.make_user(role="user", isQualityManager=True)
        yield s, pm, pm_info["password"], qm, qm_info["password"]


def test_the_third_report_with_the_same_cause_and_stage_needs_a_corrective_action(rep):
    s, pm, pm_pw, qm, qm_pw = rep
    first, second, third = (open_loss(pm, pm_pw, stage="Extraction") for _ in range(3))
    assert pm.get("/api/losses/%d" % first["id"]).json["repeat"] == {"count": 1, "needed": False, "windowDays": 180, "cause": "Operator error", "stage": "Extraction"}
    assert pm.get("/api/losses/%d" % third["id"]).json["repeat"]["count"] == 3 and pm.get("/api/losses/%d" % third["id"]).json["repeat"]["needed"] is True
    assert close(qm, qm_pw, first["id"]).ok and close(qm, qm_pw, second["id"]).ok                                         # the 1st and 2nd do not
    r = close(qm, qm_pw, third["id"], capaRequired=False)
    assert r.status == 400 and r.json["code"] == "capa_required" and "3" in r.json["error"] and "Operator error" in r.json["error"] and "Extraction" in r.json["error"]
    assert sql(s, "SELECT status FROM loss_reports WHERE id=?", (third["id"],))[0][0] == "confirmed"
    assert close(qm, qm_pw, third["id"], capaRequired=True, capaText="Retrain", capaOwner="Plant manager", capaDue="2026-12-31").ok
    fourth = open_loss(pm, pm_pw, stage="Extraction")
    assert close(qm, qm_pw, fourth["id"], capaRequired=False).status == 400                                               # the 4th inside the window too


def test_another_cause_or_stage_is_counted_separately(rep):
    s, pm, pm_pw, qm, qm_pw = rep
    for _ in range(2):
        open_loss(pm, pm_pw, stage="Extraction")
    other_cause = open_loss(pm, pm_pw, stage="Extraction", causeId=cause_id(pm, "Equipment failure"))
    other_stage = open_loss(pm, pm_pw, stage="Separation")
    assert close(qm, qm_pw, other_cause["id"]).ok and close(qm, qm_pw, other_stage["id"]).ok


def test_reports_outside_the_window_are_not_counted_and_the_settings_apply(rep):
    s, pm, pm_pw, qm, qm_pw = rep
    old = [open_loss(pm, pm_pw, stage="Storage") for _ in range(2)]
    sql(s, "UPDATE loss_reports SET opened_at='2025-01-01T00:00:00Z' WHERE id IN (?,?)", (old[0]["id"], old[1]["id"]))     # more than 180 days before
    third = open_loss(pm, pm_pw, stage="Storage")
    assert pm.get("/api/losses/%d" % third["id"]).json["repeat"]["count"] == 1
    assert close(qm, qm_pw, third["id"]).ok
    setting(s, "loss_repeat_days", 100000)
    fourth = open_loss(pm, pm_pw, stage="Storage")
    assert pm.get("/api/losses/%d" % fourth["id"]).json["repeat"]["count"] == 4
    assert close(qm, qm_pw, fourth["id"], capaRequired=False).status == 400
    setting(s, "loss_repeat_count", 0)                                                                                   # 0 = never forced
    assert close(qm, qm_pw, fourth["id"], capaRequired=False).ok
    setting(s, "loss_repeat_count", 2)
    five, six = open_loss(pm, pm_pw, stage="Other"), open_loss(pm, pm_pw, stage="Other")
    assert close(qm, qm_pw, five["id"], capaRequired=False).ok and close(qm, qm_pw, six["id"], capaRequired=False).status == 400


def test_a_corrected_report_is_not_counted_twice(rep):
    s, pm, pm_pw, qm, qm_pw = rep
    a = open_loss(pm, pm_pw, stage="Other")
    b = open_loss(pm, pm_pw, stage="Other")
    assert close(qm, qm_pw, a["id"]).ok
    c = open_loss(pm, pm_pw, stage="Other", correctsId=a["id"])                                                          # replaces a, so the third event is not a third report
    assert pm.get("/api/losses/%d" % c["id"]).json["repeat"]["count"] == 2
    assert close(qm, qm_pw, c["id"]).ok and close(qm, qm_pw, b["id"]).ok


# ---- the cause list ----

def test_the_cause_list_is_changed_by_an_administrator_only_and_every_change_is_logged(srv, people):
    assert people["plain"].get("/api/loss-causes").ok and people["pm"].get("/api/loss-causes").json["causes"][0]["name"] == "Operator error"
    for who in ("plain", "pm", "qm"):
        assert people[who].post("/api/loss-causes", {"name": "Nope"}).status == 403
        assert people[who].put("/api/loss-causes/1", {"active": False}).status == 403
    admin = people["admin"]
    name = "Power outage %d" % datetime.datetime.now().microsecond
    added = admin.post("/api/loss-causes", {"name": name})
    assert added.ok and name in [c["name"] for c in added.json["causes"]]
    cid = added.json["id"]
    assert admin.post("/api/loss-causes", {"name": name.lower()}).status == 409 and admin.post("/api/loss-causes", {"name": " "}).status == 400
    assert cid in [c["id"] for c in people["plain"].get("/api/refdata").json["lossCauses"]]
    x = open_loss(people["plain"], people["plain_pw"], causeId=cid)
    renamed = admin.put("/api/loss-causes/%d" % cid, {"name": name + " (grid)", "sortOrder": 5})
    assert renamed.ok and next(c for c in renamed.json["causes"] if c["id"] == cid) == {"id": cid, "name": name + " (grid)", "active": True, "sortOrder": 5, "reports": 1}
    assert admin.put("/api/loss-causes/%d" % cid, {"name": "Operator error"}).status == 409
    assert admin.put("/api/loss-causes/99999", {"active": False}).status == 404
    assert admin.put("/api/loss-causes/%d" % cid, {"active": False}).ok
    assert people["plain"].get("/api/losses/%d" % x["id"]).json["loss"]["causeName"] == name                           # the report keeps the name it was opened with
    log = sql(srv, "SELECT field, old_value, new_value FROM config_log WHERE area='loss-cause' AND key=? ORDER BY id", (str(cid),))
    assert [r[0] for r in log] == ["Cause added: %s" % name, "Name", "Sort order", "Active"] and log[3][1:] == ("yes", "no")


# ---- upgrading ----

def test_a_restart_keeps_the_reports_and_does_not_re_add_a_removed_cause(srv, people):
    with Server() as s:
        admin = s.admin_client()
        pm, pm_info = s.make_user(role="user", isProductionManager=True)
        x = open_loss(pm, pm_info["password"])
        other = cause_id(admin, "Other")
        admin.put("/api/loss-causes/%d" % other, {"active": False}).raise_for_status()
        s.restart()
        admin = s.admin_client()
        pm = s.client()                                                                                                    # the restart picks a new port
        pm.login(pm_info["email"], pm_info["password"]).raise_for_status()
        assert pm.get("/api/losses/%d" % x["id"]).json["loss"]["reportNo"] == x["reportNo"]
        assert "Other" not in [c["name"] for c in admin.get("/api/refdata").json["lossCauses"]]
        assert admin.get("/api/release/verify").json["ok"] is True
