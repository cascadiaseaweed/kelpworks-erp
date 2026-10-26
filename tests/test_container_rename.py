"""KW-90 (phase 2): containers, finished-good labels and the New IBC tote can be renamed (administrator only, with a reason). Records already written keep the name they were written with and an old name
resolves to the item (the alias); the labels of a container follow its name; a package counts as an IBC from a flag, not from its name; the start-up steps and the Sample Point defaults find the standard
items by key, so a rename sticks."""
import datetime
import os
import sqlite3

import pytest

from factories import feedstock_detail, finalize_run, finished_run, harvest_totes, sql, stage_payloads
from harness import Server

_n = [0]


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture()
def people(srv):
    return {"admin": srv.admin_client(), "qm": srv.make_user(role="user", isQualityManager=True)[0], "pm": srv.make_user(role="user", isProductionManager=True)[0],
            "plain": srv.make_user(role="user")[0]}


def uniq(prefix):
    _n[0] += 1
    return "%s %d" % (prefix, _n[0])


def item(client, key=None, name=None, cid=None):
    items = client.get("/api/consumables").json["consumables"]
    return [c for c in items if (c["systemKey"] == key if key else (c["id"] == cid if cid else c["name"] == name))][0]


def container(admin, litres=20, ibc=False, on_hand=100, **over):
    body = dict({"name": uniq("Test pail"), "unit": "ea", "isContainer": True, "litresEach": litres, "onHand": on_hand, "isIbc": ibc}, **over)
    r = admin.post("/api/consumables", body)
    assert r.ok, r.text
    return item(admin, name=body["name"])


def rename(client, c, new, reason="New supplier's product name"):
    return client.put("/api/consumables/%d" % c["id"], {"name": new, "renameReason": reason})


def on_hand(srv, cid):
    return sql(srv, "SELECT on_hand FROM consumables WHERE id=?", (cid,))[0][0]


def sku_name(admin, code="KELPIVEX"):
    return [s["name"] for s in admin.get("/api/refdata").json["skus"] if s["code"] == code][0]


def label_for(admin, pkg, code="KELPIVEX", **over):
    r = admin.post("/api/consumables", dict({"labelSku": code, "labelPackage": pkg, "onHand": 50}, **over))
    assert r.ok, r.text
    return sql_label(admin, pkg, code)


def sql_label(admin, pkg, code="KELPIVEX"):
    return [c for c in admin.get("/api/consumables").json["consumables"] if c["labelSku"] == code and c["labelPackage"] == pkg][0]


def finalize_draft(admin, rid, ids):
    body = {"sku": "KELPIVEX", "toteIds": ids, "feedstockDetails": {str(t): feedstock_detail() for t in ids}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    return admin.post("/api/production/drafts/%d/finalize" % rid, body)


def run_issues(admin, rid):
    return [i for i in admin.get("/api/integrity").json["issues"] if i["runId"] == rid and i["severity"] in ("error", "warning")]


# ---- the rename rule ----
def test_only_an_administrator_renames_a_container_with_a_reason_and_the_item_stays_the_same(srv, people):
    a = people["admin"]
    c = container(a)
    for who in ("qm", "pm", "plain"):
        assert rename(people[who], c, uniq("Sneaky")).status == 403
    assert rename(a, c, uniq("New pail"), reason="").status == 400
    assert rename(a, c, "  ").status == 400
    old, new = c["name"], uniq("Pail, 20 L, white")
    assert rename(a, c, new, "Moved to a new supplier").status == 200
    after = item(a, cid=c["id"])
    assert (after["name"], after["itemNumber"], after["onHand"], after["litresEach"], after["previousNames"]) == (new, c["itemNumber"], 100, 20, [old])
    assert sql(srv, "SELECT old_name, new_name, reason FROM consumable_name_history WHERE consumable_id=?", (c["id"],)) == [(old, new, "Moved to a new supplier")]
    log = sql(srv, "SELECT field, old_value, new_value FROM config_log WHERE area='inventory_item' AND key=? ORDER BY id", (new,))
    assert ("Name", old, new) in log and ("Reason for rename", None, "Moved to a new supplier") in log
    assert a.get("/api/refdata").json["containerAliases"][old] == new


def test_a_container_name_must_be_new_to_every_item_and_every_old_name(srv, people):
    a = people["admin"]
    c1, c2 = container(a), container(a)
    new1 = uniq("Renamed pail")
    assert rename(a, c1, new1).status == 200
    assert rename(a, c2, new1.upper()).status == 409                                    # a current name
    assert rename(a, c2, c1["name"]).status == 409                                      # the old name of another item
    assert a.post("/api/consumables", {"name": c1["name"], "unit": "ea", "isContainer": True, "litresEach": 5}).status == 409
    assert rename(a, item(a, cid=c1["id"]), uniq("Second name")).status == 200
    assert set(item(a, cid=c1["id"])["previousNames"]) == {new1, c1["name"]}


def test_a_chain_of_renames_all_point_at_the_current_name(srv, people):
    a = people["admin"]
    c = container(a)
    names = [c["name"], uniq("Step one"), uniq("Step two")]
    assert rename(a, c, names[1]).status == 200 and rename(a, item(a, cid=c["id"]), names[2]).status == 200
    aliases = a.get("/api/refdata").json["containerAliases"]
    assert aliases[names[0]] == names[2] and aliases[names[1]] == names[2]


def test_the_used_ibc_pool_stays_locked_and_its_flag_cannot_be_changed(srv, people):
    a = people["admin"]
    pool = item(a, key="ibc_used")
    assert rename(a, pool, "Used totes").status == 409
    assert a.put("/api/consumables/%d" % pool["id"], {"isIbc": True}).status == 409
    assert item(a, key="ibc_new")["isIbc"] is True and pool["isIbc"] is False


# ---- finished-good labels follow their container ----
def test_labels_are_renamed_with_their_container_and_cannot_be_renamed_alone(srv, people):
    a = people["admin"]
    c = container(a)
    lab = label_for(a, c["name"])
    sku = sku_name(a)
    assert lab["name"] == "FG Label - %s - %s" % (sku, c["name"])
    assert rename(a, lab, uniq("Label on its own")).status == 400
    new = uniq("Pail, new name")
    assert rename(a, c, new, "Supplier changed").status == 200
    after = item(a, cid=lab["id"])
    assert (after["name"], after["labelPackage"], after["itemNumber"]) == ("FG Label - %s - %s" % (sku, new), new, lab["itemNumber"])
    assert after["previousNames"] == [lab["name"]]
    h = sql(srv, "SELECT old_name, new_name, reason FROM consumable_name_history WHERE consumable_id=?", (lab["id"],))
    assert h[0][0] == lab["name"] and h[0][2].startswith("Follows container") and "Supplier changed" in h[0][2]
    log = sql(srv, "SELECT field, old_value, new_value FROM config_log WHERE area='inventory_item' AND key=? ORDER BY id", (after["name"],))
    assert ("Name", lab["name"], after["name"]) in log and ("Package", c["name"], new) in log
    assert a.post("/api/consumables", {"labelSku": "KELPIVEX", "labelPackage": c["name"]}).status == 400      # the old name is not a package type any more
    assert sql_label(a, new)["id"] == lab["id"]


def test_a_label_with_a_hand_typed_name_keeps_it_and_only_its_package_follows(srv, people):
    a = people["admin"]
    c = container(a)
    custom = uniq("Special pail label")
    lab = label_for(a, c["name"], name=custom)
    assert lab["name"] == custom
    new = uniq("Pail, other name")
    assert rename(a, c, new).status == 200
    after = item(a, cid=lab["id"])
    assert (after["name"], after["labelPackage"]) == (custom, new)


def test_a_label_name_that_is_already_taken_refuses_the_rename_and_changes_nothing(srv, people):
    a = people["admin"]
    c = container(a)
    lab = label_for(a, c["name"])
    new = uniq("Pail, clash")
    taken = "FG Label - %s - %s" % (sku_name(a), new)
    assert a.post("/api/consumables", {"name": taken, "unit": "kg", "manufacturer": "M", "grade": "G"}).ok
    r = rename(a, c, new)
    assert r.status == 409 and taken in r.text
    assert item(a, cid=c["id"])["name"] == c["name"] and item(a, cid=lab["id"])["name"] == lab["name"]


# ---- a record keeps the name it was written with; an old name resolves to the item ----
def test_a_draft_written_before_the_rename_finalizes_under_the_new_name_and_deducts_from_the_same_item(srv, people):
    a = people["admin"]
    c = container(a, litres=20, on_hand=100)
    lab = label_for(a, c["name"])
    ids = harvest_totes(a, n=1)
    rid, _ = finalize_run(a, ids, pack=((c["name"], 3),), finalize=False)
    new = uniq("Pail, renamed mid-draft")
    assert rename(a, c, new).status == 200
    f = finalize_draft(a, rid, ids)
    assert f.ok, f.text
    lot = sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
    assert sql(srv, "SELECT fg_lot_number, package_size, qty, litres_each FROM fg_lots WHERE run_id=?", (rid,)) == [("%s-%s" % (lot, new), new, 3, 20)]
    assert sql(srv, "SELECT output_litres, ibc_used FROM production_runs WHERE id=?", (rid,))[0] == (60, 0)
    assert sql(srv, "SELECT container_unit FROM run_packaging_entries WHERE run_id=?", (rid,)) == [(c["name"],)]          # the log keeps the text it was written with
    assert on_hand(srv, c["id"]) == 97 and on_hand(srv, lab["id"]) == 47
    assert run_issues(a, rid) == []


def test_a_run_finalized_before_the_rename_is_untouched_and_the_integrity_check_stays_clean(srv, people):
    a = people["admin"]
    c = container(a, litres=10)
    rid = finished_run(a, pack=((c["name"], 2),))
    before = sql(srv, "SELECT fg_lot_number, package_size, qty FROM fg_lots WHERE run_id=?", (rid,))
    assert rename(a, c, uniq("Pail, after the run")).status == 200
    assert sql(srv, "SELECT fg_lot_number, package_size, qty FROM fg_lots WHERE run_id=?", (rid,)) == before
    assert sql(srv, "SELECT container_unit FROM run_packaging_entries WHERE run_id=?", (rid,)) == [(c["name"],)]
    assert run_issues(a, rid) == []
    new_lots = a.get("/api/dashboard").json["finishedGoods"]["lines"]
    assert [l for l in new_lots if l["packageSize"] == c["name"]] == []                               # the dashboard counts the old lot under the container's current name
    assert sum(l["qty"] for l in new_lots if l["packageSize"] == item(a, cid=c["id"])["name"]) == 2


def test_discarding_a_committed_draft_after_a_rename_refunds_the_same_item(srv, people):
    a = people["admin"]
    c = container(a, on_hand=100)
    ids = harvest_totes(a, n=1)
    rid, _ = finalize_run(a, ids, pack=((c["name"], 4),), finalize=False)
    assert a.put("/api/production/%d/stages/packaging" % rid, stage_payloads()["packaging"]).ok
    assert on_hand(srv, c["id"]) == 96                                                                  # the Packaging section's save committed the stock
    assert rename(a, c, uniq("Pail, renamed with a draft open")).status == 200
    assert a.delete("/api/production/drafts/%d" % rid).ok
    assert on_hand(srv, c["id"]) == 100


def test_sample_rows_and_the_csv_stock_update_find_a_renamed_item_by_its_old_name(srv, people):
    a = people["admin"]
    c = container(a, on_hand=10)
    new = uniq("Pail, csv")
    assert rename(a, c, new).status == 200
    r = a.post("/api/consumables/bulk", {"csvText": "name,onHand\n%s,77\n" % c["name"]})
    assert r.ok, r.text
    assert on_hand(srv, c["id"]) == 77
    conn = sqlite3.connect(srv.db_path)
    conn.row_factory = sqlite3.Row
    try:
        import kelp_erp_server as K
        assert K.canonical_container_name(conn, c["name"]) == new and K.canonical_container_name(conn, new) == new and K.canonical_container_name(conn, "No such") == "No such"
        assert K.packaging_container_litres_map(conn)[c["name"]] == 20 and c["name"] not in K.packaging_container_litres_map(conn, current_only=True)
    finally:
        conn.close()


# ---- an IBC is a flag, not a name ----
def test_a_package_counts_as_an_ibc_from_its_flag_and_not_from_its_name(srv, people):
    a = people["admin"]
    flagged = container(a, litres=500, ibc=True, name=uniq("Tote 500"))
    lookalike = container(a, litres=500, name=uniq("Cheap IBC lookalike"))
    assert flagged["isIbc"] is True and lookalike["isIbc"] is False
    rid = finished_run(a, pack=((flagged["name"], 2), (lookalike["name"], 1)))
    assert sql(srv, "SELECT ibc_used, output_litres FROM production_runs WHERE id=?", (rid,))[0] == (2, 1500)
    assert rename(a, flagged, uniq("Tote, renamed")).status == 200
    assert run_issues(a, rid) == []
    rid2 = finished_run(a, pack=((item(a, cid=flagged["id"])["name"], 3),))
    assert sql(srv, "SELECT ibc_used FROM production_runs WHERE id=?", (rid2,))[0][0] == 3


def test_the_ibc_flag_is_an_administrator_setting_that_is_logged_and_needs_a_volume(srv, people):
    a = people["admin"]
    c = container(a, litres=100)
    assert people["qm"].put("/api/consumables/%d" % c["id"], {"isIbc": True}).status == 403
    assert item(a, cid=c["id"])["isIbc"] is False
    assert a.put("/api/consumables/%d" % c["id"], {"isIbc": True}).status == 200
    assert item(a, cid=c["id"])["isIbc"] is True
    assert ("Counts as an IBC", "no", "yes") in sql(srv, "SELECT field, old_value, new_value FROM config_log WHERE area='inventory_item' AND key=?", (c["name"],))
    assert a.put("/api/consumables/%d" % c["id"], {"isIbc": True}).status == 200                       # unchanged: nothing more is logged
    assert len([x for x in sql(srv, "SELECT field FROM config_log WHERE area='inventory_item' AND key=?", (c["name"],)) if x[0] == "Counts as an IBC"]) == 1
    assert a.put("/api/consumables/%d" % c["id"], {"isIbc": False}).status == 200
    nov = container(a, litres=None)
    assert a.put("/api/consumables/%d" % nov["id"], {"isIbc": True}).status == 400
    assert a.put("/api/consumables/%d" % c["id"], {"isIbc": True, "litresEach": None}).status == 400


# ---- the standard items: a rename sticks (own server: it renames the standard containers) ----
def test_the_new_ibc_tote_can_be_renamed_and_runs_use_the_new_name():
    with Server() as s:
        a = s.admin_client()
        ibc = item(a, key="ibc_new")
        old, new = ibc["name"], "Tote, 1,000 L, new - Acme"
        labels_before = [c for c in a.get("/api/consumables").json["consumables"] if c["labelPackage"] == old]
        assert labels_before
        assert rename(a, ibc, new, "Changed IBC supplier").status == 200
        labels = [c for c in a.get("/api/consumables").json["consumables"] if c["labelPackage"] == new]
        assert sorted(l["id"] for l in labels) == sorted(l["id"] for l in labels_before)
        assert all(l["name"].endswith(" - " + new) for l in labels)
        kel = [l for l in labels if l["labelSku"] == "KELPIVEX"][0]
        b_ibc, b_lab, b_used = on_hand(s, ibc["id"]), on_hand(s, kel["id"]), sql(s, "SELECT on_hand FROM consumables WHERE system_key='ibc_used'")[0][0]
        rid = finished_run(a, pack=((new, 2),))
        lot = sql(s, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
        assert sql(s, "SELECT fg_lot_number, qty FROM fg_lots WHERE run_id=?", (rid,)) == [("%s-%s" % (lot, new), 2)]
        assert sql(s, "SELECT ibc_used, output_litres FROM production_runs WHERE id=?", (rid,))[0] == (2, 2000)
        assert (on_hand(s, ibc["id"]), on_hand(s, kel["id"])) == (b_ibc - 2, b_lab - 2)
        assert sql(s, "SELECT on_hand FROM consumables WHERE system_key='ibc_used'")[0][0] == b_used + 1
        rid2 = finished_run(a, pack=((old, 1),))                                                           # a row typed with the old name (an older draft) still works
        lot2 = sql(s, "SELECT processing_lot FROM production_runs WHERE id=?", (rid2,))[0][0]
        assert sql(s, "SELECT fg_lot_number, package_size FROM fg_lots WHERE run_id=?", (rid2,)) == [("%s-%s" % (lot2, new), new)]
        assert sql(s, "SELECT ibc_used FROM production_runs WHERE id=?", (rid2,))[0][0] == 1
        assert on_hand(s, ibc["id"]) == b_ibc - 3
        assert run_issues(a, rid) == [] and run_issues(a, rid2) == []


def test_pre_processing_pack_out_written_under_the_old_name_is_deducted_from_the_renamed_item():
    with Server() as s:
        a = s.admin_client()
        ibc = item(a, key="ibc_new")
        ids = harvest_totes(a, n=2)
        b = a.post("/api/preproc", {}).json
        bid = b["id"]
        assert a.post("/api/preproc/%d/inputs" % bid, {"toteIds": ids}).status == 200
        a.put("/api/preproc/%d" % bid, {"batchDate": "2026-10-10", "operators": "QA", "location": "Cold", "measuredPh": 3.7, "targetPh": 3.7, "citricKg": 0}).raise_for_status()
        a.put("/api/preproc/%d/packaging" % bid, {"rows": [{"container": ibc["name"], "qty": 2, "litresEach": 500}]}).raise_for_status()
        before = on_hand(s, ibc["id"])
        assert rename(a, ibc, "Tote, pre-processing test").status == 200
        r = a.post("/api/preproc/%d/complete" % bid, {})
        assert r.status == 200, r.text
        assert on_hand(s, ibc["id"]) == before - 2
        assert [i for i in a.get("/api/integrity").json["issues"] if i["area"] == "Pre-processing stock"] == []


def test_sample_point_defaults_and_new_runs_follow_a_renamed_sample_vessel():
    with Server() as s:
        a = s.admin_client()
        falcon, bag = item(a, key="sample_falcon_50ml"), item(a, key="sample_bag_100g")
        assert rename(a, falcon, "Falcon tube, 50 mL - Corning").status == 200 and rename(a, bag, "Sample bag, 100 g - Whirl-Pak").status == 200
        ids = harvest_totes(a, n=1)
        rid, _ = finalize_run(a, ids, finalize=False)
        pts = a.get("/api/production/%d/sample-points" % rid).json["samplePoints"]
        assert pts and all(p["container"] != "50 mL falcon tube" for p in pts)
        assert "Falcon tube, 50 mL - Corning" in {p["container"] for p in pts}
        added = a.post("/api/production/%d/sample-points" % rid, {"stage": "packaging"}).json["samplePoints"][-1]
        url = "/api/production/%d/sample-points/%d" % (rid, added["id"])
        micro = a.put(url, {"description": "Microbial"}).json["samplePoints"]
        assert [p for p in micro if p["id"] == added["id"]][0]["container"] == "Falcon tube, 50 mL - Corning"
        solid = a.put(url, {"type": "Solid"}).json["samplePoints"]
        assert [p for p in solid if p["id"] == added["id"]][0]["container"] == "Sample bag, 100 g - Whirl-Pak"


def test_a_start_after_renaming_the_standard_containers_creates_no_second_copy():
    with Server() as s:
        a = s.admin_client()
        for key, new in (("ibc_new", "Tote new, other maker"), ("drum_55", "Drum, 208 L, other maker"), ("sample_falcon_50ml", "Falcon 50 mL, other maker"),
                         ("sample_bag_100g", "Bag 100 g, other maker"), ("sample_bottle_1l", "Bottle 1 L, other maker")):
            assert rename(a, item(a, key=key), new).status == 200
        count = sql(s, "SELECT COUNT(*) FROM consumables")[0][0]
        labels = sql(s, "SELECT name, label_package FROM consumables WHERE label_sku_code IS NOT NULL ORDER BY id")
        s.restart()
        assert sql(s, "SELECT COUNT(*) FROM consumables")[0][0] == count
        assert sql(s, "SELECT COUNT(*) FROM consumables WHERE name IN ('New 1,000 L IBC Tote','55 gallon drum','50 mL falcon tube','100 g sample bag','1 L bottle')")[0][0] == 0
        assert sql(s, "SELECT name, label_package FROM consumables WHERE label_sku_code IS NOT NULL ORDER BY id") == labels
        assert all(pkg in ("Tote new, other maker", "Drum, 208 L, other maker") for _n_, pkg in labels)


def test_the_ibc_flag_is_filled_once_from_the_old_name_rule_in_an_older_database():
    with Server() as s:
        a = s.admin_client()
        container(a, litres=500, name="Old style 500 L IBC")
        container(a, litres=20, name="Old style pail")
        container(a, litres=None, name="Old IBC with no volume")
        sql(s, "UPDATE consumables SET is_ibc=0")
        sql(s, "DELETE FROM app_flags WHERE key='migrated_ibc_flag_kw90'")
        s.restart()
        flags = dict(sql(s, "SELECT name, is_ibc FROM consumables WHERE is_container=1"))
        assert flags["Old style 500 L IBC"] == 1 and flags["New 1,000 L IBC Tote"] == 1
        assert flags["Old style pail"] == 0 and flags["Old IBC with no volume"] == 0 and flags["Used 1,000 L IBC Tote"] == 0
        sql(s, "UPDATE consumables SET is_ibc=0 WHERE name='Old style 500 L IBC'")                         # an administrator's later choice is never overwritten
        s.restart()
        assert sql(s, "SELECT is_ibc FROM consumables WHERE name='Old style 500 L IBC'")[0][0] == 0
