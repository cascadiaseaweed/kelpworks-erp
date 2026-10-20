"""KW-19 / KW-20 / KW-21: the dilution plan follows the filtrate TDS as it is typed, the preservative totals read as litres with the kilograms under them, and a preservative the
product does not use can be 0. The first two are screen behaviour (checked in the browser); what the server must guarantee for the third is that a 0 counts as entered and
creates no inventory line."""
import pathlib

import pytest

from factories import feedstock_detail, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def new_draft(admin):
    totes = harvest_totes(admin, 1)
    body = {"sku": "SACC-LKE", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = admin.post("/api/production/drafts", body)
    r.raise_for_status()
    return r.json["run"]["id"]


def missing(admin, rid):
    prog = admin.get("/api/production/%d/progress" % rid).json["progress"]
    return [m for s in prog["sections"] if s["key"] == "dilution" for m in s["missing"]]


def test_a_zero_preservative_counts_as_entered_and_moves_no_stock(srv, admin):
    rid = new_draft(admin)
    lot = sql(srv, "SELECT processing_lot FROM production_runs WHERE id=?", (rid,))[0][0]
    assert admin.put("/api/production/%d/stages/pasteurization" % rid, {"receivingTanks": "6A", "tank5aL": 1000, "tank6aStartL": 0}).status == 200
    assert "Ksorbate added, Tank 6A (L)" in missing(admin, rid) and "Sodium benzoate added, Tank 6A (L)" in missing(admin, rid)         # blank = still required
    r = admin.put("/api/production/%d/stages/dilution" % rid, {"ksorbateStockPct": 25, "nabenzoateStockPct": 25, "ksorbateAddedL6a": 0, "nabenzoateAddedL6a": 0,
                                                              "ksorbateAddedL": 0, "nabenzoateAddedL": 0})
    assert r.status == 200, r.text
    still = missing(admin, rid)
    assert "Ksorbate added, Tank 6A (L)" not in still and "Sodium benzoate added, Tank 6A (L)" not in still                           # a 0 is an answer
    assert sql(srv, "SELECT COUNT(*) FROM consumable_txns WHERE ref=? AND reason LIKE 'Dilution & Preservation%'", (lot,)) == [(0,)]   # and no zero-quantity inventory line
    assert sql(srv, "SELECT COALESCE(SUM(committed_kg),0) FROM run_reagent_commits WHERE run_id=?", (rid,)) == [(0,)]


def test_the_screen_pieces_are_in_place():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")
    assert "function amountTile()" in js and "zeroFillIfNotUsed(ks, getKsorbateTarget())" in js and "liveLiquidTdsPct" in js
    assert js.count("separationTds(stages)") >= 4                                      # every place the plan reads the filtrate TDS uses the live value
