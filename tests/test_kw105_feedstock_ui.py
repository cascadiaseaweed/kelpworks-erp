"""KW-105: Feedstock Inventory and window conveniences, all in the browser code (there is no Node on the PC, so these are source checks; the behaviour is checked in the browser preview).
1 a filter box keeps focus and caret through the redraw, 2 every heading wraps, 3 the horizontal scroll bar is pinned to the bottom of the screen for every table, 4 every window has an X,
5 the Stability log window is wider, 6 the Feedstock view survives a save, 7 a Columns list closes on a press anywhere else."""
import pathlib
import re

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"
JS = " ".join((PUBLIC / "app.js").read_text(encoding="utf-8").split())
CSS = " ".join((PUBLIC / "styles.css").read_text(encoding="utf-8").split())


def block(start, length=2600):
    i = JS.index(start)
    return JS[i:i + length]


def test_1_a_filter_box_keeps_focus_and_caret_when_the_table_is_redrawn():
    draw = block("function drawStab() {", 9000)
    assert "'data-key': c.key" in draw                                                                      # the filter input can be found again after the redraw
    assert "typing.tagName === 'INPUT'" in draw and "typing.selectionStart" in draw
    assert "host.querySelector('input[data-key=\"' + keepFocus.key + '\"]')" in draw
    assert "inp.focus({ preventScroll: true })" in draw and "inp.setSelectionRange(keepFocus.from, keepFocus.to)" in draw
    # ... and the table keeps its sideways / vertical position through the redraw
    assert "wrapEl.scrollLeft = keepScroll.l; wrapEl.scrollTop = keepScroll.t;" in draw
    # the remembering happens BEFORE the host is emptied
    assert draw.index("const typing = document.activeElement") < draw.index("host.innerHTML = '';")


def test_2_every_feedstock_heading_wraps():
    assert "wrapHead" not in JS and "wraphead" not in JS and "wraphead" not in CSS                         # the old two-column opt-in is gone
    assert ".wrap-wide .tablewrap.sticky-actions thead tr:first-child th { white-space: normal;" in CSS
    # the rule that made headings nowrap is still there for other tables, which is why the Feedstock rule must be more specific
    assert "th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }" in CSS
    # data cells stay on one line (KW-97)
    assert re.search(r"th, td \{[^}]*white-space: nowrap", CSS)


def test_3_every_table_has_its_horizontal_scroll_bar_pinned_to_the_bottom_of_the_screen():
    for needle in ("function sbarSync(wrap)", "function sbarEnsure(wrap)", "function sbarSweep()", "'tablewrap-sbar'",
                   "document.querySelectorAll('.tablewrap').forEach(sbarEnsure)", "wrap.after(bar)",
                   "new MutationObserver(() => { if (SBAR.pending) return;", "requestAnimationFrame(() => { SBAR.pending = false; sbarSweep(); })"):
        assert needle in JS, needle
    assert JS.count("new MutationObserver(() => { if (SBAR.pending) return;") == 1                         # started once
    assert "/flex|grid/.test(parentDisplay)" in JS                                                          # a flex / grid parent is left alone (an extra child would change that layout)
    # the two bars stay in step both ways
    assert "bar.scrollLeft = wrap.scrollLeft" in JS and "wrap.scrollLeft = bar.scrollLeft" in JS
    # the bar is sticky to the bottom, the table's own horizontal bar is hidden, nothing prints
    assert re.search(r"\.tablewrap-sbar \{ position: sticky; bottom: 0;", CSS)
    assert ".tablewrap.has-sbar::-webkit-scrollbar { height: 0;" in CSS and ".tablewrap.has-sbar { scrollbar-width: auto; scrollbar-color: auto; }" in CSS
    assert "@media print { .tablewrap-sbar { display: none; } }" in CSS
    # theme variables only (KW-43): no hard-coded light surface for the bar
    bar_rules = " ".join(re.findall(r"\.tablewrap-sbar[^{]*\{[^}]*\}", CSS))
    assert bar_rules and not re.search(r"(background|border)[^;}]*#[0-9a-fA-F]{3,6}", bar_rules)


def test_4_every_window_has_an_x_that_does_what_its_own_dismiss_button_does():
    m = block("function modal(title, body, onSubmit", 3200)
    assert "const showX = opts.closeX !== false && !opts.noBackdropClose;" in m                              # on by default; not the first-password window, which must be answered
    assert "showX ? el('button', { type: 'button', class: 'modal-close-x'" in m and "onclick: dismiss" in m
    # Cancel where there is one; the window's own Close / Done where there is none (it may refresh the page behind it); onClose where given
    assert "const dismiss = () => { if (opts.noCancel && !opts.closeX) { submitBtn.click(); return; } close(); if (opts.onClose) opts.onClose(); };" in m
    assert "Backdrop clicks do NOT close the dialog" in m                                                   # unchanged rule
    assert "noCancel: true, noBackdropClose: true" in JS and JS.count("noBackdropClose: true") == 1       # only the forced password window
    # the camera window (not built by modal()) has one too, wired to its Cancel
    cam = block("function takePhotoFile()", 2200)
    assert "class: 'modal-close-x'" in cam and "onclick: () => cancel.click()" in cam
    # the forced dialogs (signed out, still there, re-sign-in) have none
    assert "modal-close-x" not in block("function idleShowWarning(seconds)", 900)


def test_5_the_stability_log_window_is_wider_and_its_note_wraps():
    assert "modal('Feedstock Stability log — ' + t.lot, body, async () => {}, 'Close', { noCancel: true, xwide: true });" in JS
    assert "(opts.xwide ? ' xwide' : '')" in JS
    assert ".modal.xwide { max-width: 1100px; }" in CSS
    assert "el('td', { class: 'muted note-wrap' }, r.note || '—')" in JS
    assert "td.note-wrap { white-space: normal; min-width: 220px;" in CSS


def test_6_the_feedstock_view_survives_a_save_and_starts_fresh_when_the_tab_is_chosen():
    page = block("async function pageStabilized(v) {", 3200)
    assert "const view = State.stabView || (State.stabView = {" in page and "const filters = view.filters;" in page
    assert "let sortKey = view.sortKey, sortDir = view.sortDir;" in page
    assert "view.sortKey = sortKey; view.sortDir = sortDir;" in block("function drawStab() {", 300)
    assert "if (view.pageY != null) { window.scrollTo(0, view.pageY); view.pageY = null; }" in JS            # the page itself is put back
    sel = block("function selectTab(tab) {", 400)
    assert "State.stabView = null;" in sel                                                                   # choosing the tab starts fresh
    ren = block("function render() {", 700)
    assert "State.tab === 'stabilized' && State.renderedTab === 'stabilized' && State.stabView" in ren and "State.stabView.pageY = window.scrollY" in ren
    # the column choice was already kept in this browser (and still is)
    assert "const STAB_COLS_KEY = 'kelp.stabCols';" in JS


def test_7_a_columns_list_closes_on_a_press_anywhere_else():
    assert JS.count("document.addEventListener('pointerdown', e => { document.querySelectorAll('details.col-menu[open]')") == 1      # one listener for every chooser
    assert "if (!d.contains(e.target)) d.open = false;" in JS
    assert "if (e.key === 'Escape') document.querySelectorAll('details.col-menu[open]')" in JS
    # both choosers are plain details.col-menu, so both are covered
    assert JS.count("el('details', { class: 'col-menu' }") == 2
