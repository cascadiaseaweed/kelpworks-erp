"""Sugar Kelp Extract and Macrocystis Kelp Extract: products available for production runs that use NO finished-good labels."""
import pytest

from factories import finalize_run, harvest_totes, sql
from harness import Server

NO_LABEL = ("SACC-LKE", "MACRO-LKE")


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def skus(admin):
    return {s["code"]: s for s in admin.get("/api/refdata").json["skus"]}


def label_items(srv, code):
    return sql(srv, "SELECT name FROM consumables WHERE label_sku_code=?", (code,))


def test_the_two_extracts_are_active_products_with_the_new_names(srv, admin):
    s = skus(admin)
    assert (s["MACRO-LKE"]["name"], s["MACRO-LKE"]["active"]) == ("Macrocystis Kelp Extract", True)
    assert (s["SACC-LKE"]["name"], s["SACC-LKE"]["active"]) == ("Sugar Kelp Extract", True)
    assert s["MACRO-LKE"]["species"] == ["MT"] and s["SACC-LKE"]["species"] == ["SL"]
    assert all(s[c]["tdsTarget"] and s[c]["phTarget"] for c in NO_LABEL)                    # a run needs its targets
    assert not [x for x in s.values() if "Liquid Kelp Extract" in x["name"]]


def test_they_have_no_label_items_and_the_other_products_still_do(srv, admin):
    for code in NO_LABEL:
        assert label_items(srv, code) == [] and skus(admin)[code]["usesLabels"] is False
    assert len(label_items(srv, "KELPIVEX")) == 2 and skus(admin)["KELPIVEX"]["usesLabels"] is True


def test_a_label_item_cannot_be_added_for_them(srv, admin):
    r = admin.post("/api/consumables", {"labelSku": "MACRO-LKE", "labelPackage": "New 1,000 L IBC Tote"})
    assert r.status == 400 and "no finished-good labels" in r.json["error"]
    assert label_items(srv, "MACRO-LKE") == []


@pytest.mark.parametrize("code,species", [("MACRO-LKE", "MT"), ("SACC-LKE", "SL")])
def test_a_run_can_be_made_and_finalized_with_them_and_uses_no_labels(srv, admin, code, species):
    rid, f = finalize_run(admin, harvest_totes(admin, species=species), sku=code)
    assert f.ok, f.text
    assert sql(srv, "SELECT sku_code FROM production_runs WHERE id=?", (rid,))[0][0] == code
    lot = sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
    assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE ref=? AND reason LIKE 'FG labels%'", (lot,))[0][0] == 0
    assert sql(srv, "SELECT COUNT(*) FROM run_label_commits WHERE run_id=?", (rid,))[0][0] == 0


def test_a_labelled_product_still_deducts_labels(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, species="SL"), sku="KELPIVEX")
    assert f.ok, f.text
    lot = sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
    assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE ref=? AND reason LIKE 'FG labels%'", (lot,))[0][0] == 1


def test_an_older_database_is_converted_and_keeps_label_items_that_have_history(srv, admin):
    """As if the database were written before this change: old name, inactive, label items present (one with a stock movement)."""
    sql(srv, "UPDATE fg_skus SET name='Macrocystis Liquid Kelp Extract', active=0, uses_labels=1 WHERE code='MACRO-LKE'")
    sql(srv, "UPDATE fg_skus SET active=0, uses_labels=1 WHERE code='SACC-LKE'")
    for code, pkg in (("MACRO-LKE", "New 1,000 L IBC Tote"), ("MACRO-LKE", "55 gallon drum"), ("SACC-LKE", "New 1,000 L IBC Tote")):
        sql(srv, "INSERT INTO consumables (name,unit,on_hand,reorder_level,label_sku_code,label_package) VALUES (?,'ea',0,0,?,?)", ("FG Label - %s - %s" % (code, pkg), code, pkg))
    used = sql(srv, "SELECT id FROM consumables WHERE label_sku_code='MACRO-LKE' AND label_package='55 gallon drum'")[0][0]
    sql(srv, "INSERT INTO consumable_txns (consumable_id,delta,reason,created_at) VALUES (?,5,'counted','2026-10-01T00:00:00Z')", (used,))
    sql(srv, "DELETE FROM app_flags WHERE key='migrated_remove_labels_of_label_free_skus'")
    srv.restart()
    admin.base_url = srv.url
    s = skus(admin)
    assert s["MACRO-LKE"]["name"] == "Macrocystis Kelp Extract" and s["MACRO-LKE"]["active"] and s["SACC-LKE"]["active"]
    assert [r[0] for r in label_items(srv, "MACRO-LKE")] == ["FG Label - MACRO-LKE - 55 gallon drum"]        # the one with history stays
    assert label_items(srv, "SACC-LKE") == []
    assert sql(srv, "SELECT COUNT(*) FROM consumables WHERE label_sku_code IN ('MACRO-LKE','SACC-LKE')")[0][0] == 1
    srv.restart()                                                                          # and a second start changes nothing
    assert sql(srv, "SELECT COUNT(*) FROM consumables WHERE label_sku_code IN ('MACRO-LKE','SACC-LKE')")[0][0] == 1
