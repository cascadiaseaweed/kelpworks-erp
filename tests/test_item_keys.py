"""KW-89 (phase 1): the standard items are found by a stable KEY, not by name. The Used IBC pool is locked; reagents and CIP agents can be renamed by an administrator only (reason, log, the
old name kept as an alias); each reagent type has one default item chosen by item number."""
import datetime
import os
import sqlite3

import pytest

import kelp_erp_server as K
from factories import finished_run, sql
from harness import ROOT, Server

_n = [0]
EXPECTED_KEYS = {"citric_acid": "Citric Acid", "potassium_sorbate": "Potassium Sorbate", "sodium_benzoate": "Sodium Benzoate", "cip_acid": "CIP Acid", "cip_caustic": "CIP Caustic",
                 "cip_sanitizer": "CIP Sanitizer", "ibc_new": "New 1,000 L IBC Tote", "ibc_used": "Used 1,000 L IBC Tote", "drum_55": "55 gallon drum",
                 "sample_falcon_50ml": "50 mL falcon tube", "sample_bag_100g": "100 g sample bag", "sample_bottle_1l": "1 L bottle", "sample_bottle_2l": "2 L bottle"}


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture()
def people(srv):                                                                                         # fresh clients each time: a restart changes the port
    return {"admin": srv.admin_client(), "qm": srv.make_user(role="user", isQualityManager=True)[0], "pm": srv.make_user(role="user", isProductionManager=True)[0],
            "plain": srv.make_user(role="user")[0]}


def uniq(prefix):
    _n[0] += 1
    return "%s %d" % (prefix, _n[0])


def item(client, key=None, name=None):
    items = client.get("/api/consumables").json["consumables"]
    return [c for c in items if (c["systemKey"] == key if key else c["name"] == name)][0]


def rename(client, c, new, reason="Real product name"):
    return client.put("/api/consumables/%d" % c["id"], {"name": new, "renameReason": reason})


def reagent(admin, rtype="Citric Acid", **over):
    body = dict({"name": uniq("Acid"), "unit": "kg", "reagentType": rtype, "chemical": rtype, "manufacturer": uniq("Maker"), "grade": "Food"}, **over)
    assert admin.post("/api/consumables", body).ok
    return item(admin, name=body["name"])


# ---- the keys ----
def test_every_standard_item_carries_its_key_and_only_the_used_ibc_pool_is_locked(srv, people):
    assert dict(sql(srv, "SELECT system_key, name FROM consumables WHERE system_key IS NOT NULL")) == EXPECTED_KEYS
    items = people["plain"].get("/api/consumables").json["consumables"]
    assert {c["systemKey"] for c in items if c["systemKey"]} == set(EXPECTED_KEYS)
    assert [c["name"] for c in items if c["locked"]] == ["Used 1,000 L IBC Tote"]


def test_keys_are_assigned_again_at_start_from_the_standard_names_with_nothing_duplicated(srv):
    before = sql(srv, "SELECT COUNT(*) FROM consumables")[0][0]
    sql(srv, "UPDATE consumables SET system_key=NULL")
    srv.restart()
    assert dict(sql(srv, "SELECT system_key, name FROM consumables WHERE system_key IS NOT NULL")) == EXPECTED_KEYS
    assert sql(srv, "SELECT COUNT(*) FROM consumables")[0][0] == before


def test_a_renamed_standard_item_is_not_created_a_second_time_at_the_next_start(srv, people):
    a = people["admin"]
    for key, new in (("sodium_benzoate", "Sodium benzoate, granular - Test Maker"), ("cip_caustic", "Caustic soda 50 % - Test Maker")):
        c = item(a, key=key)
        assert rename(a, c, new).status == 200
    count = sql(srv, "SELECT COUNT(*) FROM consumables")[0][0]
    srv.restart()
    a = srv.admin_client()
    assert sql(srv, "SELECT COUNT(*) FROM consumables")[0][0] == count
    assert sql(srv, "SELECT COUNT(*) FROM consumables WHERE name IN ('Sodium Benzoate','CIP Caustic')")[0][0] == 0
    assert item(a, key="sodium_benzoate")["name"] == "Sodium benzoate, granular - Test Maker" and item(a, key="cip_caustic")["name"] == "Caustic soda 50 % - Test Maker"


# ---- rename ----
def test_only_an_administrator_renames_with_a_reason_and_it_is_logged_with_the_old_name_kept(srv, people):
    a = people["admin"]
    c = reagent(a)
    for who in ("qm", "pm", "plain"):
        assert rename(people[who], c, uniq("Sneaky")).status == 403
    assert rename(a, c, uniq("New name"), reason="").status == 400
    assert rename(a, c, "   ").status == 400
    assert a.post("/api/consumables/%d/receive" % c["id"], {"receivedDate": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), "lines": [{"qty": 3, "supplierLot": "R-1"}]}).ok
    old, new = c["name"], uniq("Citric acid, anhydrous - Real")
    assert rename(a, c, new, "Matches the supplier's product").status == 200
    after = item(a, name=new)
    assert (after["id"], after["itemNumber"], after["lotCount"], after["onHand"]) == (c["id"], c["itemNumber"], 1, 3) and after["previousNames"] == [old]
    assert sql(srv, "SELECT old_name, new_name, reason, changed_by FROM consumable_name_history WHERE consumable_id=?", (c["id"],))[0][:3] == (old, new, "Matches the supplier's product")
    log = sql(srv, "SELECT field, old_value, new_value FROM config_log WHERE area='inventory_item' AND key=? ORDER BY id", (new,))
    assert ("Name", old, new) in log and ("Reason for rename", None, "Matches the supplier's product") in log
    assert rename(a, after, new).status == 200                                                                    # the same name again: nothing to do


def test_names_stay_unique_also_against_old_names_and_a_label_cannot_be_renamed_on_its_own(srv, people):
    a = people["admin"]
    c1, c2 = reagent(a), reagent(a)
    first, second = uniq("Renamed one"), uniq("Renamed two")
    assert rename(a, c1, first).status == 200
    assert rename(a, c2, first.upper()).status == 409                                                              # a current name, any case
    assert rename(a, c2, c1["name"]).status == 409                                                                 # the old name of another item
    assert a.post("/api/consumables", {"name": c1["name"], "unit": "kg", "manufacturer": "M", "grade": "G"}).status == 409
    assert rename(a, item(a, name=first), c1["name"]).status == 200                                                # an item may take its own old name back
    assert rename(a, item(a, name=c1["name"]), first).status == 200
    label = [c for c in a.get("/api/consumables").json["consumables"] if c["labelSku"]][0]               # a container can be renamed since KW-90 (tests/test_container_rename.py); a label follows its container
    assert rename(a, label, "Label on its own").status == 400


def test_a_renamed_item_is_still_found_by_its_old_name(srv, people):
    a = people["admin"]
    c = reagent(a)
    old, new = c["name"], uniq("Found by alias")
    assert rename(a, c, new).status == 200
    conn = sqlite3.connect(srv.db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert K.Handler._consumable_by_name(None, conn, old)["id"] == c["id"] and K.Handler._consumable_by_name(None, conn, new)["id"] == c["id"]
        assert K.Handler._consumable_by_name(None, conn, "No such item") is None
    finally:
        conn.close()


# ---- the lock ----
def test_the_used_ibc_pool_cannot_be_renamed_or_changed_by_anyone_but_stock_still_moves(srv, people):
    a = people["admin"]
    pool = item(a, key="ibc_used")
    assert pool["locked"] is True
    assert rename(a, pool, "Used totes").status == 409 and rename(people["plain"], pool, "Used totes").status in (403, 409)
    assert a.put("/api/consumables/%d" % pool["id"], {"litresEach": 1000}).status == 409
    assert a.put("/api/consumables/%d" % pool["id"], {"isSampleContainer": True}).status == 409
    assert a.put("/api/consumables/%d" % pool["id"], {"reorderLevel": 4, "location": ""}).status == 200            # the ordinary fields still work
    assert item(a, key="ibc_used")["name"] == "Used 1,000 L IBC Tote" and item(a, key="ibc_used")["reorderLevel"] == 4
    assert a.post("/api/consumables/%d/adjust" % pool["id"], {"delta": 5, "reason": "count"}).ok
    d = a.post("/api/dispose", {"type": "consumable", "items": [{"id": pool["id"], "qty": 1}], "reason": "broken tote", "date": datetime.date.today().isoformat()})
    assert d.status == 200                                                                                        # a write-off is stock moving, the item itself stays


def test_emptied_totes_return_to_the_pool_found_by_its_key_even_if_its_name_differs(srv, people):
    a = people["admin"]
    sql(srv, "UPDATE consumables SET name='Pool of used totes' WHERE system_key='ibc_used'")                      # only a database edit can do this: the server refuses the rename
    before = sql(srv, "SELECT on_hand FROM consumables WHERE system_key='ibc_used'")[0][0]
    finished_run(a)
    after = sql(srv, "SELECT on_hand FROM consumables WHERE system_key='ibc_used'")[0][0]
    assert after > before
    assert sql(srv, "SELECT COUNT(*) FROM consumables WHERE name='Used 1,000 L IBC Tote'")[0][0] == 0
    sql(srv, "UPDATE consumables SET name='Used 1,000 L IBC Tote' WHERE system_key='ibc_used'")


# ---- the default of a reagent type ----
def test_each_reagent_type_has_one_default_and_at_upgrade_it_is_the_item_that_was_first_before(srv, people):
    rows = dict(sql(srv, "SELECT reagent_type, name FROM consumables WHERE is_type_default=1"))
    assert rows == {"Citric Acid": "Citric Acid", "Potassium Sorbate": "Potassium Sorbate", "Sodium Benzoate": rows["Sodium Benzoate"]}
    types = {t["type"]: t for t in people["plain"].get("/api/reagents").json["types"]}
    assert types["Citric Acid"]["items"][0]["name"] == "Citric Acid" and types["Citric Acid"]["defaultItemId"] == types["Citric Acid"]["items"][0]["id"]


def test_an_administrator_moves_the_default_by_item_number_it_is_logged_and_the_picker_follows(srv, people):
    a = people["admin"]
    cit = item(a, key="citric_acid")
    mine = reagent(a, "Citric Acid")
    for who in ("qm", "pm", "plain"):
        assert people[who].put("/api/consumables/%d" % mine["id"], {"isTypeDefault": True}).status == 403
    assert a.put("/api/consumables/%d" % cit["id"], {"isTypeDefault": False}).status == 400                          # a type always has one: move it instead
    assert a.put("/api/consumables/%d" % mine["id"], {"isTypeDefault": True}).status == 200
    types = {t["type"]: t for t in a.get("/api/reagents").json["types"]}
    assert types["Citric Acid"]["defaultItemId"] == mine["id"] and types["Citric Acid"]["items"][0]["id"] == mine["id"]
    assert [c["isTypeDefault"] for c in a.get("/api/consumables").json["consumables"] if c["reagentType"] == "Citric Acid" and c["id"] in (cit["id"], mine["id"])].count(True) == 1
    log = sql(srv, "SELECT old_value, new_value FROM config_log WHERE area='inventory_item' AND field='Default for Citric Acid' ORDER BY id DESC LIMIT 1")
    assert log == [("Citric Acid", mine["name"])]
    assert rename(a, item(a, key="citric_acid"), uniq("Citric acid, old stock")).status == 200                       # renaming the old one changes no default
    assert {t["type"]: t for t in a.get("/api/reagents").json["types"]}["Citric Acid"]["defaultItemId"] == mine["id"]
    typeless_name = uniq("No type")
    assert a.post("/api/consumables", {"name": typeless_name, "unit": "kg", "manufacturer": "M", "grade": "G"}).ok
    assert a.put("/api/consumables/%d" % item(a, name=typeless_name)["id"], {"isTypeDefault": True}).status == 400            # only an item with a reagent type can be a default
    assert a.put("/api/consumables/%d" % cit["id"], {"isTypeDefault": True}).status == 200                           # and back
    assert {t["type"]: t for t in a.get("/api/reagents").json["types"]}["Citric Acid"]["defaultItemId"] == cit["id"]


def test_a_type_that_loses_its_default_gets_one_again_at_the_next_start(srv):
    sql(srv, "UPDATE consumables SET is_type_default=0")
    srv.restart()
    assert len(sql(srv, "SELECT 1 FROM consumables WHERE is_type_default=1 AND reagent_type='Potassium Sorbate'")) == 1
    assert len(sql(srv, "SELECT 1 FROM consumables WHERE is_type_default=1 AND reagent_type='Citric Acid'")) == 1


# ---- the pages ----
def test_the_edit_window_shows_the_rename_the_default_and_the_lock():
    js = open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8").read()
    assert "Reason for renaming" in js and "'Previously called: '" in js and "Default ' + c.reagentType + ' item'" in js
    assert "Locked system item. " in js and "c.locked ? el('span'" in js and "'Default')" in js
