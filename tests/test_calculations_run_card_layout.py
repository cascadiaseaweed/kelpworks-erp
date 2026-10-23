"""KW-69 (Calculations page settings in headed, searchable groups) and KW-65 (the run summary card's smaller KPIs in three headed columns).
Both are screen changes: the lists that drive them (SETTING_GROUPS, RUN_CARD_GROUPS in public/app.js) are checked here so nothing can be left out or placed twice, and the
server reports each setting's built-in default so the page can mark a changed value. Layout only: no setting key, value or default changes."""
import pathlib
import re

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"
JS = (PUBLIC / "app.js").read_text(encoding="utf-8")
CSS = (PUBLIC / "styles.css").read_text(encoding="utf-8")


def block(name, opener="["):
    """The text of `const NAME = [ ... ];` in app.js."""
    start = JS.index("const %s = %s" % (name, opener))
    end = JS.index("\n];", start) if opener == "[" and "\n];" in JS[start:start + 20000] else JS.index("];", start)
    return JS[start:end]


def setting_groups():
    text = block("SETTING_GROUPS")
    out = []
    for m in re.finditer(r"\{ title: '((?:[^'\\]|\\.)*)'.*?keys: \[(.*?)\] \}", text, flags=re.S):
        out.append((m.group(1), re.findall(r"'([a-z0-9_]+)'", m.group(2))))
    return out


def test_every_setting_is_in_exactly_one_group_and_none_is_left_over(admin):
    groups = setting_groups()
    placed = [k for _t, keys in groups for k in keys]
    settings = set(admin.get("/api/refdata").json["settings"])
    assert len(placed) == len(set(placed)), "a setting is in two groups: %s" % sorted({k for k in placed if placed.count(k) > 1})
    assert set(placed) == settings, "not placed: %s; not a setting: %s" % (sorted(settings - set(placed)), sorted(set(placed) - settings))
    assert len(settings) == 54 and len(groups) == 12                                                        # the list agreed for KW-69; a new setting must be placed (and this count raised)


def test_the_groups_are_in_the_agreed_order():
    assert [t for t, _k in setting_groups()] == [
        "Process defaults by section", "Equipment & capacities", "Quality limits & tolerances", "Certificate of Analysis & lab results", "Quality Control charts & reports",
        "Release & sign-off", "Samples, lots & inventory", "Run summary card display", "Documents (SOPs & safety data sheets)", "Users, sessions & uploads",
        "Files, photos & archive", "Historical run entry"]


def test_the_run_card_setting_group_holds_exactly_the_run_card_toggles():
    keys = dict(setting_groups())["Run summary card display"]
    assert keys and all(k.startswith("run_card_show_") for k in keys)


def test_each_setting_reports_its_built_in_default_and_a_changed_value_differs_from_it(admin):
    import kelp_erp_server as K
    settings = admin.get("/api/refdata").json["settings"]
    defaults = {k: v for k, v, _l, _d in K.SETTINGS_DEFAULTS}
    assert {k: s["default"] for k, s in settings.items()} == defaults
    original = settings["sds_review_years"]["value"]
    try:
        admin.put("/api/settings/sds_review_years", {"value": defaults["sds_review_years"] + 1}).raise_for_status()
        changed = admin.get("/api/refdata").json["settings"]["sds_review_years"]
        assert changed["value"] == defaults["sds_review_years"] + 1 and changed["default"] == defaults["sds_review_years"]       # the default itself never moves
    finally:
        admin.put("/api/settings/sds_review_years", {"value": original}).raise_for_status()


def test_the_calculations_page_has_search_collapsible_groups_a_changed_marker_and_a_wrapping_description():
    flat = " ".join(JS.split())
    assert "type: 'search'" in flat and "Search the settings by name, key or description" in flat                    # search by label, key or description
    assert "(s.label + ' ' + key + ' ' + (s.description || ''))" in flat
    assert "el('details', { class: 'set-group' }" in flat and "Expand all" in flat and "Collapse all" in flat      # collapsible groups
    assert "Changed from default" in flat and "changed from default" in flat                                      # the marker, on the row and in the group heading
    assert "OTHER_SETTINGS" in flat                                                                                # a setting nobody placed is shown, never lost
    assert "description" in flat and ".slice(" not in block("SETTING_GROUPS")
    assert re.search(r"\.settings-table td\.desc \{[^}]*white-space: normal", CSS)                                  # the global td rule is nowrap; the Description wraps
    assert re.search(r"\.settings-table td\.set-name \{[^}]*white-space: normal", CSS) and re.search(r"\.settings-table td\.set-val \{[^}]*white-space: normal", CSS)
    assert "@media (max-width: 760px)" in CSS and ".settings-table thead { display: none; }" in CSS                  # readable on a narrow screen
    assert "var(--" in CSS[CSS.index(".set-group {"):CSS.index(".set-group-count")] and "#fff" not in CSS[CSS.index(".set-group {"):CSS.index("tr.set-changed-row")]    # themed (dark mode): variables only


# ---- KW-65 -------------------------------------------------------------------------------------------------------------------------------------------------------------------
def run_card_kpis():
    text = JS[JS.index("function runSummaryGrid"):]
    text = text[:text.index("].filter(k => Number(settingValue('run_card_show_'")]
    return re.findall(r"^    \['([a-z_]+)', '", text, flags=re.M)


def test_every_run_card_kpi_is_placed_once_in_a_column_or_the_headline():
    kpis = run_card_kpis()
    headline = re.findall(r"'([a-z_]+)'", re.search(r"const RUN_CARD_HEADLINE = \[(.*?)\];", JS).group(1))
    groups = re.findall(r"\['([^']+)', \[([^\]]*)\]\]", block("RUN_CARD_GROUPS"))
    placed = [k for _t, keys in groups for k in re.findall(r"'([a-z_]+)'", keys)]
    assert headline == ["volume_out", "final_ph", "final_tds", "final_orp", "conv_harvest"]                          # conversion rate, harvest swapped places with extraction efficiency
    assert len(kpis) == 14 and len(set(kpis)) == 14
    assert len(placed) == len(set(placed)) and not set(placed) & set(headline)                                      # nothing twice, no headline KPI in a column
    assert set(placed) | set(headline) == set(kpis) and set(placed) == set(kpis) - set(headline)                    # nothing left over, nothing invented
    assert [t for t, _k in groups] == ["Lot details", "Input · feedstock", "Process · performance", "Output · product"]   # the lot first, then Input / Process / Output, left to right
    assert dict((t, re.findall(r"'([a-z_]+)'", k)) for t, k in groups) == {
        "Lot details": ["run_date", "operators", "farms"],
        "Input · feedstock": ["ibcs", "feedstock_weight"],
        "Process · performance": ["conv_process", "extraction_eff"],
        "Output · product": ["product", "packaged"]}


def test_the_run_card_columns_follow_the_screen_width_and_leave_no_gaps():
    flat = " ".join(JS.split())
    assert "filter(c => c[1].length)" in flat                                                                       # a column whose KPIs are all hidden is not drawn
    assert "'rs-cols n' + Math.min(columns.length, 4)" in flat and "class: 'rs-col-h'" in flat                      # headed columns
    assert re.search(r"\.rs-cols\.n4 \{ grid-template-columns: repeat\(4, minmax\(0, 1fr\)\); \}", CSS)             # 4 on a desktop
    assert re.search(r"@media \(max-width: 1100px\) \{ \.rs-cols\.n4, \.rs-cols\.n3 \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \} \}", CSS)    # 2 on a tablet in landscape
    assert re.search(r"@media \(max-width: 700px\) \{ \.rs-cols\.n4, \.rs-cols\.n3, \.rs-cols\.n2 \{ grid-template-columns: minmax\(0, 1fr\); \} \}", CSS)    # 1 on a narrow screen
    assert "rs-headline" in JS and ".rs-headline + .rs-cols" in CSS                                                  # the headline row is unchanged


def test_the_descriptions_of_the_two_swapped_kpi_settings_say_where_they_show_and_an_older_database_is_corrected_once(fresh_server):
    from factories import sql
    key_eff, key_harv = "run_card_show_extraction_eff", "run_card_show_conv_harvest"
    desc = lambda k: sql(fresh_server, "SELECT description FROM settings WHERE key=?", (k,))[0][0]
    assert "Process column" in desc(key_eff) and "big number at the top" in desc(key_harv)                          # a new database
    sql(fresh_server, "UPDATE settings SET description='1 shows extraction efficiency on a finished run''s summary card, 0 hides it (big number at the top).' WHERE key=?", (key_eff,))
    sql(fresh_server, "UPDATE settings SET description='1 shows conversion rate, harvest on a finished run''s summary card, 0 hides it (line in the details list).', value=0 WHERE key=?", (key_harv,))
    sql(fresh_server, "DELETE FROM app_flags WHERE key='migrated_run_card_swap_descriptions_kw65'")
    fresh_server.restart()
    assert "Process column" in desc(key_eff) and "big number at the top" in desc(key_harv)                          # an older database: corrected
    assert sql(fresh_server, "SELECT value FROM settings WHERE key=?", (key_harv,))[0][0] == 0                       # an administrator's value is never touched
    sql(fresh_server, "UPDATE settings SET description='edited' WHERE key=?", (key_eff,))
    fresh_server.restart()
    assert desc(key_eff) == "edited"                                                                                # once: a later start does not overwrite it


def test_the_conversion_rates_and_the_extraction_efficiency_on_the_run_card_show_one_decimal():
    flat = " ".join(JS.split())
    assert "fmt(x.processRate, 1) + ' L/kg'" in flat and "fmt(x.harvestRate, 1) + ' L/kg'" in flat and "fmt(x.extractionEff, 1) + ' %'" in flat
