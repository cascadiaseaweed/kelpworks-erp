"""SOP links (batch 11): an administrator chooses which SOPs appear at which section, box or window of the production log; the SOP reference key is optional and
editable; the places are the same list in the server and in the app."""
import os
import re

import pytest

import kelp_erp_server as K
from factories import sql
from harness import ROOT, Server

SPOT = "production.homogenization.process_check"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


@pytest.fixture(scope="module")
def plain(srv):
    return srv.make_user()[0]


def make_sop(admin, name, **extra):
    r = admin.post("/api/sop-documents", dict({"name": name}, **extra))
    assert r.status == 200, r.text
    return r.json["sop"]["id"]


def refdata(client):
    return client.get("/api/refdata").json


# ---- the list of places is one list ----

def test_every_place_in_the_server_list_is_wired_in_the_app_and_every_wired_place_is_listed():
    with open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8") as f:
        js = f.read()
    wired = set(re.findall(r"'((?:production|cip|samples|shipping)\.[a-z_]+(?:\.[a-z_]+)*)'", js))
    assert wired == K.SOP_SPOT_KEYS, (sorted(wired - K.SOP_SPOT_KEYS), sorted(K.SOP_SPOT_KEYS - wired))
    assert len(K.SOP_SPOTS) == len(K.SOP_SPOT_KEYS)                                          # no key twice
    assert all(all(row) for row in K.SOP_SPOTS)                                              # key, area, stage, kind, label: none empty


def test_the_places_are_offered_to_every_signed_in_user(srv, plain):
    r = refdata(plain)
    assert [s["key"] for s in r["sopSpots"]] == [row[0] for row in K.SOP_SPOTS]
    assert isinstance(r["sopLinks"], dict)
    one = [s for s in r["sopSpots"] if s["key"] == "production.homogenization.qc_check"][0]
    assert one == {"key": "production.homogenization.qc_check", "area": "Production log", "stage": "Homogenization", "kind": "QC Check",
                   "label": "Lot characterization", "group": "Production log: Homogenization"}


# ---- the link that used to be coded against a key ----

def test_the_solids_loading_sop_link_is_carried_over_once(srv, admin, plain):
    wet = [s for s in refdata(plain)["sops"] if s["key"] == "wet_solids_sop"][0]
    assert refdata(plain)["sopLinks"][SPOT] == [wet["id"]]
    assert admin.put("/api/sop-links/" + SPOT, {"sopIds": []}).status == 200                # an administrator removes it ...
    srv.restart()
    for client in (admin, plain):
        client.base_url = srv.url
    assert SPOT not in refdata(plain)["sopLinks"]                                           # ... and a restart does not put it back
    assert admin.put("/api/sop-links/" + SPOT, {"sopIds": [wet["id"]]}).status == 200


# ---- choosing where an SOP is linked ----

def test_an_sop_is_linked_to_places_and_unlinked_again(srv, admin, plain):
    sid = make_sop(admin, "Pasteurizing the lot")
    r = admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["production.pasteurization", "cip.page"]})
    assert r.status == 200 and sorted(r.json["sop"]["spots"]) == ["cip.page", "production.pasteurization"]
    links = refdata(plain)["sopLinks"]
    assert sid in links["production.pasteurization"] and sid in links["cip.page"]
    listed = [s for s in admin.get("/api/sop-documents").json["sops"] if s["id"] == sid][0]
    assert sorted(listed["spots"]) == ["cip.page", "production.pasteurization"]
    assert admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["cip.page"]}).status == 200
    assert sid not in refdata(plain)["sopLinks"].get("production.pasteurization", [])
    fields = [e[0] for e in sql(srv, "SELECT field FROM sop_document_edits WHERE sop_id=? ORDER BY id", (sid,))]
    assert fields == ["Linked at", "Linked at", "Unlinked from"]                            # every change is on the SOP's history


def test_only_administrators_choose_and_only_real_places_are_accepted(srv, admin, plain):
    sid = make_sop(admin, "Guarded SOP")
    assert plain.put("/api/sop-documents/%d/links" % sid, {"spots": ["cip.page"]}).status == 403
    assert plain.put("/api/sop-links/cip.page", {"sopIds": [sid]}).status == 403
    assert admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["production.nowhere"]}).status == 400
    assert admin.put("/api/sop-documents/%d/links" % sid, {"spots": "cip.page"}).status == 400
    assert admin.put("/api/sop-links/production.nowhere", {"sopIds": [sid]}).status == 404
    assert admin.put("/api/sop-links/cip.page", {"sopIds": [999999]}).status == 400
    assert admin.put("/api/sop-documents/999999/links", {"spots": []}).status == 404
    assert sid not in refdata(plain)["sopLinks"].get("cip.page", [])                 # nothing was half-applied


def test_the_sops_at_one_place_can_be_chosen_from_the_place(srv, admin, plain):
    a, b = make_sop(admin, "First at the place"), make_sop(admin, "Second at the place")
    spot = "production.packaging.entries"
    r = admin.put("/api/sop-links/" + spot, {"sopIds": [a, b]})
    assert r.status == 200 and sorted(r.json["sopLinks"][spot]) == sorted([a, b])
    assert admin.put("/api/sop-links/" + spot, {"sopIds": [b]}).json["sopLinks"][spot] == [b]
    assert spot + "" in refdata(plain)["sopLinks"] and a not in refdata(plain)["sopLinks"][spot]
    assert [s for s in admin.get("/api/sop-documents").json["sops"] if s["id"] == b][0]["spots"] == [spot]


def test_a_numbered_sop_is_listed_before_the_unnumbered_ones(srv, admin, plain):
    nums = [s["docNumber"] for s in plain.get("/api/documents").json["sops"]]
    numbered = [n for n in nums if n]
    assert nums[:len(numbered)] == numbered and numbered == sorted(numbered)


def test_an_sop_cannot_be_deleted_here_so_its_links_stay(srv, admin, plain):
    sid = make_sop(admin, "Stays in KelpWorks")
    admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["production.packaging"]})
    r = admin.delete("/api/sop-documents/%d" % sid)
    assert r.status == 409 and r.json["code"] == "controlled_in_sharepoint"
    assert sid in refdata(plain)["sopLinks"]["production.packaging"]


# ---- the places ----

def test_the_places_carry_an_area_a_section_and_a_type_for_filtering_and_grouping():
    info = list(K.SOP_SPOT_INFO.values())
    assert [i["area"] for i in info if i["area"] != "Production log"] == sorted([i["area"] for i in info if i["area"] != "Production log"], key=lambda a: ["CIP Log", "Samples", "Shipping"].index(a))
    assert {i["area"] for i in info} == {"Production log", "CIP Log", "Samples", "Shipping"}
    stages = []
    for i in info:
        if i["area"] == "Production log" and (not stages or stages[-1] != i["stage"]):
            stages.append(i["stage"])
    assert stages == ["Initiation", "Feedstock", "Homogenization", "Extraction", "Separation", "Pasteurization", "Dilution & Preservation", "Packaging"]   # one block per stage, in log order
    for st in stages:                                                                        # every stage has its whole-section place first
        first = [i for i in info if i["area"] == "Production log" and i["stage"] == st][0]
        assert first["kind"] == "Section" and first["label"] == "Whole section"
    kinds = {i["kind"] for i in info}
    assert kinds == {"Section", "Page", "View", "Process Check", "QC Check", "Sample Point", "Other box", "Window"}
    with open(os.path.join(ROOT, "public", "app.js"), encoding="utf-8") as f:
        js = f.read()
    listed = re.search(r"const SOP_KIND_ORDER = \[([^\]]*)\]", js).group(1)
    assert set(re.findall(r"'([^']+)'", listed)) == kinds                                    # the picker knows every type, so the "group by type" view loses nothing
    assert {i["group"] for i in info if i["area"] != "Production log"} == {"CIP Log", "Samples", "Shipping"}


def test_the_production_log_windows_are_not_offered():
    assert not [k for k in K.SOP_SPOT_KEYS if k in ("production.run_window", "production.process_log", "production.amend", "production.documents",
                                                     "production.sample_labels", "production.lab_results")]
    assert {"cip.page", "samples.page", "samples.cart", "shipping.page", "shipping.shipment_window"} <= K.SOP_SPOT_KEYS


def test_a_link_to_a_place_that_no_longer_exists_is_removed_at_start(srv, admin, plain):
    sid = make_sop(admin, "Old window link")
    sql(srv, "INSERT INTO sop_links (spot, sop_id, created_by, created_at) VALUES ('production.amend', ?, 'old', 'now')", (sid,))
    assert sql(srv, "SELECT COUNT(*) FROM sop_links WHERE spot='production.amend'")[0][0] == 1
    srv.restart()
    for client in (admin, plain):
        client.base_url = srv.url
    assert sql(srv, "SELECT COUNT(*) FROM sop_links WHERE spot='production.amend'")[0][0] == 0


def test_everyone_sees_where_each_sop_is_linked(srv, admin, plain):
    sid = make_sop(admin, "Visible links")
    admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["cip.page", "shipping.page"]})
    mine = [s for s in plain.get("/api/documents").json["sops"] if s["id"] == sid][0]
    assert sorted(mine["spots"]) == ["cip.page", "shipping.page"]                            # a plain user reads the Documents tab's "Linked at"


# ---- one SOP to many places at once ----

def test_one_sop_is_added_to_and_removed_from_many_places_at_once(srv, admin, plain):
    sid, other = make_sop(admin, "Bulk SOP"), make_sop(admin, "Other bulk SOP")
    admin.put("/api/sop-documents/%d/links" % sid, {"spots": ["samples.cart"]})
    homog = [i["key"] for i in K.SOP_SPOT_INFO.values() if i["group"] == "Production log: Homogenization"]
    r = admin.post("/api/sop-links/bulk", {"sopId": sid, "action": "add", "spots": homog})
    assert r.status == 200
    mine = [s for s in admin.get("/api/sop-documents").json["sops"] if s["id"] == sid][0]
    assert sorted(mine["spots"]) == sorted(homog + ["samples.cart"])                         # added to every place; the older link stays
    admin.post("/api/sop-links/bulk", {"sopId": other, "action": "add", "spots": homog[:2]})
    r = admin.post("/api/sop-links/bulk", {"sopId": sid, "action": "remove", "spots": homog[:3]})
    mine = [s for s in admin.get("/api/sop-documents").json["sops"] if s["id"] == sid][0]
    assert sorted(mine["spots"]) == sorted(homog[3:] + ["samples.cart"])
    assert other in refdata(plain)["sopLinks"][homog[0]]                                     # another SOP's links are untouched
    fields = [e[0] for e in sql(srv, "SELECT field FROM sop_document_edits WHERE sop_id=? ORDER BY id", (sid,))]
    assert fields.count("Linked at") == 1 + len(homog) and fields.count("Unlinked from") == 3


def test_bulk_linking_is_checked(srv, admin, plain):
    sid = make_sop(admin, "Bulk guarded")
    body = {"sopId": sid, "action": "add", "spots": ["cip.page"]}
    assert plain.post("/api/sop-links/bulk", body).status == 403
    assert admin.post("/api/sop-links/bulk", dict(body, action="replace")).status == 400
    assert admin.post("/api/sop-links/bulk", dict(body, spots="cip.page")).status == 400
    assert admin.post("/api/sop-links/bulk", dict(body, spots=["cip.page", "production.nowhere"])).status == 400
    assert admin.post("/api/sop-links/bulk", dict(body, sopId=999999)).status == 404
    assert sid not in refdata(plain)["sopLinks"].get("cip.page", [])                         # a refused request changes nothing


# ---- the reference key ----

def test_the_reference_key_can_be_set_changed_and_cleared(srv, admin, plain):
    sid = make_sop(admin, "Keyed SOP")
    key = lambda: [s for s in admin.get("/api/sop-documents").json["sops"] if s["id"] == sid][0]["key"]
    assert key() is None
    assert admin.put("/api/sop-documents/%d" % sid, {"name": "Keyed SOP", "key": "  keyed_sop "}).status == 200
    assert key() == "keyed_sop"
    other = make_sop(admin, "Another SOP")
    r = admin.put("/api/sop-documents/%d" % other, {"name": "Another SOP", "key": "keyed_sop"})
    assert r.status == 409 and "already in use" in r.json["error"]
    assert admin.put("/api/sop-documents/%d" % sid, {"name": "Keyed SOP"}).status == 200      # a rename that says nothing about the key leaves it alone
    assert key() == "keyed_sop"
    assert admin.put("/api/sop-documents/%d" % sid, {"name": "Keyed SOP", "key": ""}).status == 200
    assert key() is None
    assert plain.put("/api/sop-documents/%d" % sid, {"name": "x", "key": "y"}).status == 403
    edits = [(e[0], e[1], e[2]) for e in sql(srv, "SELECT field, old_value, new_value FROM sop_document_edits WHERE sop_id=? AND field='Reference key' ORDER BY id", (sid,))]
    assert edits == [("Reference key", "-", "keyed_sop"), ("Reference key", "keyed_sop", "-")]
