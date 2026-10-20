"""KW-32: Sugar Kelp Extract / Macrocystis Kelp Extract are finished goods that have no SKU until they ship. Assigning the SKU (a signed act by a Production / Quality Manager)
renames the lot -- or splits part of it into a child lot --, deducts the labels of that SKU, and is written to the audit trail. A shipment assigns what has no SKU yet."""
import pytest

from factories import finalize_run, finished_run, harvest_totes, release_run, sql
from harness import Server

IBC = "New 1,000 L IBC Tote"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    plain, _ = srv.make_user(role="user")
    pm, pm_info = srv.make_user(role="user", isProductionManager=True)
    qm, qm_info = srv.make_user(role="user", isQualityManager=True)
    admin = srv.admin_client()
    cust = admin.post("/api/customers", {"name": "Kelp Buyer Ltd"})
    cust.raise_for_status()
    cid = [c["id"] for c in cust.json["customers"] if c["name"] == "Kelp Buyer Ltd"][0]
    return {"plain": plain, "pm": pm, "qm": qm, "admin": admin, "pm_pw": pm_info["password"], "qm_pw": qm_info["password"], "customer": cid}


def bulk_run(srv, people, units=4, sku="SACC-LKE", release=True):
    admin = people["admin"]
    rid, f = finalize_run(admin, harvest_totes(admin, 1), sku=sku, pack=((IBC, units),))
    assert f.ok, f.text
    if release:
        release_run(srv, rid, people["pm"], people["pm_pw"], people["qm"], people["qm_pw"])
    return rid


def lots(admin, rid):
    return [l for l in admin.get("/api/fg").json["fg"] if l["runId"] == rid]


def label_stock(srv, sku):
    return sql(srv, "SELECT on_hand FROM consumables WHERE label_sku_code=? AND label_package=?", (sku, IBC))[0][0]


def assign(client, lot, sku="KELPIVEX", qty=None, password="", **kw):
    body = {"skuCode": sku, "password": password}
    if qty is not None:
        body["qty"] = qty
    return client.post("/api/fg/%d/assign-sku" % lot["id"], dict(body, **kw))


def test_a_label_free_product_makes_finished_goods_without_a_sku_and_the_others_have_one(srv, people):
    rid = bulk_run(srv, people)
    l = lots(people["admin"], rid)[0]
    assert l["sku"] == "SACC-LKE" and l["madeAs"] == "SACC-LKE" and l["skuAssigned"] is False and l["status"] == "on_hand"
    other = finished_run(people["admin"])
    assert all(x["skuAssigned"] is True and x["madeAs"] == x["sku"] for x in lots(people["admin"], other))


def test_only_a_manager_with_the_password_can_assign(srv, people):
    rid = bulk_run(srv, people)
    lot = lots(people["admin"], rid)[0]
    assert assign(people["plain"], lot, password="x").status == 403
    assert assign(people["admin"], lot, password="x").status == 403                          # the admin role alone grants nothing
    assert assign(people["pm"], lot, password="wrong").status == 400
    assert lots(people["admin"], rid)[0]["skuAssigned"] is False


def test_assigning_a_whole_lot_renames_it_in_place_deducts_the_labels_and_is_audited(srv, people):
    rid = bulk_run(srv, people, units=3)
    lot = lots(people["admin"], rid)[0]
    before = label_stock(srv, "KELPIVEX")
    r = assign(people["pm"], lot, "KELPIVEX", password=people["pm_pw"], note="customer order 1")
    assert r.status == 200, r.text
    new = lots(people["admin"], rid)
    assert len(new) == 1 and new[0]["lot"] == lot["lot"] and new[0]["sku"] == "KELPIVEX" and new[0]["skuAssigned"] and new[0]["madeAs"] == "SACC-LKE" and new[0]["assignedBy"]
    assert label_stock(srv, "KELPIVEX") == before - 3
    ev = sql(srv, "SELECT event_type, capacity, comment FROM release_events WHERE run_id=? AND event_type='fg_lot_sku_assigned'", (rid,))
    assert len(ev) == 1 and ev[0][1] == "Production Manager" and ev[0][2] == "customer order 1"
    assert people["admin"].get("/api/release/verify").json["ok"]                              # the hash chain is intact


def test_part_of_a_lot_is_split_into_a_child_lot(srv, people):
    rid = bulk_run(srv, people, units=4)
    parent = lots(people["admin"], rid)[0]
    r = assign(people["qm"], parent, "REGENAKELP", qty=1, password=people["qm_pw"])
    assert r.status == 200, r.text
    ls = sorted(lots(people["admin"], rid), key=lambda x: x["id"])
    p, c = ls[0], ls[1]
    assert (p["qty"], p["skuAssigned"], p["sku"]) == (3, False, "SACC-LKE")
    assert (c["qty"], c["skuAssigned"], c["sku"], c["parentLotId"], c["madeAs"]) == (1, True, "REGENAKELP", p["id"], "SACC-LKE")
    assert c["lot"].startswith(p["lot"]) and c["ph"] == p["ph"] and c["tds"] == p["tds"]
    issues = [i for i in people["admin"].get("/api/integrity").json["issues"] if i["runId"] == rid and i["area"] == "Finished goods vs packaging"]
    assert issues == []                                                                       # parent + child still account for what was packaged
    assign(people["qm"], p, "REGENAKELP", qty=3, password=people["qm_pw"]).raise_for_status()   # the rest, later
    assert all(x["skuAssigned"] for x in lots(people["admin"], rid))


def test_what_cannot_be_assigned(srv, people):
    rid = bulk_run(srv, people, units=2)
    lot = lots(people["admin"], rid)[0]
    pm = lambda **kw: assign(people["pm"], lot, password=people["pm_pw"], **kw)
    assert pm(sku="SACC-LKE").status == 400                                                   # a label-free product is not a destination
    assert pm(sku="NOPE").status == 400
    assert pm(qty=3).status == 400 and pm(qty=0).status == 400 and pm(qty=1.5).status == 400
    pending = bulk_run(srv, people, release=False)
    assert assign(people["pm"], lots(people["admin"], pending)[0], password=people["pm_pw"]).status == 400     # not released for sale
    assign(people["pm"], lot, password=people["pm_pw"]).raise_for_status()
    assert pm().status == 409                                                                 # already has its SKU


def test_a_shipment_assigns_the_sku_the_line_names(srv, people):
    rid = bulk_run(srv, people, units=4)
    lot = lots(people["admin"], rid)[0]
    ship = lambda **kw: people["pm"].post("/api/shipments", dict({"customerId": people["customer"], "lines": [{"fgLotId": lot["id"], "qty": 2}]}, **kw))
    assert ship().status == 400 and "no SKU" in ship().text                                     # nothing says what it ships as
    assert ship(lines=[{"fgLotId": lot["id"], "qty": 2, "skuCode": "KELPIVEX"}]).status == 400   # signed act: password missing
    assert lots(people["admin"], rid)[0]["qty"] == 4                                          # and nothing was changed
    assert people["plain"].post("/api/shipments", {"customerId": people["customer"], "lines": [{"fgLotId": lot["id"], "qty": 2, "skuCode": "KELPIVEX"}], "password": "x"}).status in (400, 403)
    r = ship(lines=[{"fgLotId": lot["id"], "qty": 2, "skuCode": "KELPIVEX"}], password=people["pm_pw"])
    assert r.status == 200, r.text
    line = r.json["shipment"]["lines"][0]
    assert line["sku"] == "KELPIVEX" and line["qty"] == 2
    ls = sorted(lots(people["admin"], rid), key=lambda x: x["id"])
    assert [(x["sku"], x["skuAssigned"], x["qty"]) for x in ls] == [("SACC-LKE", False, 2), ("KELPIVEX", True, 0)]
    assert ls[1]["status"] == "sold"


def test_two_lines_of_one_lot_can_ship_as_different_skus(srv, people):
    rid = bulk_run(srv, people, units=4)
    lot = lots(people["admin"], rid)[0]
    r = people["pm"].post("/api/shipments", {"customerId": people["customer"], "password": people["pm_pw"],
                                              "lines": [{"fgLotId": lot["id"], "qty": 1, "skuCode": "KELPIVEX"}, {"fgLotId": lot["id"], "qty": 3, "skuCode": "FIELDKELP"}]})
    assert r.status == 200, r.text
    assert sorted((l["sku"], l["qty"]) for l in r.json["shipment"]["lines"]) == [("FIELDKELP", 3), ("KELPIVEX", 1)]
    over = people["pm"].post("/api/shipments", {"customerId": people["customer"], "password": people["pm_pw"],
                                                 "lines": [{"fgLotId": lots(people["admin"], bulk_run(srv, people, units=2))[0]["id"], "qty": 3, "skuCode": "KELPIVEX"}]})
    assert over.status == 400


def test_an_assigned_lot_ships_as_before_and_a_moved_part_keeps_its_state(srv, people):
    rid = bulk_run(srv, people, units=2, sku="KELPIVEX")                                       # a labelled product: assigned from the start
    lot = lots(people["admin"], rid)[0]
    assert lot["skuAssigned"] is True
    r = people["plain"].post("/api/shipments", {"customerId": people["customer"], "lines": [{"fgLotId": lot["id"], "qty": 1}]})
    assert r.status == 200 and r.json["shipment"]["lines"][0]["sku"] == "KELPIVEX"            # no password, no permission needed: nothing to assign
    rid2 = bulk_run(srv, people, units=4)
    parent = lots(people["admin"], rid2)[0]
    people["admin"].post("/api/fg/%d/move" % parent["id"], {"toLocation": "Dock", "qty": 1}).raise_for_status()
    moved = [x for x in lots(people["admin"], rid2) if x["id"] != parent["id"]][0]
    assert moved["skuAssigned"] is False and moved["madeAs"] == "SACC-LKE" and moved["ph"] == parent["ph"] and moved["tds"] == parent["tds"]
