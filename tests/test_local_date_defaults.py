"""A date input pre-filled with "today" must use the browser's LOCAL day, not the UTC day.

`new Date().toISOString().slice(0, 10)` is the UTC date: on a Pacific evening it is already TOMORROW, and the server refuses a received / run / reading
date after its own today ("The received date cannot be in the future"), so every Receive that kept the default date failed. The screen now builds every
such default from `localDateStr()` / `todayStr()` (public/app.js). There is no JS runtime in CI, so the screen is checked at the source and the server
rule it feeds is checked over HTTP."""
import datetime
import pathlib
import re

import pytest

from factories import approve_suppliers
from harness import Server

JS = (pathlib.Path(__file__).resolve().parent.parent / "public" / "app.js").read_text(encoding="utf-8")


def test_no_date_input_default_is_built_from_the_utc_day():
    # The only places that may format a date from toISOString are the helper itself and the month-end calculation (a pure UTC date, not "today").
    bad = [ln.strip()[:120] for ln in JS.splitlines() if re.search(r"new Date\(\)\.toISOString\(\)\.slice\(0, 10\)", ln)]
    assert not bad, "today must come from todayStr() / localDateStr(), not the UTC day: %s" % bad
    helper = re.search(r"const localDateStr = \(d = new Date\(\)\) => (.*);", JS)
    assert helper and "getTimezoneOffset()" in helper.group(1), "localDateStr must shift by the browser's time-zone offset"
    assert re.search(r"const todayStr = \(\) => localDateStr\(\);", JS)


def test_receive_stock_defaults_to_the_local_day():
    start = JS.index("async function receiveStock(c) {")
    assert "const today = todayStr();" in JS[start:start + 400]


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


def test_the_server_accepts_today_and_refuses_tomorrow_as_a_received_date(srv):
    a = srv.admin_client()
    assert a.post("/api/consumables", {"name": "Local date item", "unit": "kg", "onHand": 0}).ok
    cid = [c["id"] for c in a.get("/api/consumables").json["consumables"] if c["name"] == "Local date item"][0]
    approve_suppliers(a, cid)
    today = datetime.date.today()           # the server's local day (the harness server runs on this machine)
    ok = a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "LD-1", "supplier": "Acme", "receivedDate": today.isoformat()})
    assert ok.ok, ok.text
    late = a.post("/api/consumables/%d/receive" % cid, {"qty": 1, "supplierLot": "LD-2", "supplier": "Acme",
                                                        "receivedDate": (today + datetime.timedelta(days=1)).isoformat()})
    assert late.status == 400 and "future" in late.text, late.text
