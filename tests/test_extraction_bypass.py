"""KW-15 / KW-16: a run can bypass extraction (reason required, default 'Extraction not optimized'); its extraction fields are not required, and its Extraction
Performance values are the Homogenization-out QC check wherever they are used (summary, Yield & Usage); extraction efficiency is not applicable and the QC charts take no
extraction point from it."""
import kelp_erp_server as K
import pytest

from factories import feedstock_detail, finalize_run, harvest_totes
from harness import Server


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def draft(admin, totes=None):
    totes = totes or harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    return rid, totes


def extraction(admin, rid, **body):
    r = admin.put("/api/production/%d/stages/extraction" % rid, body)
    assert r.status == 200, r.text
    return r


def stages(admin, rid):
    return [d for d in admin.get("/api/production/drafts").json["drafts"] if d["id"] == rid][0]["stages"]


def missing(admin, rid):
    prog = admin.get("/api/production/%d/progress" % rid).json["progress"]
    return [m for s in prog["sections"] if s["key"] == "extraction" for m in s["missing"]]


def finalize(admin, rid, totes):
    body = {"sku": "KELPIVEX", "toteIds": totes, "feedstockDetails": {str(t): feedstock_detail() for t in totes}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"}
    return admin.post("/api/production/drafts/%d/finalize" % rid, body)


def test_bypassing_extraction_clears_its_readings_and_defaults_the_reason(admin):
    rid, _ = draft(admin)
    assert stages(admin, rid)["extraction"]["amplitudePct"] == 5                           # the helper filled the normal extraction fields
    extraction(admin, rid, bypassed=True)
    ex = stages(admin, rid)["extraction"]
    assert ex["bypassed"] is True and ex["bypassComment"] == K.EXTRACTION_BYPASS_DEFAULT == "Extraction not optimized"
    assert ex.get("amplitudePct") is None and ex.get("tdsPct") is None and ex.get("startedAt") is None


def test_the_reason_is_editable_but_never_blank(admin):
    rid, _ = draft(admin)
    extraction(admin, rid, bypassed=True, bypassComment="Flow cell only, nothing to tune")
    assert stages(admin, rid)["extraction"]["bypassComment"] == "Flow cell only, nothing to tune"
    assert admin.put("/api/production/%d/stages/extraction" % rid, {"bypassed": True, "bypassComment": "   "}).status == 400
    assert stages(admin, rid)["extraction"]["bypassComment"] == "Flow cell only, nothing to tune"


def test_extraction_fields_are_not_required_when_bypassed_and_the_run_finalizes(admin):
    rid, totes = draft(admin)
    extraction(admin, rid, bypassed=True)
    assert missing(admin, rid) == []
    f = finalize(admin, rid, totes)
    assert f.ok, f.text
    run = [r for r in admin.get("/api/production").json["runs"] if r["id"] == rid][0]
    assert run["stages"]["extraction"]["bypassed"] is True and run["stages"]["extraction"]["bypassComment"] == "Extraction not optimized"


def test_a_run_that_does_not_bypass_still_needs_every_extraction_field(admin):
    rid, _ = draft(admin)
    extraction(admin, rid, amplitudePct=None, tdsPct=None)
    assert sorted(missing(admin, rid)) == ["Amplitude (%)", "QC Check: TDS (%)"]
    extraction(admin, rid, bypassed=True)
    extraction(admin, rid, bypassed=False)                                                  # un-bypassing: no reason is kept, and the readings are needed again
    ex = stages(admin, rid)["extraction"]
    assert "bypassed" not in ex and "bypassComment" not in ex
    assert "Amplitude (%)" in missing(admin, rid) and "Reason extraction was bypassed" not in missing(admin, rid)


def test_the_bypass_reason_is_required_even_if_the_flag_is_set_without_one(admin):
    rid, _ = draft(admin)
    extraction(admin, rid, bypassed=True)
    assert "Reason extraction was bypassed" not in missing(admin, rid)                      # the default was filled in


def test_a_bypassed_run_uses_the_homogenization_values_and_has_no_efficiency(srv, admin):
    plain, plain_totes = draft(admin)
    assert finalize(admin, plain, plain_totes).ok
    byp, byp_totes = draft(admin)
    extraction(admin, byp, bypassed=True)
    assert finalize(admin, byp, byp_totes).ok
    rows = {r["id"]: r for r in admin.get("/api/yield-usage").json["runs"]}
    assert rows[plain]["extractionBypassed"] is False and rows[plain]["extractionEfficiency"] is not None
    b = rows[byp]
    assert b["extractionBypassed"] is True and b["extractionEfficiency"] is None            # not applicable, never 0 %
    assert b["tdsAfterExtraction"] == b["tdsBeforeExtraction"] == 5                         # relinked to the Homogenization-out TDS
    # the QC charts take an extraction-efficiency point from the normal run only
    qc = admin.get("/api/qc-charts").json
    pts = [p["r"] for m in qc["measures"] if m["key"] == "qc:extraction_eff" for s in m["sections"] for p in s["points"]]
    assert plain in pts and byp not in pts


def test_the_production_summary_says_extraction_was_bypassed(srv, admin):
    rid, totes = draft(admin)
    extraction(admin, rid, bypassed=True, bypassComment="Product went straight through the flow cell")
    assert finalize(admin, rid, totes).ok
    pdf = admin.get("/api/production/%d/summary.pdf" % rid)
    assert pdf.status == 200
    text = " ".join(" ".join(str(x) for x in (l if isinstance(l, (list, tuple)) else [l])) for l in K.pdf_text_lines(pdf.body))
    assert "Bypassed: Product went straight through the flow cell" in text, text[:1500]
