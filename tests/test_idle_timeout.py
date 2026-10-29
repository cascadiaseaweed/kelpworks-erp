"""KW-08: a session idle longer than the setting `idle_timeout_minutes` (0 = off) is ended by the server (after a short grace, so the browser can save open sections first);
any request, or the activity ping, keeps it alive; the background download-token refresh does not."""
import time

import pytest

import kelp_erp_server as K
from harness import Server

pytestmark = pytest.mark.slow    # KW-70: skipped by the quick tier (-m "not slow"); CI runs it


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


def set_limit(srv, minutes):
    srv.admin_client().put("/api/settings/idle_timeout_minutes", {"value": minutes}).raise_for_status()


def test_the_setting_exists_and_defaults_to_thirty_minutes(srv):
    assert srv.admin_client().get("/api/refdata").json["settings"]["idle_timeout_minutes"]["value"] == 30
    assert srv.admin_client().post("/api/activity", {}).json["idleTimeoutMinutes"] == 30


def test_the_grace_is_a_fifth_of_the_limit_between_2_seconds_and_2_minutes():
    assert K.idle_grace_seconds(0.05) == 2.0 and K.idle_grace_seconds(1) == 12.0 and K.idle_grace_seconds(30) == 120.0 and K.idle_grace_seconds(600) == 120.0


def test_an_idle_session_is_ended_with_a_message_and_activity_keeps_it_alive(srv):
    quiet, active = srv.make_user(role="user")[0], srv.make_user(role="user")[0]
    quiet.get("/api/me").raise_for_status()
    active.get("/api/me").raise_for_status()
    set_limit(srv, 0.05)                                                      # 3 seconds (+ 2 s grace)
    try:
        for _ in range(4):
            time.sleep(1.6)
            assert active.post("/api/activity", {}).status == 200             # the ping keeps this session alive
        r = quiet.get("/api/me")                                              # ~6.4 s since its last request
        assert r.status == 401 and r.json["code"] == "idle_timeout" and "without activity" in r.json["error"]
        assert quiet.get("/api/me").status == 401                             # and it stays ended
        assert active.get("/api/me").status == 200
        fresh = srv.make_user(role="user")[0]                                 # a new sign-in starts a new session
        assert fresh.get("/api/me").status == 200
    finally:
        set_limit(srv, 30)


def test_the_download_token_refresh_does_not_count_as_activity(srv):
    c = srv.make_user(role="user")[0]
    c.get("/api/me").raise_for_status()
    set_limit(srv, 0.05)
    try:
        for _ in range(4):
            time.sleep(1.6)
            c.post("/api/auth/download-token", {})                            # what the app does every 5 minutes in the background
        assert c.get("/api/me").status == 401
    finally:
        set_limit(srv, 30)


def test_zero_turns_the_timeout_off(srv):
    c = srv.make_user(role="user")[0]
    c.get("/api/me").raise_for_status()
    set_limit(srv, 0)
    try:
        time.sleep(0.5)
        assert c.get("/api/me").status == 200 and c.post("/api/activity", {}).json["idleTimeoutMinutes"] == 0
    finally:
        set_limit(srv, 30)
