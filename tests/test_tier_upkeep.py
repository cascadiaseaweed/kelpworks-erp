"""KW-70 (keeping the core set right): the cross-cutting guards in CORE, the path rules, the "a test failed that the quick tier would have missed" warning, the
JavaScript syntax check in CI, and the written workflow for updating CORE.
"""
import os
import sys

from harness import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import quick_tests as qt  # noqa: E402
import tier_health as health  # noqa: E402
import test_migrations as tm  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- CORE
def test_core_holds_the_guards_that_any_change_can_break():
    assert "test_clean_backup" in qt.CORE, "a new table must be listed in KEEP or CLEAR: schema changes are the commonest change"
    assert "test_upgrade_latest" in qt.CORE, "the quick tier needs one migration check"
    for name in ("test_fresh_db", "test_api_smoke", "test_integrity_cleanup", "test_release_gate", "test_release_integrity", "test_secrets_sessions"):
        assert name in qt.CORE


def test_the_quick_tiers_migration_check_is_the_release_that_is_live_now():
    last = [l.split("#", 1)[0].split()[0] for l in read("tests", "legacy_commits.txt").splitlines() if l.split("#", 1)[0].strip()][-1]
    assert tm.newest_legacy_commit() == last
    assert "check_upgrade(newest_legacy_commit()" in read("tests", "test_upgrade_latest.py")


def test_the_upgrade_check_is_one_function_used_by_both_the_quick_and_the_full_check():
    text = read("tests", "test_migrations.py")
    assert "def check_upgrade(commit, workdir)" in text and "check_upgrade(commit, legacy_dir)" in text


# ---------------------------------------------------------------- path rules
def plan_for(monkeypatch, paths):
    monkeypatch.setattr(qt, "changed_paths", lambda base: set(paths))
    monkeypatch.setattr(qt, "merge_base", lambda base: "HEAD")
    monkeypatch.setattr(qt, "changed_lines", lambda base, path: set())
    return qt.plan()


def test_a_changed_stylesheet_or_page_selects_the_theme_tests(monkeypatch):
    for path in ("public/styles.css", "public/index.html"):
        chosen, notes, full = plan_for(monkeypatch, [path])
        assert chosen.get("test_theme") == "path rule: %s" % path, path


def test_a_new_legacy_commit_or_the_clean_backup_tool_selects_their_tests(monkeypatch):
    chosen, _, _ = plan_for(monkeypatch, ["tests/legacy_commits.txt"])
    assert "test_upgrade_latest" in chosen
    chosen, _, _ = plan_for(monkeypatch, ["tools/make_clean_backup.py"])
    assert "test_clean_backup" in chosen


def test_a_directory_prefix_rule_matches_everything_under_it(monkeypatch):
    monkeypatch.setattr(qt, "PATH_RULES", {"docs/backlog/": ["test_export_backlog"]})
    chosen, _, _ = plan_for(monkeypatch, ["docs/backlog/items.csv"])
    assert chosen.get("test_export_backlog", "").startswith("path rule")
    chosen, _, _ = plan_for(monkeypatch, ["docs/other.md"])
    assert "test_export_backlog" not in chosen


def test_every_path_rule_names_a_real_test_file():
    files = qt.list_test_files()
    for rule, tests in qt.PATH_RULES.items():
        for t in tests:
            assert t in files, "PATH_RULES[%r] names %s, which is not a test file" % (rule, t)


# ---------------------------------------------------------------- "failed here, but the quick tier would not have run it"
FAILING = """<testsuites><testsuite name="pytest">
<testcase classname="tests.test_a_core_file" name="x" time="1"/>
<testcase classname="tests.test_missed_one" name="y" time="1"><failure message="boom">trace</failure></testcase>
<testcase classname="tests.test_missed_two" name="z" time="1"><error message="setup broke">trace</error></testcase>
<testcase classname="tests.test_chosen_one" name="w" time="1"><failure message="boom"/></testcase>
<testcase classname="tests.test_slow_one" name="v" time="1"><failure message="boom"/></testcase>
<testcase classname="tests.test_skipped_one" name="u" time="1"><skipped message="not here"/></testcase>
</testsuite></testsuites>"""


def test_failures_and_errors_are_read_but_skips_are_not(tmp_path):
    x = tmp_path / "r.xml"
    x.write_text(FAILING, encoding="utf-8")
    assert health.read_failures([str(x)]) == {"test_missed_one", "test_missed_two", "test_chosen_one", "test_slow_one"}


def test_a_failure_in_a_file_the_quick_tier_skips_is_reported_as_a_miss():
    failed = {"test_missed_one", "test_missed_two", "test_chosen_one", "test_slow_one"}
    got = health.missed_by_quick_tier(failed, chosen={"test_chosen_one": "changed"}, slow={"test_slow_one"}, full=None)
    assert [(k, f) for k, f, _ in got] == [("missed", "test_missed_one"), ("missed", "test_missed_two")]
    assert "CORE" in got[0][2] and "path rule" in got[0][2]
    assert health.missed_by_quick_tier(failed, {}, set(), full="shared test machinery changed") == [], "when the whole suite would have run, nothing was missed"
    assert health.missed_by_quick_tier(set(), {}, set(), None) == []


def test_the_report_names_a_miss_and_survives_not_knowing_the_quick_tiers_choice(tmp_path, monkeypatch, capsys):
    x = tmp_path / "r.xml"
    x.write_text(FAILING, encoding="utf-8")
    monkeypatch.setattr(health, "quick_tier_choice", lambda: ({"test_chosen_one": "changed"}, None))
    assert health.main([str(x)]) == 0, "advisory: a miss never fails the run"
    out = capsys.readouterr().out
    assert "test_missed_one failed here" in out and "test_chosen_one failed here" not in out
    monkeypatch.setattr(health, "quick_tier_choice", lambda: None)               # no git or no origin/main
    assert health.main([str(x)]) == 0
    assert "test_missed_one failed here" not in capsys.readouterr().out


def test_no_failures_means_no_attempt_to_ask_git(tmp_path, monkeypatch):
    ok = tmp_path / "ok.xml"
    ok.write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="a" time="1"/></testsuite></testsuites>', encoding="utf-8")

    def boom():
        raise AssertionError("asked for the quick tier's choice although nothing failed")
    monkeypatch.setattr(health, "quick_tier_choice", boom)
    assert health.main([str(ok)]) == 0


# ---------------------------------------------------------------- CI: JavaScript syntax
def test_ci_checks_the_syntax_of_every_javascript_file_before_the_tests_run():
    wf = read(".github", "workflows", "tests.yml")
    assert "actions/setup-node" in wf
    assert 'for f in public/*.js' in wf and 'node --check "$f"' in wf
    assert wf.index("node --check") < wf.index("Fresh database boots"), "a syntax error must fail before the (long) test steps"
    assert "npm " not in wf and "package.json" not in wf, "the app has no build step and gains no Node dependency"
    assert not os.path.exists(os.path.join(ROOT, "package.json"))


def test_the_javascript_check_covers_every_script_the_page_loads():
    html = read("public", "index.html")
    import re
    scripts = re.findall(r'<script[^>]*\ssrc="/?([^"?]+\.js)', html)
    assert scripts, "index.html loads no script?"
    for src in scripts:
        assert os.path.dirname(src) in ("", "public"), "%s is outside public/*.js: the CI check would not see it" % src


# ---------------------------------------------------------------- the written workflow
def test_the_release_guide_says_when_and_how_the_core_set_is_updated():
    guide = read("docs", "release-guide.md")
    assert "Keeping the core set right" in guide
    for phrase in ("new cross-cutting guard test", "CI fails on a test file the quick tier did not run", "over its share", "tools/quick_tests.py", "PATH_RULES"):
        assert phrase in guide, phrase
    claude = read("CLAUDE.md")
    assert "Keeping the core set right" in claude and "node --check" in claude
