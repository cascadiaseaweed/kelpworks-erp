"""Builders for realistic test data over the HTTP API: harvested totes, finalized production runs, released runs.

The production log has many required fields (`PROGRESS_SECTIONS` in the server); `stage_payloads()` reads that registry so the helpers keep
working when a field is added.
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import kelp_erp_server as K  # noqa: E402   (read-only: the registry of required fields)

TEXT_VALUES = {"tanks": "2A", "startedAt": "2026-05-04T10:00", "packagedAt": "2026-05-04T12:00", "sampleCollectedAt": "2026-05-04T12:00", "receivingTanks": "6A"}
MICROBIAL = {"apc": "<10", "yeast": "<10", "mold": "<10", "fecal": "<1", "salm": "Negative"}


TEST_SUPPLIERS = ("Acme", "Acme Chemicals", "DuBois", "DuBois Chemicals Canada Inc.", "Island Barrels", "S", "Tote Co", "Univar", "Univar Solutions")


def approve_suppliers(admin, cid, names=TEST_SUPPLIERS):
    """KW-23: a delivery's supplier is chosen from the item's approved list, so a test item gets the suppliers its tests receive from."""
    for n in names:
        admin.post("/api/consumables/%d/suppliers" % cid, {"name": n})


def harvest_totes(client, n=2, site="JAM", species="SL", date="2026-05-04"):
    """Check in n totes and return their ids."""
    r = client.post("/api/harvest", {"site": site, "species": species, "harvestDate": date, "toteCount": n, "totalKg": 200 * n, "location": "Cold"})
    r.raise_for_status()
    lots = r.json["created"]
    by_lot = {t["lot"]: t["id"] for t in client.get("/api/totes").json["totes"]}
    return [by_lot[l] for l in lots]


def stage_payloads():
    out = {}
    for sec in K.PROGRESS_SECTIONS:
        for entry in sec.get("fields", []):
            if isinstance(entry[1], tuple):          # "any one of these" (KW-18): fill them all in (older releases, which the upgrade tests boot, require each of them)
                for k in entry[1]:
                    out.setdefault(entry[0], {})[k] = 5
                continue
            if entry[1] == "bypassComment":          # only required when extraction is bypassed (KW-15); the helpers run a normal extraction
                continue
            out.setdefault(entry[0], {})[entry[1]] = TEXT_VALUES.get(entry[1], 5)
    # Releases older than KW-10 (the upgrade tests boot them) still require the single-tank homogenization fields; the current server accepts them and then recalculates them from the tank entries
    out["homogenization"].update({k: 5 for k in ("slurryL", "wetSolidsWtG", "liquidWtG", "dilutionWaterL", "postDilutionTankL")})
    return out


def feedstock_detail():
    return {"loadedAt": "2026-05-04T09:00", "ph": 3.7, "orp": 100, "weightKg": 200, "volumeL": 1000, "odour": "Mild", "odourIntensity": "Mild",
            "decision": "accepted", "surfacePhotoId": "x.png", "striationPhotoId": "y.png"}


def finalize_run(client, tote_ids, sku="KELPIVEX", pack=(("New 1,000 L IBC Tote", 1),), finalize=True):
    """Draft a production run on these totes, fill every required field, add packaging, and (by default) finalize it.
    Returns (run_id, finalize Response or None)."""
    details = {str(t): feedstock_detail() for t in tote_ids}
    body = {"sku": sku, "toteIds": tote_ids, "feedstockDetails": details, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    r = client.post("/api/production/drafts", body)
    r.raise_for_status()
    rid = r.json["run"]["id"]
    for stage, payload in stage_payloads().items():
        client.put("/api/production/%d/stages/%s" % (rid, stage), payload)
    for unit, qty in pack:
        client.post("/api/production/%d/packaging-entries" % rid, {"containerUnit": unit, "qty": qty})
    if not finalize:
        return rid, None
    return rid, client.post("/api/production/drafts/%d/finalize" % rid, body)


def finished_run(client, n_totes=1, **kw):
    """Harvest + finalize in one call; returns the run id (the finalize must succeed)."""
    rid, f = finalize_run(client, harvest_totes(client, n_totes), **kw)
    assert f.ok, f.text
    return rid


def lots_of(server, run_id):
    conn = sqlite3.connect(server.db_path)
    try:
        return [dict(zip(("id", "number", "qty", "status"), row)) for row in
                conn.execute("SELECT id, fg_lot_number, qty, status FROM fg_lots WHERE run_id=? ORDER BY id", (run_id,))]
    finally:
        conn.close()


def sql(server, statement, args=()):
    """Run one statement directly on the test server's database (test setup only) and return the rows."""
    conn = sqlite3.connect(server.db_path)
    try:
        rows = conn.execute(statement, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


METALS = {c: "1" for c in ("as", "cd", "cr", "co", "cu", "pb", "hg", "mo", "ni", "se", "zn")}      # ppm; judged as a loading once the application rate is set


def lab_report(client, run_id, name="lab-report.pdf"):
    """Attach a lab report document to the run; returns its attachment id."""
    import base64
    r = client.post("/api/production/%d/attachments" % run_id, {"filename": name, "contentType": "application/pdf", "dataB64": base64.b64encode(b"%PDF-1.4 test report").decode()})
    r.raise_for_status()
    return max(a["id"] for a in r.json["attachments"])


def release_ready_results(server, qm, run_id, metals=None, micro=None):
    """Everything the release gate asks for: the product application rate (so heavy metals can be judged), the lab report, and passing metals + microbial results."""
    sql(server, "UPDATE settings SET value=1000 WHERE key='coa_application_rate_kg_ha'")
    att = lab_report(qm, run_id)
    results = [{"specCode": c, "value": v} for c, v in dict(MICROBIAL, **(micro or {})).items()] + [{"specCode": c, "value": v} for c, v in dict(METALS, **(metals or {})).items()]
    r = qm.post("/api/production/%d/lab-results" % run_id, {"labName": "Test Lab", "reportNumber": "T-1", "attachmentId": att, "results": results})
    r.raise_for_status()
    return att


def release_run(server, run_id, pm, pm_password, qm, qm_password):
    """The real sign-off flow: Production Manager reviews the log, Quality Manager enters the gated lab results and releases."""
    r = pm.post("/api/release/runs/%d/review" % run_id, {"decision": "approve", "password": pm_password})
    r.raise_for_status()
    release_ready_results(server, qm, run_id)
    r = qm.post("/api/release/runs/%d/release" % run_id, {"decision": "release", "password": qm_password,
                                                                 "comment": "Test data: TDS and pH are not set to specification"})
    r.raise_for_status()
    return lots_of(server, run_id)
