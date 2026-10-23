"""KW-63: Separation's Liquid Out gets the weight of the recovered liquid (kg), the calculated volume (L) = weight / rho_liquid (g/mL) from the Filtrate characterization, and an optional
estimate whose % difference from the calculation is flagged like the 6A/6B variance. The server stores the calculated numbers whenever the section is saved, so a density corrected later moves
the volume with it. The screens are pinned at the bottom."""
import pathlib

import pytest

from factories import finalize_run, harvest_totes, sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


@pytest.fixture(scope="module")
def srv():
    with Server() as s:
        yield s


@pytest.fixture(scope="module")
def admin(srv):
    return srv.admin_client()


def draft(admin):
    rid, _ = finalize_run(admin, harvest_totes(admin, 1), finalize=False)
    return rid


def put(admin, rid, **payload):
    r = admin.put("/api/production/%d/stages/separation" % rid, payload)
    r.raise_for_status()
    return r.json["run"]["stages"]["separation"]


def test_the_volume_is_the_weight_divided_by_the_density_and_is_stored(admin):
    rid = draft(admin)
    s = put(admin, rid, liquidWeightKg=102.0, liquidRhoLiquidGMl=1.02)
    assert s["liquidWeightKg"] == 102.0 and s["liquidVolumeL"] == pytest.approx(100.0)                 # 1 g/mL = 1 kg/L
    s = put(admin, rid, liquidRhoLiquidGMl=1.05)                                                       # the density is corrected later: the volume follows
    assert s["liquidVolumeL"] == pytest.approx(102.0 / 1.05)
    s = put(admin, rid, liquidWeightKg=200.0)
    assert s["liquidVolumeL"] == pytest.approx(200.0 / 1.05)


@pytest.mark.parametrize("weight,rho", [(None, 1.02), (102.0, None), (102.0, 0), (102.0, -1.0), (None, None)])
def test_without_both_a_weight_and_a_positive_density_there_is_no_volume(admin, weight, rho):
    rid = draft(admin)
    s = put(admin, rid, liquidWeightKg=weight, liquidRhoLiquidGMl=rho)
    assert "liquidVolumeL" not in s and "liquidVariancePct" not in s


def test_an_estimate_gives_a_stored_percentage_difference_from_the_calculation(admin):
    rid = draft(admin)
    s = put(admin, rid, liquidWeightKg=102.0, liquidRhoLiquidGMl=1.02, liquidEstVolumeL=80.0)
    assert s["liquidEstVolumeL"] == 80.0 and s["liquidVariancePct"] == pytest.approx((100.0 - 80.0) / 80.0 * 100)                   # (calculated - estimate) / estimate
    s = put(admin, rid, liquidEstVolumeL=125.0)
    assert s["liquidVariancePct"] == pytest.approx(-20.0)
    s = put(admin, rid, liquidEstVolumeL=None)                                                         # the estimate is optional
    assert "liquidEstVolumeL" not in s and "liquidVariancePct" not in s and s["liquidVolumeL"] == pytest.approx(100.0)
    s = put(admin, rid, liquidEstVolumeL=0)                                                            # an estimate of 0 cannot be compared with
    assert "liquidVariancePct" not in s


def test_the_flag_threshold_is_a_setting(admin):
    s = admin.get("/api/refdata").json["settings"]["separation_liquid_variance_flag_pct"]
    assert s["value"] == 5 and s["default"] == 5 and "recovered liquid" in s["description"]


def test_the_new_keys_read_well_in_an_amendment_diff():
    import kelp_erp_server as K
    for key in ("liquidWeightKg", "liquidVolumeL", "liquidEstVolumeL", "liquidVariancePct", "centrifugeUnused", "centrifugeUnusedReason", "screwUnused", "screwUnusedReason"):
        assert key in K._KEY_LABELS and K._KEY_LABELS[key]


def test_the_summary_pdf_shows_the_recovered_liquid_and_a_unit_that_was_not_used(admin):
    import kelp_erp_server as K
    totes = harvest_totes(admin, 1)
    rid, _ = finalize_run(admin, totes, finalize=False)
    put(admin, rid, liquidWeightKg=102.0, liquidRhoLiquidGMl=1.02, liquidEstVolumeL=80.0, screwUnused=True, screwUnusedReason="Belt broke")
    from test_moisture_one_required import finalize
    assert finalize(admin, rid, totes).ok
    pdf = admin.get("/api/production/%d/summary.pdf" % rid)
    assert pdf.status == 200
    text = " ".join(K.pdf_text_lines(pdf.body))
    assert "recovered liquid 102.0 kg = 100.0 L" in text and "estimate 80.0 L" in text and "screw press not used: Belt broke" in text


def test_the_screens():
    flat = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
    assert "recoveredLiquidBox" in flat and "Total weight of recovered liquid (kg)" in flat and "Estimated recovered liquid volume (L) (optional)" in flat
    assert "Total recovered liquid volume (L)" in flat and "Needs ρliquid" in flat
    assert "w / rho" in flat and "(vol - est) / est * 100" in flat                                            # the live calculation is the server's
    assert "settingValue('separation_liquid_variance_flag_pct', 5)" in flat
    assert "separation_liquid_variance_flag_pct" in flat and "'Separation: total recovered liquid volume'" in flat
