"""KW-63: Separation's Liquid Out gets the weight of the recovered liquid (kg) and the calculated volume (L) = weight / rho_liquid (g/mL) from the Filtrate characterization. The server stores
the calculated numbers whenever the section is saved, so a density corrected later moves the volume with it. KW-104 took the optional estimate (and its % difference check) off the screen and
dropped its flag setting; the server still reads and keeps an estimate that an earlier run stored (the tests below pin that), and a blank weight starts at 0. The screens are pinned at the bottom."""
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


def test_an_estimate_stored_by_an_earlier_run_still_gives_its_stored_percentage_difference(admin):
    rid = draft(admin)
    s = put(admin, rid, liquidWeightKg=102.0, liquidRhoLiquidGMl=1.02, liquidEstVolumeL=80.0)
    assert s["liquidEstVolumeL"] == 80.0 and s["liquidVariancePct"] == pytest.approx((100.0 - 80.0) / 80.0 * 100)                   # (calculated - estimate) / estimate
    s = put(admin, rid, liquidEstVolumeL=125.0)
    assert s["liquidVariancePct"] == pytest.approx(-20.0)
    s = put(admin, rid, liquidEstVolumeL=None)                                                         # the estimate is optional
    assert "liquidEstVolumeL" not in s and "liquidVariancePct" not in s and s["liquidVolumeL"] == pytest.approx(100.0)
    s = put(admin, rid, liquidEstVolumeL=0)                                                            # an estimate of 0 cannot be compared with
    assert "liquidVariancePct" not in s


def test_the_flag_setting_is_gone_and_its_leftover_row_is_dropped_once(admin):
    import kelp_erp_server as K
    assert "separation_liquid_variance_flag_pct" not in {k for k, _v, _l, _d in K.SETTINGS_DEFAULTS}
    assert "separation_liquid_variance_flag_pct" not in admin.get("/api/refdata").json["settings"]
    # a database from before this change still has the row: the first start with this code drops it, once, and says so in the change log (a server of its own: it is restarted)
    with Server() as own:
        sql(own, "INSERT INTO settings (key,value,label,description,updated_at) VALUES ('separation_liquid_variance_flag_pct',5,'x','x','2026-01-01T00:00:00Z')")
        sql(own, "DELETE FROM app_flags WHERE key='migrated_remove_separation_variance_setting_kw104'")
        own.restart()
        assert "separation_liquid_variance_flag_pct" not in own.admin_client().get("/api/refdata").json["settings"]
        rows = sql(own, "SELECT field,new_value,changed_by FROM config_log WHERE key='separation_liquid_variance_flag_pct'")
        assert [tuple(r) for r in rows] == [("Setting removed (no longer used)", "removed", "System")]


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
    assert "recoveredLiquidBox" in flat and "Total weight of recovered liquid (kg)" in flat
    assert "Total recovered liquid volume (L)" in flat and "w / rho" in flat                                    # the live calculation is the server's
    # KW-104: the message names where the density is; the estimate, its difference line and its setting are gone from the screen and the Calculations page
    assert "Needs density of liquid (ρliquid), found in QC Check – Filtrate characterization" in flat
    assert "Needs ρliquid" not in flat and "enter it in the Filtrate characterization above" not in flat
    assert "Estimated recovered liquid volume" not in flat and "Calculated vs estimate" not in flat and "liquidEstInp" not in flat
    assert "(vol - est) / est * 100" not in flat and "separation_liquid_variance_flag_pct" not in flat
    assert "'Separation: total recovered liquid volume'" in flat and "recovered liquid volume vs estimate" not in flat
    # KW-104: a blank weight starts at 0 -- only for a run held to the newer rules, and a saved weight is never replaced
    assert "if (values.liquidWeightKg != null) liquidWtInp.value = formatQcValue(values.liquidWeightKg, 2); else if (v2) liquidWtInp.value = '0';" in flat
