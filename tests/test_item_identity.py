"""KW-86: an inventory item is one specific product. A reagent or CIP agent has a chemical, manufacturer, grade and form (a different one is a NEW item; a different supplier is not);
Manufacturer and Grade are required for new ones; a likely duplicate is warned; changing what an item IS after it has been received needs a reason; every change is in the audit trail.
Packaging, sample containers and labels take only a manufacturer and its product code."""
import datetime
import os

import pytest

from factories import sql
from harness import ROOT, Server

_n = [0]


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def plain(srv):
    return srv.make_user(role="user")[0]


def uniq(prefix):
    _n[0] += 1
    return "%s %d" % (prefix, _n[0])


def reagent(**over):
    return dict({"name": uniq("Sorbate"), "unit": "kg", "chemical": "Potassium sorbate", "manufacturer": uniq("Nantong"), "grade": "FCC", "form": "granular"}, **over)


def get_item(client, name):
    return [c for c in client.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def log(srv, name):
    return sql(srv, "SELECT field, old_value, new_value, changed_by FROM config_log WHERE area='inventory_item' AND key=? ORDER BY id", (name,))


# ---- required fields ----
def test_a_new_reagent_needs_a_manufacturer_and_a_grade_for_everyone(admin, plain):
    for who in (admin, plain):
        r = who.post("/api/consumables", reagent(manufacturer=""))
        assert r.status == 400 and "manufacturer" in r.text.lower()
        r = who.post("/api/consumables", reagent(grade="  "))
        assert r.status == 400 and "grade" in r.text.lower()
    assert plain.post("/api/consumables", reagent()).ok


def test_a_new_cip_agent_needs_them_too_but_a_container_or_label_does_not(admin):
    assert admin.post("/api/consumables", dict(reagent(), isCipAgent=True, manufacturer="")).status == 400
    assert admin.post("/api/consumables", dict(reagent(), isCipAgent=True)).ok
    name = uniq("Tote")
    assert admin.post("/api/consumables", {"name": name, "unit": "ea", "isContainer": True, "litresEach": 1000}).ok            # nothing required
    name2 = uniq("Bottle")
    assert admin.post("/api/consumables", {"name": name2, "unit": "ea", "isContainer": True, "manufacturer": "Berry Global", "manufacturerCode": "B-123",
                                           "grade": "ignored", "form": "ignored", "chemical": "ignored"}).ok
    c = get_item(admin, name2)
    assert (c["manufacturer"], c["manufacturerCode"]) == ("Berry Global", "B-123") and (c["grade"], c["form"], c["chemical"]) == (None, None, None)       # only a maker and a code


def test_the_identity_is_stored_shown_and_logged_when_the_item_is_created(srv, admin):
    r = reagent(manufacturerCode="NT-FCC-25", organic=True)
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert (c["chemical"], c["manufacturer"], c["manufacturerCode"], c["grade"], c["form"], c["organic"]) == ("Potassium sorbate", r["manufacturer"], "NT-FCC-25", "FCC", "granular", True)
    rows = log(srv, r["name"])
    assert len(rows) == 1 and rows[0][0] == "Item identity" and rows[0][1] is None
    assert rows[0][2] == "Chemical: Potassium sorbate; Manufacturer: %s; Product code: NT-FCC-25; Grade: FCC; Form: granular; Organic: yes" % r["manufacturer"] and rows[0][3]


def test_text_is_cleaned_and_clipped(admin):
    r = reagent(manufacturer="  Nantong   Acid \n Co  ", grade="F" * 300)
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert c["manufacturer"] == "Nantong Acid Co" and len(c["grade"]) == 120


# ---- likely duplicates ----
def test_the_same_product_under_another_name_is_warned_and_can_be_added_anyway(admin):
    first = reagent(chemical="Citric acid", manufacturer="Jungbunzlauer", grade="Food", form="anhydrous")          # this test owns this combination
    assert admin.post("/api/consumables", first).ok
    again = dict(first, name=uniq("Another name"), chemical=" citric  ACID", manufacturer="jungbunzlauer ", grade="FOOD", form="Anhydrous")
    r = admin.post("/api/consumables", again)
    assert r.status == 409 and r.json["code"] == "likely_duplicate"
    assert [d["name"] for d in r.json["duplicates"]] == [first["name"]] and r.json["duplicates"][0]["itemNumber"]
    assert "approved supplier" in r.json["error"]
    assert admin.post("/api/consumables", dict(again, confirmDuplicate=True)).ok                          # a warning, not a block


@pytest.mark.parametrize("change", [{"grade": "Technical"}, {"form": "monohydrate"}, {"form": ""}, {"manufacturer": "Other Maker"}, {"chemical": "Citric acid 2"}, {"organic": True}])
def test_a_different_grade_form_manufacturer_chemical_or_organic_status_is_a_new_item(admin, change):
    base = reagent(chemical="Malic acid", manufacturer=uniq("Maker A"), grade="Food", form="granular")
    assert admin.post("/api/consumables", base).ok
    assert admin.post("/api/consumables", dict(base, name=uniq("Different"), **change)).ok


def test_nothing_is_said_when_the_chemical_is_not_known(admin):
    base = reagent(chemical="", manufacturer=uniq("Maker Z"), grade="Food")
    assert admin.post("/api/consumables", base).ok
    assert admin.post("/api/consumables", dict(base, name=uniq("No chemical twin"))).ok                  # chemical, manufacturer and grade must all be given to compare


def test_an_older_item_is_matched_through_its_item_type(admin):
    cit = get_item(admin, "Citric Acid")                                                                  # a seeded item: blank manufacturer and grade, Item type Citric Acid
    assert cit["manufacturer"] is None and cit["grade"] is None and cit["reagentType"] == "Citric Acid"
    assert admin.put("/api/consumables/%d" % cit["id"], {"manufacturer": "Acme Acids", "grade": "Food"}).ok            # filling blanks needs no reason
    r = admin.post("/api/consumables", {"name": uniq("Citric twin"), "unit": "kg", "reagentType": "Citric Acid", "manufacturer": "acme acids", "grade": "food"})
    assert r.status == 409 and [d["name"] for d in r.json["duplicates"]] == ["Citric Acid"]


# ---- editing ----
def test_filling_blanks_and_changing_an_unused_item_need_no_reason_and_every_change_is_logged(srv, admin):
    r = reagent()
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert admin.put("/api/consumables/%d" % c["id"], {"manufacturerCode": "X-1", "grade": "USP"}).ok
    assert admin.put("/api/consumables/%d" % c["id"], {"manufacturerCode": "X-2"}).ok
    got = get_item(admin, r["name"])
    assert (got["grade"], got["manufacturerCode"]) == ("USP", "X-2")
    rows = log(srv, r["name"])[1:]
    assert sorted(((x[0], x[1], x[2]) for x in rows), key=str) == sorted([("Manufacturer product code", None, "X-1"), ("Grade", "FCC", "USP"), ("Manufacturer product code", "X-1", "X-2")], key=str)
    assert admin.put("/api/consumables/%d" % c["id"], {"grade": "USP"}).ok and len(log(srv, r["name"])) == len(rows) + 1               # no change, no line


def test_changing_what_a_received_item_is_needs_a_reason_and_the_reason_is_logged(srv, admin):
    r = reagent()
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert admin.post("/api/consumables/%d/receive" % c["id"], {"receivedDate": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), "lines": [{"qty": 5, "supplierLot": "L-1"}]}).ok
    assert get_item(admin, r["name"])["lotCount"] == 1
    bad = admin.put("/api/consumables/%d" % c["id"], {"grade": "Technical"})
    assert bad.status == 400 and "reason" in bad.text.lower() and "new item" in bad.text
    assert get_item(admin, r["name"])["grade"] == "FCC"
    assert admin.put("/api/consumables/%d" % c["id"], {"manufacturerCode": "FREE-1"}).ok                              # a product code correction is free
    ok = admin.put("/api/consumables/%d" % c["id"], {"grade": "Technical", "identityReason": "Supplier relabelled the product"})
    assert ok.ok and get_item(admin, r["name"])["grade"] == "Technical"
    rows = [x for x in log(srv, r["name"]) if x[0] in ("Grade", "Reason for change")]
    assert rows[0][:3] == ("Grade", "FCC", "Technical") and rows[1][:3] == ("Reason for change", None, "Supplier relabelled the product")


def test_filling_a_blank_on_a_received_item_needs_no_reason(admin):
    r = reagent(form="")
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert admin.post("/api/consumables/%d/receive" % c["id"], {"receivedDate": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), "lines": [{"qty": 1, "supplierLot": "L-2"}]}).ok
    assert admin.put("/api/consumables/%d" % c["id"], {"form": "powder"}).ok


def test_packaging_takes_only_a_manufacturer_and_its_name_changes_only_by_an_administrator_with_a_reason(admin):
    name = uniq("Drum")
    assert admin.post("/api/consumables", {"name": name, "unit": "ea", "isContainer": True}).ok
    c = get_item(admin, name)
    assert admin.put("/api/consumables/%d" % c["id"], {"manufacturer": "Greif", "manufacturerCode": "G-9"}).ok
    assert admin.put("/api/consumables/%d" % c["id"], {"grade": "Food"}).status == 400
    assert admin.put("/api/consumables/%d" % c["id"], {"grade": ""}).ok                                    # blank is fine
    assert admin.put("/api/consumables/%d" % c["id"], {"name": uniq("Renamed drum")}).status == 400                                # a rename needs a reason (KW-90 extends the rename to containers)
    assert get_item(admin, name)["manufacturer"] == "Greif"


# ---- where it shows ----
def test_the_production_log_picker_data_carries_who_made_it(admin):
    cit = get_item(admin, "Citric Acid")
    types = {t["type"]: t for t in admin.get("/api/reagents").json["types"]}
    item = [i for i in types["Citric Acid"]["items"] if i["id"] == cit["id"]][0]
    assert {"manufacturer", "grade", "form", "chemical"} <= set(item)


def test_existing_items_keep_their_names_numbers_and_blanks(admin):
    items = admin.get("/api/consumables").json["consumables"]
    seeded = [c for c in items if c["name"] in ("Potassium Sorbate", "Sodium Benzoate", "CIP Caustic")]
    assert len(seeded) == 3 and all(c["itemNumber"] and c["manufacturer"] is None and c["grade"] is None and c["form"] is None for c in seeded)


def test_the_add_form_states_the_rule_and_suggests_the_agreed_name_pattern():
    js = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    assert "A different manufacturer, grade, form / concentration, organic status or safety data sheet means a NEW item" in js
    assert "A different supplier or distributor is NOT a new item" in js
    start = js.index("function suggestItemName(p)")
    body = js[max(0, start - 250):start + 700]
    assert "NAME_COMMON_GRADES = ['food', 'fcc']" in body and "list(p.form)" in body and "join(' - ')" in body                  # chemical, form(s) - manufacturer
    assert "' (' + tags.join(', ') + ')'" in body and "['Org']" in body and "'Organic'" not in body                             # (grade if not the usual one, Org)


# ---- several grades and forms ----
def test_grade_and_form_can_hold_several_choices_stored_once_each_in_the_order_given(admin):
    r = reagent(chemical="Citric acid", grade=["Food", "fcc", "FOOD"], form="anhydrous; granular, Anhydrous")
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert (c["grade"], c["form"]) == ("Food, fcc", "anhydrous, granular")


def test_the_same_choices_in_another_order_are_a_duplicate_a_different_set_is_not(admin):
    first = reagent(chemical="Ascorbic acid", manufacturer=uniq("Multi Maker"), grade="Food, USP", form="anhydrous, granular")
    assert admin.post("/api/consumables", first).ok
    r = admin.post("/api/consumables", dict(first, name=uniq("Reordered"), grade="usp, food", form=["Granular", "ANHYDROUS"]))
    assert r.status == 409 and [d["name"] for d in r.json["duplicates"]] == [first["name"]]
    assert admin.post("/api/consumables", dict(first, name=uniq("Fewer forms"), form="anhydrous")).ok                    # {anhydrous} is not {anhydrous, granular}
    assert admin.post("/api/consumables", dict(first, name=uniq("More grades"), grade="Food, USP, FCC")).ok


def test_adding_a_choice_to_a_received_item_needs_a_reason_but_reordering_does_not(srv, admin):
    r = reagent(grade="Food", form="anhydrous")
    assert admin.post("/api/consumables", r).ok
    c = get_item(admin, r["name"])
    assert admin.post("/api/consumables/%d/receive" % c["id"], {"receivedDate": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), "lines": [{"qty": 2, "supplierLot": "M-1"}]}).ok
    assert admin.put("/api/consumables/%d" % c["id"], {"form": "anhydrous, granular"}).status == 400
    ok = admin.put("/api/consumables/%d" % c["id"], {"form": ["anhydrous", "granular"], "identityReason": "Supplier ships it granular"})
    assert ok.ok and get_item(admin, r["name"])["form"] == "anhydrous, granular"
    n = len(log(srv, r["name"]))
    assert admin.put("/api/consumables/%d" % c["id"], {"form": "Granular, Anhydrous"}).ok                                # the same set: no change, no reason, no log line
    assert len(log(srv, r["name"])) == n and get_item(admin, r["name"])["form"] == "anhydrous, granular"


def test_the_grade_choices_do_not_offer_organic_and_the_checkboxes_share_one_tidy_style():
    js = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    grades = js[js.index("const ITEM_GRADES = ["):].split("\n", 1)[0]
    assert "'Food'" in grades and "'FCC'" in grades and "Organic" not in grades                                          # Organic is its own checkbox
    css = open(os.path.join(ROOT, "public", "styles.css"), encoding="utf-8").read()
    assert ".check-line input[type=checkbox]" in css and "function checkLine(" in js
    assert js.count("checkLine(") >= 6 and "field('Organic grade" not in js and "field('Used for Sample Point'" not in js
