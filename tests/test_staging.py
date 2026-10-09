"""Full backup (live) and restore (staging only): the machinery that seeds a staging server with a copy of live data.

The live server is the default environment. A staging server is started with KELP_ERP_ENV=staging, KELP_ERP_ALLOW_RESTORE=1 and a
KELP_ERP_STAGING_PASSWORD, and is the only kind of server that will accept a restore.
"""
import io
import json
import os
import sqlite3
import zipfile

import pytest

from harness import Server
from test_migrations import build_legacy_db

STAGING_ENV = {"KELP_ERP_ENV": "staging", "KELP_ERP_ALLOW_RESTORE": "1", "KELP_ERP_STAGING_PASSWORD": "staging-pass-9"}


@pytest.fixture
def staging():
    with Server(env=STAGING_ENV) as srv:
        yield srv


def put_file(server, folder, name, content):
    d = os.path.join(server.tmp, folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "wb") as f:
        f.write(content)


def read_zip(data):
    return zipfile.ZipFile(io.BytesIO(data))


def make_zip(members, manifest=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
        if manifest:
            zf.writestr("manifest.json", json.dumps({"format": "kelpworks-backup", "version": 1, "createdAt": "x", "env": "production"}))
    return buf.getvalue()


# ---- which environment am I? ----

def test_env_is_public_and_production_by_default(anon):
    r = anon.get("/api/env")
    assert r.status == 200
    assert r.json == {"env": "production", "restoreEnabled": False}


def test_staging_reports_itself(staging):
    assert staging.client().get("/api/env").json == {"env": "staging", "restoreEnabled": True}


# ---- the full backup (taken on the live site) ----

def test_full_backup_is_admin_only(server, anon, make_user):
    assert anon.get("/api/admin/backup?full=1").status == 401
    user, _ = make_user(role="user")
    assert user.get("/api/admin/backup?full=1").status == 403


def test_full_backup_contains_database_documents_and_manifest(server, admin):
    put_file(server, "uploads", "report.pdf", b"%PDF sentinel")
    put_file(server, "lab_templates", "form.docx", b"template sentinel")
    r = admin.get("/api/admin/backup?full=1")
    assert r.status == 200 and r.headers["Content-Type"] == "application/zip"
    zf = read_zip(r.body)
    names = zf.namelist()
    assert {"kelp_erp.db", "manifest.json", "uploads/report.pdf", "lab_templates/form.docx"} <= set(names)
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "kelpworks-backup" and manifest["files"]["uploads"] >= 1
    assert zf.read("uploads/report.pdf") == b"%PDF sentinel"


def test_plain_database_backup_still_works(admin):
    r = admin.get("/api/admin/backup")
    assert r.status == 200 and r.body.startswith(b"SQLite format 3")


# ---- restore: refused unless this is a staging server ----

def test_live_server_refuses_restore(server, admin):
    zip_bytes = admin.get("/api/admin/backup?full=1").body
    r = admin.post_bytes("/api/admin/restore", zip_bytes)
    assert r.status == 403
    assert "disabled" in r.json["error"]


def test_restore_needs_a_staging_password():
    with Server(env={"KELP_ERP_ENV": "staging", "KELP_ERP_ALLOW_RESTORE": "1", "KELP_ERP_STAGING_PASSWORD": ""}) as srv:
        admin = srv.admin_client()
        r = admin.post_bytes("/api/admin/restore", make_zip({"kelp_erp.db": b"x"}))
        assert r.status == 400 and "KELP_ERP_STAGING_PASSWORD" in r.json["error"]


def test_restore_is_admin_only(staging, make_user):
    user, _ = staging.make_user(role="user")
    assert user.post_bytes("/api/admin/restore", b"x").status == 403
    assert staging.client().post_bytes("/api/admin/restore", b"x").status == 401


# ---- restore: the real thing ----

def test_restore_copies_live_data_and_resets_passwords(server, admin, staging):
    live_user, live_info = server.make_user(role="user")          # a person that exists only on the live site, with a live password
    put_file(server, "uploads", "lab-report.pdf", b"live document")
    staging_info = {"email": "staging-only@test.local"}           # something that must NOT survive the restore
    staging.admin_client().post("/api/users", {"name": "Staging Only", "email": staging_info["email"], "password": "staging-only-pass", "role": "user",
                                               "mustChange": False}).raise_for_status()
    zip_bytes = admin.get("/api/admin/backup?full=1").body

    r = staging.admin_client().post_bytes("/api/admin/restore", zip_bytes)
    assert r.status == 200, r.text
    assert r.json["ok"] and r.json["backupEnv"] == "production"

    # the copy has the live people, none of the staging-only ones, and nobody's live password works
    c = staging.client()
    assert c.login(live_info["email"], live_info["password"]).status == 401          # the live password
    assert c.login(live_info["email"], STAGING_ENV["KELP_ERP_STAGING_PASSWORD"]).status == 200
    adm = staging.client()
    assert adm.login("admin@test.local", STAGING_ENV["KELP_ERP_STAGING_PASSWORD"]).status == 200
    emails = [u["email"] for u in adm.get("/api/users").json["users"]]
    assert live_info["email"] in emails and staging_info["email"] not in emails

    # documents came across too, and the data is on the staging disk (not the live one)
    with open(os.path.join(staging.tmp, "uploads", "lab-report.pdf"), "rb") as f:
        assert f.read() == b"live document"
    assert staging.client().get("/api/env").json["env"] == "staging"
    staging.restart()                                                              # and it survives a restart
    assert staging.client().login(live_info["email"], STAGING_ENV["KELP_ERP_STAGING_PASSWORD"]).status == 200


@pytest.mark.parametrize("label,payload", [
    ("not a zip", b"this is not a zip file"),
    ("no manifest", make_zip({"kelp_erp.db": b"x"}, manifest=False)),
    ("path traversal", make_zip({"kelp_erp.db": b"x", "uploads/../../evil.txt": b"x"})),
    ("absolute path", make_zip({"kelp_erp.db": b"x", "/etc/evil.txt": b"x"})),
    ("unknown folder", make_zip({"kelp_erp.db": b"x", "other/file.txt": b"x"})),
    ("no database", make_zip({"uploads/a.txt": b"x"})),
    ("damaged database", make_zip({"kelp_erp.db": b"not a sqlite file" * 100})),
])
def test_bad_backups_are_rejected_and_change_nothing(staging, label, payload):
    keeper, info = staging.make_user(role="user")
    r = staging.admin_client().post_bytes("/api/admin/restore", payload)
    assert r.status == 400, "%s: %s" % (label, r.text)
    assert staging.client().login(info["email"], info["password"]).status == 200       # untouched
    assert not os.path.exists(os.path.join(staging.tmp, "evil.txt"))


def test_restoring_an_older_database_migrates_it(staging, tmp_path):
    """A live database written by an older version of the code (here the release before the SGS update) restores onto newer code."""
    old_db = build_legacy_db("64a35b4", tmp_path)
    with open(old_db, "rb") as f:
        payload = make_zip({"kelp_erp.db": f.read()})
    r = staging.admin_client().post_bytes("/api/admin/restore", payload)
    assert r.status == 200, r.text
    c = staging.client()
    assert c.login("admin@kelp.local", STAGING_ENV["KELP_ERP_STAGING_PASSWORD"]).status == 200
    assert c.get("/api/refdata").status == 200
    conn = sqlite3.connect(staging.db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(lab_analyses)")}
        assert "req_note" in cols                                  # a column added by a recent migration
    finally:
        conn.close()
