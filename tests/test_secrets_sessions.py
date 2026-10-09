"""Secrets and sessions (risk review batch 2: R-04, R-12, R-13).

No public signing secret, no default passwords outside development, sessions that end when they should, downloads that use the same
authentication as the API, and a login that is throttled and does not reveal which emails exist.
"""
import base64
import hashlib
import hmac
import json
import re
import time

import pytest

from harness import ADMIN_EMAIL, ADMIN_PASSWORD, Server

OLD_PUBLIC_SECRET = "dev-secret-change-me"          # the signing secret the source code used to contain


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def forge_token(secret, uid=1):
    body = b64(json.dumps({"uid": uid, "exp": int(time.time()) + 3600, "tv": 0}).encode())
    sig = b64(hmac.new(secret.encode(), body.encode("ascii"), hashlib.sha256).digest())
    return "%s.%s" % (body, sig)


# ---- R-04: the signing secret ----

def test_a_token_forged_with_the_old_public_secret_is_rejected():
    with Server(env={"KELP_ERP_SECRET": None}) as srv:                  # no secret configured
        c = srv.client()
        c.token = forge_token(OLD_PUBLIC_SECRET)
        assert c.get("/api/users").status == 401


def test_the_old_public_secret_is_not_accepted_even_when_configured():
    with Server(env={"KELP_ERP_SECRET": OLD_PUBLIC_SECRET}) as srv:
        c = srv.client()
        c.token = forge_token(OLD_PUBLIC_SECRET)
        assert c.get("/api/users").status == 401


def test_a_generated_secret_is_kept_across_restarts():
    with Server(env={"KELP_ERP_SECRET": None}) as srv:
        admin = srv.admin_client()
        assert "generated signing key" in srv.log_tail()
        srv.restart()
        admin.base_url = srv.url                                     # (a restart picks a new port)
        assert admin.get("/api/me").status == 200                    # the same key was loaded again: existing sessions survive a restart


# ---- R-04: default passwords ----

def test_no_default_passwords_outside_development():
    with Server(env={"KELP_ERP_ADMIN_PASSWORD": None, "KELP_ERP_INITIAL_PASSWORD": None}) as srv:
        c = srv.client()
        assert c.login(ADMIN_EMAIL, "kelp1234").status == 401
        assert c.login("dpedde@cascadiaseaweed.com", "Cascadia123!").status == 401               # the staff roster default
        shown = re.search(r"administrator account (\S+)\s+password: (\S+)", srv.log_tail(100))
        assert shown, "the generated admin password should be printed once at first start"
        assert srv.client().login(shown.group(1), shown.group(2)).status == 200


def test_development_mode_keeps_the_convenience_defaults():
    with Server(env={"KELP_ERP_ENV": "development", "KELP_ERP_ADMIN_PASSWORD": None, "KELP_ERP_INITIAL_PASSWORD": None}) as srv:
        assert srv.client().get("/api/env").json["env"] == "development"
        assert srv.client().login(ADMIN_EMAIL, "kelp1234").status == 200
        assert srv.client().login("dpedde@cascadiaseaweed.com", "Cascadia123!").status == 200


def test_the_login_page_has_no_prefilled_credentials():
    with Server() as srv:
        html = srv.client().get("/").text
        assert "kelp1234" not in html and 'value="admin@kelp.local"' not in html


# ---- R-04: must-change-password is enforced by the server ----

def test_a_user_who_must_change_their_password_can_do_nothing_else(server, admin):
    email, pw = "mustchange@test.local", "temporary-pass-1"
    admin.post("/api/users", {"name": "Must Change", "email": email, "password": pw, "role": "user", "mustChange": True}).raise_for_status()
    c = server.client()
    login = c.login(email, pw)
    assert login.status == 200 and login.json["user"]["mustChange"] is True
    refused = c.get("/api/refdata")
    assert refused.status == 403 and refused.json.get("code") == "password_change_required"
    assert c.get("/api/reports/xlsx?token=%s" % c.token).status == 403                  # downloads too
    assert c.get("/api/me").status == 200
    changed = c.post("/api/me/password", {"currentPassword": pw, "newPassword": "a-new-long-password"})
    assert changed.status == 200
    c.token = changed.json["token"]
    assert c.get("/api/refdata").status == 200


# ---- R-12: sessions end when they should ----

def make(server, admin, name):
    email, pw = "%s@test.local" % name, "%s-password-1" % name
    admin.post("/api/users", {"name": name, "email": email, "password": pw, "role": "user", "mustChange": False}).raise_for_status()
    uid = [u["id"] for u in admin.get("/api/users").json["users"] if u["email"] == email][0]
    return email, pw, uid


def test_changing_your_password_ends_your_other_sessions(server, admin):
    email, pw, _ = make(server, admin, "pwchange")
    here, elsewhere = server.client(), server.client()
    here.login(email, pw)
    elsewhere.login(email, pw)
    r = here.post("/api/me/password", {"currentPassword": pw, "newPassword": "brand-new-password"})
    assert r.status == 200
    here.token = r.json["token"]
    assert here.get("/api/me").status == 200                  # this session carries on with the fresh token
    assert elsewhere.get("/api/me").status == 401             # the other session is over


def test_an_admin_password_reset_ends_the_users_sessions(server, admin):
    email, pw, uid = make(server, admin, "reset")
    c = server.client()
    c.login(email, pw)
    assert admin.post("/api/users/%d/password" % uid, {"password": "reset-by-admin-1"}).status == 200
    assert c.get("/api/me").status == 401


def test_a_deactivated_user_loses_api_and_download_access(server, admin):
    email, pw, uid = make(server, admin, "deactivate")
    c = server.client()
    c.login(email, pw)
    assert c.get("/api/reports/xlsx?token=%s" % c.token).status == 200
    assert admin.put("/api/users/%d" % uid, {"active": False}).status == 200
    assert c.get("/api/me").status in (401, 403)
    assert c.get("/api/reports/xlsx?token=%s" % c.token).status in (401, 403)           # this used to keep working for up to 12 hours


# ---- R-13: login throttling, no account-existence oracle ----

def test_repeated_wrong_passwords_are_throttled():
    with Server() as srv:
        for i in range(8):
            assert srv.client().login(ADMIN_EMAIL, "wrong-%d" % i).status == 401
        locked = srv.client().login(ADMIN_EMAIL, ADMIN_PASSWORD)                       # even the right password waits out the lock
        assert locked.status == 429 and "seconds" in locked.json["error"]
        assert srv.client().login("someone.else@test.local", "x").status == 401        # a different account from the same address is judged on its own count


def test_a_successful_login_clears_the_count():
    with Server() as srv:
        for i in range(5):
            srv.client().login(ADMIN_EMAIL, "wrong-%d" % i)
        assert srv.client().login(ADMIN_EMAIL, ADMIN_PASSWORD).status == 200
        for i in range(7):
            assert srv.client().login(ADMIN_EMAIL, "wrong-again-%d" % i).status == 401


def test_a_forged_forwarded_header_cannot_dodge_the_throttle():
    with Server() as srv:
        for i in range(8):
            srv.client().request("POST", "/api/auth/login", {"email": ADMIN_EMAIL, "password": "bad%d" % i}, headers={"X-Forwarded-For": "10.0.0.%d" % i})
        r = srv.client().request("POST", "/api/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, headers={"X-Forwarded-For": "10.9.9.9"})
        assert r.status == 429                                  # the header is ignored unless the server sits behind Render's proxy


def test_behind_a_trusted_proxy_each_real_address_counts_separately():
    with Server(env={"KELP_ERP_TRUST_PROXY": "1"}) as srv:
        for i in range(8):
            srv.client().request("POST", "/api/auth/login", {"email": ADMIN_EMAIL, "password": "bad%d" % i},
                                 headers={"X-Forwarded-For": "spoofed, 203.0.113.7"})
        blocked = srv.client().request("POST", "/api/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                       headers={"X-Forwarded-For": "other, 203.0.113.7"})
        other = srv.client().request("POST", "/api/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                     headers={"X-Forwarded-For": "x, 198.51.100.4"})
        assert blocked.status == 429 and other.status == 200    # the LAST entry is the address the proxy saw


def test_unknown_and_known_accounts_take_similar_time():
    with Server() as srv:
        def average(email):
            start = time.time()
            for i in range(3):
                srv.client().login(email, "wrong-password")
            return (time.time() - start) / 3
        known, unknown = average("admin@test.local"), average("nobody@test.local")
        assert unknown > known * 0.5, "unknown email answered in %.3fs vs %.3fs for a known one" % (unknown, known)
