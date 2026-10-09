"""Smoke tests: a real server on a throwaway database, driven over HTTP (fixtures: tests/conftest.py, machinery: tests/harness.py)."""
import os

from harness import ADMIN_EMAIL, ADMIN_PASSWORD, ROOT


def test_serves_the_app(anon):
    r = anon.get("/")
    assert r.status == 200
    assert "KelpWorks" in r.text


def test_uses_a_throwaway_database(server):
    assert os.path.abspath(server.db_path) != os.path.join(ROOT, "kelp_erp.db")
    assert os.path.exists(server.db_path)


def test_api_requires_a_token(anon):
    assert anon.get("/api/refdata").status == 401
    assert anon.get("/api/refdata", token="not-a-real-token").status == 401


def test_login(server):
    assert server.client().login(ADMIN_EMAIL, "wrong-password").status == 401
    good = server.client().login(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert good.status == 200
    assert good.json["user"]["role"] == "admin"


def test_me(admin):
    r = admin.get("/api/me")
    assert r.status == 200
    assert r.json["email"] == ADMIN_EMAIL


def test_seed_data_is_loaded(admin):
    ref = admin.get("/api/refdata").raise_for_status().json
    assert "SL" in [s["code"] for s in ref["species"]]
    assert "KELPIVEX" in [k["code"] for k in ref["skus"]]


def test_admin_only_routes(admin, make_user):
    user, _ = make_user(role="user")
    assert user.get("/api/users").status == 403
    r = admin.get("/api/users")
    assert r.status == 200
    assert ADMIN_EMAIL in [u["email"] for u in r.json["users"]]
