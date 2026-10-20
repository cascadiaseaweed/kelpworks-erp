"""KW-43: light / dark theme chosen per user. KW-12 (the SOP icon) is front-end only; the two checks at the bottom keep the stylesheet and the page in step."""
import pathlib
import re

import pytest

from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


def test_everyone_starts_on_the_light_theme_and_each_person_keeps_their_own_choice(srv):
    a, _ = srv.make_user(role="user")
    b, _ = srv.make_user(role="user")
    assert a.get("/api/me").json["theme"] == "light" and b.get("/api/me").json["theme"] == "light"
    r = a.put("/api/me/theme", {"theme": "dark"})
    assert r.status == 200 and r.json["theme"] == "dark"
    assert a.get("/api/me").json["theme"] == "dark"
    assert b.get("/api/me").json["theme"] == "light"                               # another person is not affected
    assert srv.admin_client().get("/api/me").json["theme"] == "light"
    assert a.put("/api/me/theme", {"theme": "light"}).json["theme"] == "light"


def test_only_light_or_dark_is_accepted(srv):
    a, _ = srv.make_user(role="user")
    for bad in ("blue", "", None, 1):
        assert a.put("/api/me/theme", {"theme": bad}).status == 400
    assert a.put("/api/me/theme", {}).status == 400
    assert a.get("/api/me").json["theme"] == "light"


def test_the_choice_survives_signing_in_again_and_a_restart(srv):
    a, info = srv.make_user(role="user")
    a.put("/api/me/theme", {"theme": "dark"}).raise_for_status()
    srv.restart()
    again = srv.client()
    r = again.login(info["email"], info["password"])
    assert r.status == 200 and r.json["user"]["theme"] == "dark"                  # the sign-in answer already carries it
    assert again.get("/api/me").json["theme"] == "dark"


def test_every_stylesheet_colour_variable_has_a_dark_value():
    css = (PUBLIC / "styles.css").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--(?:bg|bd|fg)-[0-9a-f]{6})\)", css))
    root = css.split("html[data-theme=\"dark\"]", 1)
    light = set(re.findall(r"^\s*(--(?:bg|bd|fg)-[0-9a-f]{6}):", root[0], re.M))
    dark = set(re.findall(r"^\s*(--(?:bg|bd|fg)-[0-9a-f]{6}):", root[1], re.M))
    assert used and not used - light, "a colour variable has no light value: %s" % sorted(used - light)
    assert not light - dark, "a colour variable has no dark value: %s" % sorted(light - dark)


def test_the_page_has_the_theme_button_and_the_script_applies_it():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")
    assert 'id="themeToggle"' in html
    assert "applyTheme(State.user.theme)" in js and "'/me/theme'" in js
