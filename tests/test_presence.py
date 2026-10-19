"""KW-07: presence -- who has which run / batch / tote open, so a second person is warned before editing it. In memory only; an entry expires after
`presence_timeout_seconds`; a tab's call replaces what it announced before."""
import time

import pytest

import kelp_erp_server as K
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def people(srv):
    a, a_info = srv.make_user(role="user")
    b, b_info = srv.make_user(role="user")
    return {"a": a, "b": b, "admin": srv.admin_client()}


def beat(client, tab, *cards):
    r = client.post("/api/presence", {"tab": tab, "open": [{"key": k, "label": "x"} for k in cards]})
    assert r.status == 200, r.text
    return r.json


def names(out, key):
    return [p["id"] for p in out["presence"].get(key, [])]


def test_a_second_user_sees_who_has_a_card_open_and_each_is_listed_online(people):
    a, b = people["a"], people["b"]
    first = beat(a, "tabaaaa1", "run:11")
    assert names(first, "run:11") == [first["me"]]
    out = beat(b, "tabbbbb1", "run:11", "batch:3")
    assert sorted(names(out, "run:11")) == sorted([first["me"], out["me"]])         # both are in run 11
    assert names(out, "batch:3") == [out["me"]]
    assert {p["id"] for p in out["online"]} >= {first["me"], out["me"]}
    assert all(p["initials"] and p["name"] for p in out["online"])


def test_a_tab_replaces_what_it_announced_and_leaving_clears_it(people):
    a, b = people["a"], people["b"]
    beat(a, "tabaaaa2", "run:21", "tote:5")
    out = beat(a, "tabaaaa2", "tote:5")                                              # the run window was closed
    assert names(out, "run:21") == [] and len(names(out, "tote:5")) == 1
    out = beat(a, "tabaaaa2")                                                         # everything closed
    assert names(out, "tote:5") == []
    assert a.post("/api/presence", {"tab": "tabaaaa2", "open": []}).json["me"] in [p["id"] for p in out["online"]]    # still online, just not in a card


def test_presence_expires_after_the_timeout_setting(srv, people):
    a, b, admin = people["a"], people["b"], people["admin"]
    assert admin.put("/api/settings/presence_timeout_seconds", {"value": 1}).status == 200
    try:
        beat(a, "tabaaaa3", "run:31")
        time.sleep(1.4)                                                               # the browser went away without leaving
        out = beat(b, "tabbbbb3")
        assert names(out, "run:31") == []
    finally:
        admin.put("/api/settings/presence_timeout_seconds", {"value": 90}).raise_for_status()


def test_closing_a_tab_removes_it_at_once(people):
    a, b = people["a"], people["b"]
    beat(a, "tabaaaa7", "run:71")
    assert a.post("/api/presence", {"tab": "tabaaaa7", "leave": True}).status == 200
    out = beat(b, "tabbbbb7")
    assert names(out, "run:71") == []


def test_two_tabs_of_one_person_count_once(people):
    a = people["a"]
    beat(a, "tabaaaa4", "run:41")
    out = beat(a, "tabaaaa5", "run:41")
    assert len(names(out, "run:41")) == 1


def test_bad_requests_are_refused_and_it_needs_a_sign_in(srv, people):
    a, anon = people["a"], srv.client()
    assert a.post("/api/presence", {"tab": "x", "open": []}).status == 400                        # tab id too short
    assert a.post("/api/presence", {"tab": "tabaaaa6", "open": [{"key": "customer:1"}]}).status == 400   # unknown kind of card
    assert a.post("/api/presence", {"tab": "tabaaaa6", "open": [{"key": "run:%d" % i} for i in range(1, 30)]}).status == 400
    assert anon.post("/api/presence", {"tab": "tabaaaa6", "open": []}).status == 401


def test_the_setting_is_listed_and_initials_come_from_the_name(people):
    admin = people["admin"]
    assert admin.get("/api/refdata").json["settings"]["presence_timeout_seconds"]["value"] == 90
    assert K.presence_initials("Plant Admin") == "PA" and K.presence_initials("daniel") == "D" and K.presence_initials("") == "?"
