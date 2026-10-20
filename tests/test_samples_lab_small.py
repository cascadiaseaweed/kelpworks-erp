"""KW-33 / KW-34 / KW-35 / KW-14 / KW-27 / KW-38: the small Samples / lab changes. The retention shelf life default is 24 months (and an untouched 12 moves to 24 once);
the rest is screen behaviour, checked in the browser, with a few pins here so a later edit cannot quietly undo it."""
import pathlib

import pytest

import kelp_erp_server as K
from factories import sql
from harness import Server

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"


def months(admin):
    return admin.get("/api/samples?retention=1").json["retentionMonths"]


def test_a_new_database_keeps_retention_samples_for_24_months():
    assert [d for d in K.SETTINGS_DEFAULTS if d[0] == "sample_retention_months"][0][1] == 24
    with Server() as s:
        assert months(s.admin_client()) == 24


def test_an_untouched_12_moves_to_24_once_and_a_chosen_value_is_left_alone():
    with Server() as s:
        a = s.admin_client()
        sql(s, "UPDATE settings SET value=12 WHERE key='sample_retention_months'")
        sql(s, "DELETE FROM app_flags WHERE key='migrated_retention_months_24'")                  # as if the database predates the change
        s.restart()
        a = s.admin_client()
        assert months(a) == 24
        sql(s, "UPDATE settings SET value=18 WHERE key='sample_retention_months'")                # an administrator's own choice
        s.restart()
        assert months(s.admin_client()) == 18                                                       # the migration does not run twice
    with Server() as s:
        sql(s, "UPDATE settings SET value=18 WHERE key='sample_retention_months'")
        sql(s, "DELETE FROM app_flags WHERE key='migrated_retention_months_24'")
        s.restart()
        assert months(s.admin_client()) == 18                                                       # and a changed value was never the old default


def test_the_screen_pieces_are_in_place():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")
    assert "kelp.sampleCols.catalogue" in js and "hidden: true, cell: s => el('td', { class: 'mono' }, el('b', {}, s.idDetailed))" in js          # KW-34
    assert "!f.statuses) f.statuses = Object.keys(SAMPLE_STATUS_LABELS).filter(k => k !== 'submitted')" in js                                        # KW-35
    assert "function sampleContainerTotals" in js and "Containers to collect" in js and "function buildSampleSummaryCard" in js                        # KW-14 / KW-27
    flat = " ".join(js.split())
    assert "notesSection.box, sampleSummary.box);" in flat                              # KW-27: the summary comes after the Notes section
    assert "sampleSummary.refresh(draftId)" in js
