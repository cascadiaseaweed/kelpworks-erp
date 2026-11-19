"""KW-72: administrators are warned when no backup has been copied off the server for longer than `archive_sync_warn_hours`.

What counts: the sync script (archive key) making ANY request = contact; a nightly backup downloaded IN FULL, by the script or by an administrator = copied off the server. Never seen is too old."""
import datetime
import time

import pytest

from factories import sql
from harness import Server

KEY = "archive-key-for-tests-0123456789abcdef"


@pytest.fixture(scope="module")
def srv():
    with Server(env={"KELP_ERP_ARCHIVE_KEY": KEY}) as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def keyed(srv, path):
    return srv.client().get(path, headers={"X-Archive-Key": KEY})


def info(admin):
    r = admin.get("/api/archive/sync")
    assert r.status == 200, r.text
    return r.json


def info_after_copy(admin, done=lambda i: i["lastOffsite"] and not i["warn"], seconds=10):
    """The server notes a copy only AFTER the last byte of the download has been sent, so the client can be back with its next request first (under load, in the parallel CI run): wait for the note.
    (A later test that sets its own flags must not be overwritten by a note that arrives late, either.)"""
    deadline = time.time() + seconds
    while True:
        i = info(admin)
        if done(i) or time.time() > deadline:
            return i
        time.sleep(0.1)


def hours_ago(h):
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=h)).replace(microsecond=0).isoformat() + "Z"


def set_flags(srv, contact=None, offbox=None):
    sql(srv, "DELETE FROM app_flags WHERE key IN ('archive_last_contact','archive_last_offbox')")
    if contact:
        sql(srv, "INSERT INTO app_flags (key,value) VALUES ('archive_last_contact', ?)", (contact,))
    if offbox:
        sql(srv, "INSERT INTO app_flags (key,value) VALUES ('archive_last_offbox', ?)",
            ('{"at": "%s", "name": "kelp_erp_2026-01-01.zip", "by": "archive key"}' % offbox,))


def make_backup(admin):
    r = admin.post("/api/archive/backup")
    assert r.status == 200 and r.json["created"], r.text
    return r.json["created"]


def test_never_seen_warns_on_both_counts(srv, admin):
    set_flags(srv)
    i = info(admin)
    assert i["applies"] and i["warn"] and i["warnHours"] == 36
    assert i["lastContact"] is None and i["lastOffsite"] is None
    assert len(i["problems"]) == 2 and "No records sync has ever contacted" in i["problems"][0] and "No backup has ever been copied" in i["problems"][1]
    assert admin.get("/api/archive/status").json["sync"]["warn"] is True                # the Admin page reads it from the status too


def test_a_fresh_contact_and_copy_clear_the_warning(srv, admin):
    set_flags(srv)
    name = make_backup(admin)
    assert keyed(srv, "/api/archive/manifest").status == 200                              # any request with the key is contact
    i = info(admin)
    assert i["lastContact"] and i["warn"] and len(i["problems"]) == 1                    # in touch, but nothing copied yet
    got = srv.client().get("/api/archive/backup?name=" + name, headers={"X-Archive-Key": KEY})
    assert got.status == 200 and got.body[:2] == b"PK"
    i = info_after_copy(admin)
    assert i["warn"] is False and i["problems"] == [] and i["lastOffsite"]["name"] == name and i["lastOffsite"]["by"] == "archive key"


def test_stale_contact_and_stale_copy_each_warn(srv, admin):
    set_flags(srv, contact=hours_ago(40), offbox=hours_ago(2))
    i = info(admin)
    assert i["warn"] and len(i["problems"]) == 1 and "last contacted this server 40 hours ago" in i["problems"][0]
    set_flags(srv, contact=hours_ago(1), offbox=hours_ago(40))
    i = info(admin)
    assert i["warn"] and len(i["problems"]) == 1 and "40 hours ago (kelp_erp_2026-01-01.zip)" in i["problems"][0]
    set_flags(srv, contact=hours_ago(100), offbox=hours_ago(30))
    assert "4 days ago" in info(admin)["problems"][0]                                       # long gaps read in days


def test_a_sync_clears_a_stale_warning(srv, admin):
    set_flags(srv, contact=hours_ago(90), offbox=hours_ago(90))
    assert info(admin)["warn"] is True
    name = make_backup(admin)
    assert keyed(srv, "/api/archive/backups").status == 200
    assert srv.client().get("/api/archive/backup?name=" + name, headers={"X-Archive-Key": KEY}).status == 200
    assert info_after_copy(admin)["warn"] is False


def test_an_administrator_downloading_a_backup_by_hand_counts_as_a_copy(srv, admin):
    set_flags(srv, contact=hours_ago(1))
    name = make_backup(admin)
    token = admin.download_token()
    r = srv.client().get("/api/archive/backup?name=" + name + "&token=" + token)
    assert r.status == 200
    i = info_after_copy(admin)
    assert i["warn"] is False and i["lastOffsite"]["by"] != "archive key"


def test_things_that_are_not_a_copy_do_not_count(srv, admin):
    seen = hours_ago(1)
    set_flags(srv, contact=seen)
    wrong = srv.client().get("/api/archive/manifest", headers={"X-Archive-Key": "wrong-key-wrong-key-wrong-key-1234"})
    assert wrong.status == 403 and info(admin)["lastContact"] == seen                      # a wrong key is not contact
    for bad in ("../kelp_erp.db", "kelp_erp_1999-01-01.zip"):
        assert srv.client().get("/api/archive/backup?name=" + bad, headers={"X-Archive-Key": KEY}).status == 404
    i = info(admin)
    assert i["lastOffsite"] is None and i["warn"]                                          # nothing was copied, however much the script asked


def test_the_threshold_is_a_setting_and_zero_turns_the_warning_off(srv, admin):
    set_flags(srv, contact=hours_ago(20), offbox=hours_ago(20))
    assert info(admin)["warn"] is False
    assert admin.put("/api/settings/archive_sync_warn_hours", {"value": 12}).status == 200
    try:
        i = info(admin)
        assert i["warn"] and i["warnHours"] == 12 and len(i["problems"]) == 2
        assert admin.put("/api/settings/archive_sync_warn_hours", {"value": 0}).status == 200
        i = info(admin)
        assert i["warn"] is False and i["applies"] is False
    finally:
        admin.put("/api/settings/archive_sync_warn_hours", {"value": 36})


def test_only_an_administrator_or_the_key_reads_it(srv, admin):
    plain, _ = srv.make_user()
    assert plain.get("/api/archive/sync").status == 403
    assert srv.client().get("/api/archive/sync").status in (401, 403)
    assert keyed(srv, "/api/archive/sync").status == 200                                      # the script may read it too


def test_a_server_that_expects_no_copy_does_not_warn():
    with Server() as s:                                                                         # no archive key, nightly backup off (the development / test default)
        a = s.admin_client()
        i = a.get("/api/archive/sync").json
        assert i["applies"] is False and i["warn"] is False and i["problems"] == []


def test_the_setting_is_listed_on_the_calculations_page(srv, admin):
    s = admin.get("/api/settings").json["settings"]["archive_sync_warn_hours"]
    assert s["value"] == 36 and s["default"] == 36 and "copied off the server" in s["label"]
