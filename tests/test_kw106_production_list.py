"""KW-106: the Production Runs page is easier to navigate. All in the browser code (no Node on the PC, so these are source checks; the behaviour is checked in the browser preview):
1 an In progress / Finalized switch with counts, 2 a search / filter bar with five attention chips, 3 compact rows that open into the full card, 4 a collapsible notes box,
5 a More menu for the less used buttons, 6 month headings and paging (20 at a time). Nothing on the server changed."""
import pathlib
import re

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"
JS = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
CSS = " ".join((PUBLIC / "styles.css").read_text(encoding="utf-8").split())


def block(start, length=2600):
    i = JS.index(start)
    return JS[i:i + length]


def test_1_the_switch_separates_in_progress_from_finalized_and_shows_both_counts():
    page = block("async function pageProduction(v) {", 3800)
    assert "tab('drafts', 'In progress', dr.drafts.length)" in page and "tab('final', 'Finalized', r.runs.length)" in page
    assert "const now = view.tab || (dr.drafts.length ? 'drafts' : 'final');" in page                       # In progress when anything is in progress, else Finalized
    assert "for (const d of dr.drafts) pane.append(draftCard(d, lossSum));" in page                         # the in-progress cards are the same cards as before
    assert "} else drawFinalizedRuns(pane, r.runs, lossSum, view);" in page
    assert "No runs in progress." in page
    # the draft card itself is untouched (Resume, Report a loss, Print sample labels, Discard)
    card = block("function draftCard(d, lossSum) {", 1500)
    for label in ("'Resume'", "'Report a loss'", "'Print sample labels'", "'Discard'"):
        assert label in card
    assert ".prod-switch { display: inline-flex;" in CSS and ".prod-switch .prod-tab.active" in CSS


def test_the_view_is_kept_across_a_save_and_starts_fresh_when_the_tab_is_chosen():
    page = block("async function pageProduction(v) {", 3800)
    assert "const view = State.prodView || (State.prodView = prodViewFresh());" in page
    assert "if (view.pageY != null) { window.scrollTo(0, view.pageY); view.pageY = null; }" in page
    assert "const prodViewFresh = () => ({ tab: null, q: '', sku: '', from: '', to: '', species: '', farm: '', chips: [], open: new Set(), shown: PROD_PAGE, pageY: null });" in JS
    assert "State.prodView = null;" in block("function selectTab(tab) {", 500)
    ren = block("function render() {", 900)
    assert "State.tab === 'production' && State.renderedTab === 'production' && State.prodView" in ren and "State.prodView.pageY = window.scrollY" in ren


def test_2_the_filter_bar_has_the_fields_and_five_chips_and_filters_before_paging():
    for needle in ("'Search lot, product, operators, farm…'", "sel('sku', 'All products'", "sel('farm', 'All farms'", "sel('species', 'All species'",
                   "dateBox('from', 'Run date from')", "dateBox('to', 'Run date to')", "'Clear filters'"):
        assert needle in JS, needle
    chips = block("const PROD_CHIPS = [", 1200)
    for key in ("'review', 'Awaiting review'", "'amend', 'Under amendment'", "'hold', 'On hold'", "'loss', 'Has a loss report'", "'notes', 'Has notes'"):
        assert key in chips, key
    m = block("function prodMatches(run, v, sum) {", 1500)
    assert "PROD_CHIPS.some(([k, , test]) => v.chips.includes(k) && test(run, sum))" in m                    # ticked chips are "any of these"
    assert "q.split(/\\s+/).every(w => hay.includes(w))" in m                                               # every search word must be found
    draw = block("function drawList() {", 900)
    assert draw.index("runs.filter(run => prodMatches(run, view, lossSum))") < draw.index("hits.slice(0, view.shown)")   # all runs are searched, then the page is cut
    # the number on a chip counts what is listed now (every filter and ticked chip applied, all pages of the list)
    counts = block("function updateChipCounts() {", 500)
    assert "runs.filter(r => prodMatches(r, view, lossSum))" in counts and "chips: []" not in counts and "b._label + ' (' + base.filter(r => b._test(r, lossSum)).length + ')'" in counts
    assert "drawList(); updateChipCounts();" in JS and JS.count("updateChipCounts();") >= 2          # redrawn after every filter change and when the bar is built
    # the bar is built once (typing redraws the list only), so the search box keeps focus
    assert "q.addEventListener('input', () => { view.q = q.value; onFilter(); });" in JS
    assert "const onFilter = () => { view.shown = PROD_PAGE; drawList();" in JS
    assert JS.count("buildBar();") >= 2 and "bar.replaceChildren(q," in JS


def test_3_compact_rows_open_into_the_full_card_and_stay_open_after_a_save():
    row = block("function runRow(run, lossSum, view) {", 3300)
    assert "el('details', { class: 'run-row' }" in row
    for needle in ("run.runDate", "skuName(run.sku)", "fmt(x.out, 0) + ' L'", "'pH '", "'TDS '", "releaseBadge(run.release.state)", "lossBadge(lossSum, run.id, run.processingLot)", "presenceBadge('run:' + run.id)",
                   "'Excluded'", "'Historical'"):
        assert needle in row, needle
    assert "const build = () => { if (!body.firstChild) body.append(runCard(run, lossSum)); };" in row         # the card is built the first time the row is opened
    assert "if (view.open.has(run.id)) { det.open = true; build(); }" in row and "view.open.add(run.id)" in row and "view.open.delete(run.id)" in row
    assert "e.preventDefault()" in row                                                                      # a click on a badge's own action does not also open / close the row
    # Compact is the default; Detailed (every run as a full card, as before) is a per-browser choice
    assert "const PROD_DETAIL_KEY = 'kelp.prodDetail';" in JS and "detailed ? runCard(run, lossSum) : runRow(run, lossSum, view)" in JS
    assert ".run-row > summary {" in CSS and ".rr-body > .card {" in CSS


def test_4_the_notes_box_is_collapsible_with_a_preview_and_long_notes_are_clamped():
    n = block("function runNotesPanel(run, events, opts = {}) {", 2300)
    assert "return el('details', { class: 'notes-panel', ...(opts.open === true || run.amendment ? { open: '' } : {}) }," in n
    assert "'Notes on this run (' + items.length + ')'" in n and "class: 'muted np-preview'" in n
    assert "n.text.split('\\n').length > 3 || n.text.length > 280" in n and "'Show more'" in n and "'Show less'" in n
    assert ".np-text.clamped { display: -webkit-box; -webkit-line-clamp: 3;" in CSS and ".notes-panel[open] .np-preview { display: none; }" in CSS
    # the Product Release window (already folded) keeps its notes open; the run card uses the default (closed)
    assert "runNotesPanel(d.run, d.events, { emptyText: 'No notes were written on this run.', open: true })" in JS
    assert "runNotesPanel(run)," in block("function runCard(run, lossSum) {", 4200)


def test_5_the_card_keeps_three_buttons_and_the_rest_are_under_a_more_menu():
    card = block("function runCard(run, lossSum) {", 4200)
    visible = card[:card.index("moreMenu([")]
    for label in ("'📋 Process log'", "'🧪 QC'", "'📎 Documents'"):
        assert label in visible
    menu = card[card.index("moreMenu(["):card.index("run.amendment ? amendBanner(run)")]
    for label in ("'Report a loss'", "'✏️ Continue amendment'", "'✏️ Amend run'", "'🧫 Lab results'", "'Print FG labels'", "'Print sample labels'"):
        assert label in menu, label
    assert "canAmendLog() ?" in menu                                                                        # Amend is still only for the people who may
    for gone in ("'🧫 Lab results'", "'Print FG labels'", "'Print sample labels'"):
        assert gone not in visible
    # the menu is a details.col-menu, so the KW-105 outside-click closer applies to it
    more = block("function moreMenu(items) {", 700)
    assert "class: 'col-menu more-menu'" in more and "d.open = false; fn();" in more
    assert ".more-menu { margin-left: 0; }" in CSS


def test_6_the_finalized_list_has_month_headings_and_pages_of_twenty():
    assert "const PROD_PAGE = 20;" in JS
    draw = block("function drawList() {", 1500)
    assert "prodMonthLabel(ym)" in draw and "class: 'month-head'" in draw
    assert "'Show older runs (' + (hits.length - shown.length) + ' more)'" in draw and "view.shown += PROD_PAGE; drawList();" in draw
    assert "toLocaleString('en-CA', { month: 'long', year: 'numeric' })" in JS and "'No run date'" in JS


def test_the_new_css_uses_theme_variables_only():
    new = CSS[CSS.index("KW-106: the Production Runs page"):CSS.index("@media (max-width: 600px) { .rr-lot")]
    assert not re.search(r"(background|border)[^;}]*#[0-9a-fA-F]{3,6}", new.replace("color: #fff", ""))     # white text on the active tab is the only literal colour
