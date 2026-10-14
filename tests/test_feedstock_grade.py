"""Feedstock grade: administrator-defined rules (IF / AND / OR with < <= = >= > !=) assign every tote a grade from its pH, ORP, stabilization period and odour."""
import datetime

import pytest

import kelp_erp_server as K
from factories import harvest_totes
from harness import Server


def cond(field, op, value=None):
    return {"field": field, "op": op, "value": value}


def rule(grade, *groups, enabled=True, note=None, rid=1):
    return {"id": rid, "grade": grade, "note": note, "enabled": enabled, "groups": [list(g) for g in groups]}


# ---- the engine, operator by operator ----

@pytest.mark.parametrize("op,value,ph,expected", [
    ("lt", 4, 3.9, True), ("lt", 4, 4.0, False), ("le", 4, 4.0, True), ("le", 4, 4.1, False),
    ("eq", 4, 4.0, True), ("eq", 4, 4.1, False), ("ge", 4, 4.0, True), ("ge", 4, 3.9, False),
    ("gt", 4, 4.1, True), ("gt", 4, 4.0, False), ("ne", 4, 3.5, True), ("ne", 4, 4.0, False),
])
def test_every_comparison_on_a_number(op, value, ph, expected):
    assert K.grade_condition_ok(cond("ph", op, value), {"ph": ph}) is expected


def test_a_missing_value_never_satisfies_a_comparison_but_blank_tests_work():
    assert K.grade_condition_ok(cond("orp", "ge", -1000), {"orp": None}) is False
    assert K.grade_condition_ok(cond("orp", "ne", 5), {"orp": None}) is False                   # not "not equal": unknown is unknown
    assert K.grade_condition_ok(cond("orp", "blank"), {"orp": None}) is True
    assert K.grade_condition_ok(cond("orp", "notblank"), {"orp": 0}) is True                    # zero is a value, not blank
    assert K.grade_condition_ok(cond("odour", "blank"), {"odour": "  "}) is True


def test_odour_intensity_compares_in_the_order_mild_medium_strong():
    assert K.grade_condition_ok(cond("odour_intensity", "le", "Medium"), {"odour_intensity": "Mild"}) is True
    assert K.grade_condition_ok(cond("odour_intensity", "gt", "Mild"), {"odour_intensity": "Strong"}) is True
    assert K.grade_condition_ok(cond("odour_intensity", "lt", "Medium"), {"odour_intensity": "Strong"}) is False
    assert K.grade_condition_ok(cond("odour_intensity", "eq", "medium"), {"odour_intensity": "Medium"}) is True


def test_odour_text_conditions():
    both = {"odour": "Marine, Sweet (Apple Juice)"}
    assert K.grade_condition_ok(cond("odour", "contains", "marine"), both) is True
    assert K.grade_condition_ok(cond("odour", "notcontains", "Sulfuric"), both) is True
    assert K.grade_condition_ok(cond("odour", "eq", "Marine"), both) is False                  # "is" means exactly
    assert K.grade_condition_ok(cond("odour", "ne", "Marine"), both) is True


def test_and_inside_a_set_or_between_sets_and_first_enabled_rule_wins():
    a = rule("A", [cond("ph", "le", 4), cond("orp", "ge", 0)], rid=1)
    b = rule("B", [cond("ph", "le", 5)], [cond("odour", "contains", "Marine")], rid=2)         # (pH <= 5) OR (odour contains Marine)
    rules = [a, b]
    assert K.grade_for(rules, "C", {"ph": 3.8, "orp": 10})[0] == "A"
    assert K.grade_for(rules, "C", {"ph": 3.8, "orp": -20})[0] == "B"                          # fails A's AND, meets B
    assert K.grade_for(rules, "C", {"ph": 6.0, "orp": 10, "odour": "Marine"})[0] == "B"        # second set of B
    assert K.grade_for(rules, "C", {"ph": 6.0, "orp": 10, "odour": "Sulfuric"}) [0] == "C"     # nothing matched: the otherwise grade
    assert K.grade_for([dict(a, enabled=False), b], "C", {"ph": 3.8, "orp": 10})[0] == "B"      # a switched-off rule is skipped
    grade, why, rid = K.grade_for(rules, "C", {"ph": 3.8, "orp": 10})
    assert rid == 1 and "Rule 1" in why and "pH <= 4" in why and "ORP (mV) >= 0" in why


# ---- the administrator's screen and the inventory ----

@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


RULES = {"defaultGrade": "C", "rules": [
    {"grade": "A", "groups": [[cond("ph", "le", 4), cond("orp", "ge", 0), cond("odour_intensity", "le", "Mild")]]},
    {"grade": "B", "groups": [[cond("ph", "le", 5)], [cond("odour", "contains", "Marine"), cond("stab_weeks", "ge", 8)]]},
]}


def set_props(admin, tote, ph, orp, odour=None, intensity=None):
    assert admin.put("/api/totes/%d" % tote, {"ph": ph, "orp": orp}).status == 200
    if odour or intensity:
        r = admin.post("/api/totes/%d/characterize" % tote, {"decision": "accepted", "odour": odour, "odourIntensity": intensity})
        assert r.status == 200, r.text


def grade_of(admin, tote):
    return [t for t in admin.get("/api/totes").json["totes"] if t["id"] == tote][0]


def test_every_tote_has_a_grade_even_before_any_rule_exists(srv, admin):
    tote = harvest_totes(admin, n=1)[0]
    g = grade_of(admin, tote)
    assert g["grade"] == "Ungraded" and g["gradeWhy"] == "No rule matched"
    assert admin.get("/api/grade-rules").json["rules"] == []


def test_only_an_administrator_can_set_the_rules_but_anyone_can_read_them(srv, admin):
    plain, _ = srv.make_user(role="user", isQualityManager=True)
    assert plain.get("/api/grade-rules").status == 200
    assert plain.put("/api/grade-rules", RULES).status == 403
    assert plain.post("/api/grade-rules/test", {"ph": 3.8}).status == 200


def test_the_rules_grade_the_inventory_automatically(srv, admin):
    r = admin.put("/api/grade-rules", RULES)
    assert r.status == 200 and [x["grade"] for x in r.json["rules"]] == ["A", "B"] and r.json["defaultGrade"] == "C"
    a, b1, b2, c = harvest_totes(admin, n=4, date=datetime.date.today().isoformat())
    set_props(admin, a, 3.8, 20, "Marine", "Mild")                       # A: pH <= 4, ORP >= 0, intensity <= Mild
    set_props(admin, b1, 4.9, -30, "Sulfuric (Rotten Eggs)", "Strong")   # B: pH <= 5
    set_props(admin, b2, 5.6, -30, "Marine", "Medium")                   # B through the second set needs 8 weeks: this tote is new, so not B
    set_props(admin, c, 5.6, -30, "Sulfuric (Rotten Eggs)", "Strong")
    assert [grade_of(admin, t)["grade"] for t in (a, b1, b2, c)] == ["A", "B", "C", "C"]
    assert "Rule 1" in grade_of(admin, a)["gradeWhy"]
    set_props(admin, a, 4.4, 20)                                         # a reading changes: the grade follows at once
    assert grade_of(admin, a)["grade"] == "B"


def test_the_stabilization_period_is_a_grade_input(srv, admin):
    old = admin.post("/api/harvest", {"site": "JAM", "species": "SL", "harvestDate": (datetime.date.today() - datetime.timedelta(weeks=9)).isoformat(),
                                      "toteCount": 1, "totalKg": 200, "location": "Cold"}).json["created"]
    ids = {t["lot"]: t["id"] for t in admin.get("/api/totes").json["totes"]}
    tote = ids[old[0]]
    set_props(admin, tote, 5.6, -30, "Marine", "Medium")
    g = grade_of(admin, tote)
    assert g["grade"] == "B" and "Stabilization period (weeks) >= 8" in g["gradeWhy"]


def test_the_rules_are_checked_and_every_save_is_logged(srv, admin):
    bad = [
        {"defaultGrade": "", "rules": []},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": []}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("colour", "eq", "red")]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("ph", "contains", "x")]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("ph", "le", "abc")]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("odour_intensity", "le", "Awful")]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("odour", "contains", "")]]}]},
        {"defaultGrade": "C", "rules": [{"grade": "<script>", "groups": [[cond("ph", "le", 4)]]}]},
    ]
    for body in bad:
        assert admin.put("/api/grade-rules", body).status == 400, body
    before = len(admin.get("/api/grade-rules/log").json["log"])
    assert admin.put("/api/grade-rules", {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("ph", "le", 4)]]}]}).status == 200
    log = admin.get("/api/grade-rules/log").json["log"]
    assert len(log) == before + 1 and "pH <= 4 -> A" in log[0]["new"] and log[0]["by"]
    assert admin.put("/api/grade-rules", RULES).status == 200                                   # leave the module's rules in place


def test_the_test_panel_says_which_rule_applies(srv, admin):
    r = admin.post("/api/grade-rules/test", {"ph": 3.8, "orp": 5, "odourIntensity": "Mild"})
    assert r.json["grade"] == "A" and "Rule 1" in r.json["why"]
    assert admin.post("/api/grade-rules/test", {"ph": 9}).json == {"grade": "C", "why": "No rule matched"}


# ---- every change of a grade is traceable ----

from factories import finalize_run, sql  # noqa: E402


def grade_log(admin, tote):
    return [e for e in admin.get("/api/totes/%d/ph" % tote).json["stabilityLog"] if e["field"] == "Grade"]


def test_a_new_tote_and_a_start_up_back_fill(srv, admin):
    seed_rows = sql(srv, "SELECT COUNT(*) FROM tote_lots WHERE grade IS NOT NULL")[0][0]
    assert seed_rows > 300                                                                        # the totes that were there at start were graded...
    assert sql(srv, "SELECT COUNT(*) FROM tote_stability_log WHERE field='Grade' AND note LIKE 'First graded%' AND user_name='System'")[0][0] == 0   # ...without a log line each
    assert admin.put("/api/grade-rules", RULES).status == 200
    tote = harvest_totes(admin, n=1, date=datetime.date.today().isoformat())[0]
    log = grade_log(admin, tote)
    assert len(log) == 1 and log[0]["oldValue"] is None and log[0]["newValue"] == "C" and log[0]["note"].startswith("First graded. No rule matched")


def test_a_change_of_readings_is_logged_with_the_rule_and_the_readings(srv, admin):
    assert admin.put("/api/grade-rules", RULES).status == 200
    tote = harvest_totes(admin, n=1, date=datetime.date.today().isoformat())[0]
    set_props(admin, tote, 3.8, 20, "Marine", "Mild")                                            # pH and ORP first (C -> B), then the odour reading (B -> A)
    set_props(admin, tote, 4.6, 20)                                                              # A -> B
    log = grade_log(admin, tote)
    assert [(e["oldValue"], e["newValue"]) for e in log] == [("A", "B"), ("B", "A"), ("C", "B"), (None, "C")]
    newest = log[0]
    assert "Readings updated" in newest["note"] and "Rule 2" in newest["note"] and "pH 4.6" in newest["note"] and "ORP 20" in newest["note"] and newest["by"]
    set_props(admin, tote, 4.7, 20)                                                              # still B: nothing is logged
    assert len(grade_log(admin, tote)) == 4


def test_a_rule_change_regrades_every_tote_and_points_to_the_change(srv, admin):
    assert admin.put("/api/grade-rules", RULES).status == 200
    tote = harvest_totes(admin, n=1, date=datetime.date.today().isoformat())[0]
    set_props(admin, tote, 4.4, 20, "Marine", "Mild")                                            # B under RULES
    stricter = {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("ph", "le", 4.5)]]}]}
    r = admin.put("/api/grade-rules", stricter)
    assert r.status == 200 and r.json["regraded"] >= 1
    change_id = admin.get("/api/grade-rules/log").json["log"][0]
    newest = grade_log(admin, tote)[0]
    assert (newest["oldValue"], newest["newValue"]) == ("B", "A") and "Grading rules changed (change #" in newest["note"]
    assert sql(srv, "SELECT COUNT(*) FROM config_log WHERE area='grade' AND id=?", (int(newest["note"].split("#")[1].split(")")[0]),))[0][0] == 1
    assert change_id["by"]
    assert admin.put("/api/grade-rules", RULES).status == 200


def test_a_used_tote_keeps_the_grade_it_had(srv, admin):
    assert admin.put("/api/grade-rules", RULES).status == 200
    tote = harvest_totes(admin, n=1, date=datetime.date.today().isoformat())[0]
    set_props(admin, tote, 3.8, 20, "Marine", "Mild")                                            # A
    rid, f = finalize_run(admin, [tote])
    assert f.ok, f.text
    assert sql(srv, "SELECT status, grade, grade_frozen FROM tote_lots WHERE id=?", (tote,))[0] == ("consumed", "A", 1)
    before = len(grade_log(admin, tote))
    assert admin.put("/api/grade-rules", {"defaultGrade": "C", "rules": []}).status == 200      # a rule change that would make it C
    g = grade_of(admin, tote)
    assert g["grade"] == "A" and "Fixed" in g["gradeWhy"] and len(grade_log(admin, tote)) == before
    assert admin.put("/api/grade-rules", RULES).status == 200


def test_manual_regrade_is_for_administrators(srv, admin):
    plain, _ = srv.make_user(role="user")
    assert plain.post("/api/grade-rules/regrade").status == 403
    assert admin.post("/api/grade-rules/regrade").status == 200


def test_the_daily_check_records_a_grade_that_changed_because_time_passed():
    with Server(env={"KELP_ERP_GRADE_CHECK_SECONDS": "1"}) as s:
        admin = s.admin_client()
        assert admin.put("/api/grade-rules", {"defaultGrade": "C", "rules": [{"grade": "A", "groups": [[cond("stab_weeks", "ge", 8)]]}]}).status == 200
        tote = harvest_totes(admin, n=1, date=datetime.date.today().isoformat())[0]
        assert grade_of(admin, tote)["grade"] == "C"
        old = (datetime.date.today() - datetime.timedelta(weeks=9)).isoformat()
        sql(s, "UPDATE tote_lots SET checkin_date=? WHERE id=?", (old, tote))                      # nine weeks go by (no request touches this tote)
        sql(s, "DELETE FROM app_flags WHERE key='grade_synced_on'")
        import time
        for _ in range(20):
            time.sleep(0.5)
            if grade_log(admin, tote) and grade_log(admin, tote)[0]["newValue"] == "A":
                break
        newest = grade_log(admin, tote)[0]
        assert (newest["oldValue"], newest["newValue"]) == ("C", "A") and "Daily check" in newest["note"] and newest["by"] == "System"
