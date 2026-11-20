"""KW-104: seven small changes to the production log.

1  Separation: a blank "Total weight of recovered liquid (kg)" starts at 0 (newer rules only)      -> source checks (tests/test_separation_liquid.py has the rest of it)
2  the volume line says where the density is                                                        -> source checks (there)
3  the optional estimate / variance check and its setting are gone                                  -> tests/test_separation_liquid.py
4  Separation's Filtrate characterization starts from Extraction's QC Check, never TSliquid         -> source checks (the browser check proves the behaviour)
5  Notes has a Save button that Save & close / Finalize / the idle timeout press                    -> source checks
6  Notes pictures are added with Upload photo / Take photo buttons                                  -> source checks
7  the tote card's button commits the tote to the run (WIP) even with no Decision yet               -> API tests: a tote left In stock by a refused Save was the bug
"""
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
    r = admin.post("/api/production/drafts", {"sku": "KELPIVEX", "toteIds": [], "feedstockDetails": {}, "runDate": "2026-05-04", "operators": "NW", "location": "Cold"})
    r.raise_for_status()
    return r.json["run"]["id"]


def card(**extra):
    """What the card posts: the usual entries, no pictures, and no decision unless one is given."""
    body = {k: v for k, v in feedstock_detail().items() if k not in ("decision", "surfacePhotoId", "striationPhotoId")}
    body.update(extra)
    return body


def status_of(admin, tote_id):
    return next(t for t in admin.get("/api/totes").json["totes"] if t["id"] == tote_id)


# ---- 7: Commit tote to run ------------------------------------------------------------------------------------------------------------------------------

def test_committing_a_tote_with_no_decision_locks_it_to_the_run_like_save_and_close(srv, admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert status_of(admin, t)["status"] == "in_stock"
    r = admin.post("/api/production/%d/feedstock/%d/save" % (rid, t), card())
    assert r.status == 200 and r.json["rejected"] is False                         # before KW-104: 400 "A decision (accepted/rejected) is required", the tote stayed In stock
    tote = status_of(admin, t)
    assert tote["status"] == "wip" and tote["runId"] == rid
    assert sql(srv, "SELECT old_value,new_value FROM tote_stability_log WHERE tote_lot_id=? AND field='Status'", (t,)) == [("in_stock", "wip")]
    assert sql(srv, "SELECT decision_set FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (rid, t)) == [(0,)]                 # no decision was chosen: Finalize still asks for one
    r = admin.post("/api/production/%d/feedstock/%d/save" % (rid, t), card(decision="accepted"))
    assert r.status == 200
    assert sql(srv, "SELECT decision_set FROM run_inputs WHERE run_id=? AND tote_lot_id=?", (rid, t)) == [(1,)]                 # an explicit choice is recorded as before
    assert status_of(admin, t)["status"] == "wip"


def test_a_decision_that_is_neither_accepted_nor_rejected_is_refused(admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    assert admin.post("/api/production/%d/feedstock/%d/save" % (rid, t), card(decision="maybe")).status == 400
    assert status_of(admin, t)["status"] == "in_stock"


def test_a_rejected_tote_still_goes_to_qaqc_hold(admin):
    t, = harvest_totes(admin, 1)
    rid = new_draft(admin)
    r = admin.post("/api/production/%d/feedstock/%d/save" % (rid, t), card(decision="rejected", rejectionReason="Slimy"))
    assert r.status == 200 and r.json["rejected"] is True
    assert status_of(admin, t)["status"] == "hold"


# ---- the screens ----------------------------------------------------------------------------------------------------------------------------------------

def js():
    return " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())


def between(flat, start, stop):
    a = flat.index(start)
    return flat[a:flat.index(stop, a)]


def test_the_tote_card_button_says_what_it_does_and_the_run_window_says_so_plainly():
    flat = js()
    assert "opts.commitToRun ? 'Commit tote to run' : 'Save'" in flat                              # only the production run's card; Pre-Processing and Feedstock Inventory keep Save
    assert flat.count("commitToRun: true") == 1
    assert "'Tote ' + t.lot + ' is now WIP and locked to this run.'" in flat
    assert "status.className = opts.commitToRun ? 'error' : 'help'" in flat                        # a refusal is visible, not the small grey help line


def test_notes_has_a_save_button_that_every_save_and_close_presses():
    notes = between(js(), "function buildNotesSection", "const SAMPLE_TYPES")
    assert "class: 'secondary section-save' }, n ? 'Save note' : 'Save')" in notes
    assert "'Add note'" not in notes
    assert "msg.textContent = 'Saved.'" in notes                                                   # pressSectionSaves reads the next sibling's text: 'Saved.' or the reason
    assert "save, msg" in notes                                                                    # ... so the message element sits right after the button
    assert "if (!n && !txt.value.trim() && !staged.length)" in notes                               # nothing typed: nothing saved, and the run is not created for it
    assert "pendingId" in notes                                                                    # a retry after a failed picture adds to the note that was made, not a second one


def test_notes_pictures_use_the_upload_and_take_photo_buttons():
    notes = between(js(), "function buildNotesSection", "const SAMPLE_TYPES")
    assert "'Upload photo'" in notes and "'📷 Take photo'" in notes and "takePhotoFile()" in notes
    assert "style: 'display:none'" in notes                                                        # the file choosers are behind the buttons, not shown
    assert "field(n ? 'Add pictures' : 'Pictures (optional)', el('div', {}, photoRow, thumbs))" in notes
    assert "prepareUpload(f0)" in notes                                                            # the same shrinking as every other picture


def test_separation_filtrate_characterization_starts_from_extraction_but_never_tsliquid():
    flat = js()
    assert "const PREFILL_KEYS = ['qcPh', 'tdsPct', 'brixPct', 'mannitolPct', 'rhoLiquidGMl'];" in flat      # the five readings; TSliquid is not among them
    assert "liquidQc.fill(src.values, PREFILL_KEYS)" in flat
    fill = between(flat, "function fill(source, keys)", "return { box, getPayload, fill };")
    assert "edited.has(k)" in fill and "inp.value.trim() !== ''" in fill                           # a typed, cleared or saved value is never overwritten
    sep = between(flat, "function separationQcSource", "function buildSeparationSection")
    assert "ex && !ex.bypassed" in sep and "Homogenization (extraction was bypassed)" in sep        # a bypassed run takes the Homogenization lot characterization (KW-16)
    assert flat.count("() => separationQcSource(extractionSection, homogenizationSection)") == 2    # the run in progress and the amended run
    assert flat.count("() => separationSectionRef?.kwPrefill()") == 4                               # Extraction and Homogenization tell it when they are saved (both windows)
    assert "sectionEl.addEventListener('toggle', () => { if (sectionEl.open) prefillFromExtraction(); });" in flat
