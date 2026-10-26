"""KW-46: a reagent item has an organic grade; an organic product's pickers offer only organic items; an organic product finalized with a reagent that is not marked organic is flagged to
the reviewer (release review and audit trail). The warning and the confirm before finalizing are screen behaviour, pinned at the bottom."""
import pathlib

import pytest

from factories import finalize_run, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def item(admin, name):
    return [c for c in admin.get("/api/consumables").json["consumables"] if c["name"] == name][0]


def conflicts(admin, rid):
    return admin.get("/api/release/runs/%d" % rid).json["organicConflicts"]


def test_an_item_can_be_marked_organic_and_every_change_is_logged(srv, admin):
    r = admin.post("/api/consumables", {"name": "Organic Citric Acid (test)", "unit": "kg", "onHand": 10, "reagentType": "Citric Acid", "organic": True, "manufacturer": "Test Maker", "grade": "Food"})
    assert r.status == 200, r.text
    c = item(admin, "Organic Citric Acid (test)")
    assert c["organic"] is True and c["reagentType"] == "Citric Acid"
    assert item(admin, "Citric Acid")["organic"] is False                                            # the seeded item is not organic
    reagents = {t["type"]: t for t in admin.get("/api/reagents").json["types"]}
    assert {i["name"]: i["organic"] for i in reagents["Citric Acid"]["items"]}["Organic Citric Acid (test)"] is True
    assert admin.put("/api/consumables/%d" % c["id"], {"organic": False}).status == 200
    assert admin.put("/api/consumables/%d" % c["id"], {"organic": True}).status == 200
    log = sql(srv, "SELECT field, old_value, new_value, changed_by FROM config_log WHERE area='inventory_item' AND key='Organic Citric Acid (test)' AND field='Organic grade' ORDER BY id")
    assert [(x[1], x[2]) for x in log] == [("yes", "no"), ("no", "yes")] and log[0][3]
    assert admin.put("/api/consumables/%d" % c["id"], {"organic": True}).status == 200                # no change, no log line
    assert sql(srv, "SELECT COUNT(*) FROM config_log WHERE area='inventory_item' AND key='Organic Citric Acid (test)' AND field='Organic grade'") == [(2,)]


def test_an_item_that_is_not_a_plain_reagent_cannot_be_marked_organic(srv, admin):
    ibc = item(admin, "New 1,000 L IBC Tote")
    admin.put("/api/consumables/%d" % ibc["id"], {"organic": True}).raise_for_status()
    assert item(admin, "New 1,000 L IBC Tote")["organic"] is False


def test_the_products_say_whether_they_are_organic(admin):
    skus = {s["code"]: s["organic"] for s in admin.get("/api/refdata").json["skus"]}
    assert skus["KELPIVEX-O"] is True and skus["FIELDKELP-O"] is True
    assert skus["KELPIVEX"] is False and skus["SACC-LKE"] is False


def test_an_organic_run_with_a_non_organic_reagent_is_flagged_and_an_organic_one_is_not(srv, admin):
    rid, f = finalize_run(admin, harvest_totes(admin, 1), sku="KELPIVEX-O")
    assert f.status == 200, f.text
    bad = conflicts(admin, rid)
    assert {x["reagent"] for x in bad} >= {"Citric Acid"} and all(x["item"] for x in bad)           # the reviewer sees what and which item
    submitted = sql(srv, "SELECT detail FROM release_events WHERE run_id=? AND event_type='submitted'", (rid,))[0][0]
    assert "organicConflicts" in submitted and "Citric Acid" in submitted                              # and the audit trail says so
    plain, f2 = finalize_run(admin, harvest_totes(admin, 1), sku="KELPIVEX")
    assert f2.status == 200 and conflicts(admin, plain) == []                                        # a non-organic product never conflicts
    for name in ("Citric Acid", "Potassium Sorbate", "Sodium Benzoate"):
        admin.put("/api/consumables/%d" % item(admin, name)["id"], {"organic": True}).raise_for_status()
    rid3, f3 = finalize_run(admin, harvest_totes(admin, 1), sku="KELPIVEX-O")
    assert f3.status == 200 and conflicts(admin, rid3) == []


def test_the_screens():
    flat = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
    assert "w.organicProduct = () => false;" in flat and "offered = t => items(t).filter(i => !w.organicProduct() || i.organic" in flat       # organic products: organic items only
    assert "dilutionSummary.reagentWatch.organicProduct = () =>" in flat and "Finalize anyway?" in flat                                           # the SKU drives it; finalize asks
    assert "payload.organic = organicCb.checked" in flat and "Organic grade" in flat
