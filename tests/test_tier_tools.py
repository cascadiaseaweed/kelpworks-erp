"""KW-70 (maintenance automation): the quick tier switches to the whole suite by itself, and tools/tier_health.py keeps the slow markers and the core set honest.

What these pin: nothing about the tiers has to be maintained by hand except the choice of CORE. Shared test machinery or a broad change selects the whole suite,
the timing report says which files to mark or unmark `slow` and edits them safely, CI compiles every Python file and reports timings without ever failing on them.
"""
import os
import sys

import pytest

from harness import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import quick_tests as qt  # noqa: E402
import tier_health as health  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- the quick tier switches to the whole suite by itself
def test_changing_the_shared_test_machinery_selects_the_whole_suite(monkeypatch):
    monkeypatch.setattr(qt, "changed_paths", lambda base: {"tests/harness.py", "kelp_erp_server.py"})
    monkeypatch.setattr(qt, "merge_base", lambda base: "HEAD")
    monkeypatch.setattr(qt, "changed_lines", lambda base, path: set())
    chosen, notes, full = qt.plan()
    assert full and "tests/harness.py" in full
    for path in ("tests/conftest.py", "tests/factories.py", "pytest.ini", "requirements-dev.txt", ".github/workflows/tests.yml"):
        assert path in qt.SHARED_MACHINERY, path


def test_a_change_that_reaches_many_test_files_selects_the_whole_suite(monkeypatch):
    monkeypatch.setattr(qt, "changed_paths", lambda base: {"tools/quick_tests.py"})          # a real file, so it can be read
    monkeypatch.setattr(qt, "merge_base", lambda base: "HEAD")
    monkeypatch.setattr(qt, "MAX_RELATED", 1)
    monkeypatch.setattr(qt, "related_tests", lambda names, tests, texts: {"test_sites": "r", "test_receiving": "r"})
    monkeypatch.setattr(qt, "python_names", lambda text, lines: {"anything"})
    monkeypatch.setattr(qt, "changed_lines", lambda base, path: None)
    chosen, notes, full = qt.plan()
    assert full and "related test files" in full


def test_list_never_runs_anything_even_when_the_whole_suite_would_be_chosen(monkeypatch, capsys):
    monkeypatch.setattr(qt, "plan", lambda base, also: ({"test_fresh_db": "core"}, [], "shared test machinery changed (tests/harness.py)"))

    def boom(parallel):
        raise AssertionError("--list started a run")
    monkeypatch.setattr(qt, "run_all", boom)
    assert qt.main(["--list"]) == 0
    assert "WHOLE suite" in capsys.readouterr().out


def test_no_escalate_keeps_the_quick_tier(monkeypatch):
    ran = []
    monkeypatch.setattr(qt, "plan", lambda base, also: ({"test_fresh_db": "core"}, [], "shared test machinery changed (tests/harness.py)"))
    monkeypatch.setattr(qt, "run_all", lambda parallel: ran.append("all") or 0)
    monkeypatch.setattr(qt, "compile_all", lambda: True)
    monkeypatch.setattr(qt.subprocess, "run", lambda cmd, cwd=None: ran.append("quick") or type("R", (), {"returncode": 0})())
    assert qt.main(["--no-escalate"]) == 0
    assert ran == ["quick"]


def test_compile_mode_checks_every_python_file_outside_tests(tmp_path, monkeypatch):
    assert qt.main(["--compile"]) == 0
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    monkeypatch.setattr(qt, "ROOT", str(tmp_path))
    assert qt.compile_all() is False


# ---------------------------------------------------------------- the timing report
JUNIT = """<testsuites><testsuite name="pytest">
<testcase classname="tests.test_big_unmarked" name="a" time="40.0"/>
<testcase classname="tests.test_core_thing" name="b" time="5.0"/>
<testcase classname="tests.test_marked_but_quick" name="c" time="2.0"/>
<testcase classname="tests.test_migrations" name="d[abc]" time="100.0"/>
<testcase classname="tests.test_migrations" name="d[def]" time="50.0"/>
<testcase classname="tests.sub.test_not_a_file" name="e" time="1.0"/>
</testsuite></testsuites>"""


def test_the_health_tool_sums_the_time_per_test_file(tmp_path):
    x = tmp_path / "a.xml"
    x.write_text(JUNIT, encoding="utf-8")
    secs, count = health.read_timings([str(x)])
    assert secs["test_migrations"] == 150.0 and count["test_migrations"] == 2
    assert secs["test_big_unmarked"] == 40.0


def test_the_health_tool_says_what_to_mark_unmark_and_trim():
    secs = {"test_big_unmarked": 40.0, "test_core_thing": 5.0, "test_marked_but_quick": 2.0, "test_migrations": 150.0, "test_core_big": 200.0}
    kinds = {(k, f) for k, f, _ in health.judge(secs, slow={"test_marked_but_quick", "test_migrations"}, core=["test_core_thing", "test_core_big"])}
    assert ("mark", "test_big_unmarked") in kinds                    # slow and not marked
    assert ("unmark", "test_marked_but_quick") in kinds              # marked, now quick
    assert ("mark", "test_migrations") not in kinds and ("unmark", "test_migrations") not in kinds      # marked and still slow: fine
    assert ("mark", "test_core_big") not in kinds                    # a core file is never marked slow by the tool
    assert ("core", "CORE") in kinds                                 # the core set is over budget (205 s)
    assert not health.judge({"test_fast": 3.0}, slow=set(), core=[])


def test_a_test_that_mentions_the_function_is_found_even_when_its_file_name_shares_no_word():
    """Only the mention can find this one: the file name has nothing in common with the function name (guards the regex that matches a whole word)."""
    texts = {"test_zzz": "def test_a():\n    zorblax_frobnicate(1)\n", "test_other": "def test_b():\n    zorblax_frobnicatexx(1)\n"}
    got = qt.related_tests({"zorblax_frobnicate"}, ["test_zzz", "test_other"], texts)
    assert got.get("test_zzz") == "mentions zorblax_frobnicate"
    assert "test_other" not in got, "a longer name that merely contains it is not a mention"


def test_the_tools_contain_no_stray_control_characters():
    """A backspace once replaced `\\b` in a regex of tools/quick_tests.py and silently broke the matching."""
    for name in ("quick_tests.py", "tier_health.py"):
        text = read("tools", name)
        bad = sorted({hex(ord(c)) for c in text if ord(c) < 32 and c not in "\n\r\t"})
        assert not bad, "%s holds control characters %s" % (name, bad)


def test_the_judgement_does_not_change_when_every_time_is_doubled():
    """A test takes about twice as long when several run at once, so the tool must judge shares of the total, never absolute seconds."""
    secs = {"test_big_unmarked": 40.0, "test_core_thing": 5.0, "test_marked_but_quick": 2.0, "test_migrations": 150.0, "test_core_big": 200.0, "test_other": 60.0}
    args = dict(slow={"test_marked_but_quick", "test_migrations"}, core=["test_core_thing", "test_core_big"])
    once = health.judge(secs, **args)
    twice = health.judge({f: t * 2 for f, t in secs.items()}, **args)
    assert once and [(k, f) for k, f, _ in once] == [(k, f) for k, f, _ in twice]


def test_a_related_slow_file_is_left_out_of_the_quick_tier_with_a_note(monkeypatch):
    monkeypatch.setattr(qt, "changed_paths", lambda base: {"tools/quick_tests.py"})
    monkeypatch.setattr(qt, "merge_base", lambda base: "HEAD")
    monkeypatch.setattr(qt, "changed_lines", lambda base, path: None)
    monkeypatch.setattr(qt, "python_names", lambda text, lines: {"restore_backup"})
    monkeypatch.setattr(qt, "related_tests", lambda names, tests, texts: {"test_staging": "mentions restore_backup", "test_sites": "name shares"})
    chosen, notes, full = qt.plan()
    assert "test_staging" not in chosen and chosen.get("test_sites") == "name shares"
    assert any("test_staging" in n and "--also" in n for n in notes)
    chosen2, _, _ = qt.plan(also=["staging"])
    assert "test_staging" in chosen2


SOURCE = '''"""doc"""
import os
import pytest
from harness import (
    A,
    B,
)

SCRIPT = """
import sys
import kelp_erp_server as k
"""


def test_x():
    pass
'''


def test_the_marker_is_added_after_the_real_imports_never_inside_a_string():
    """The first version of this change put the marker inside a triple-quoted script that has `import` lines at the start of a line."""
    out = health.insert_mark(SOURCE)
    lines = out.split("\n")
    at = lines.index(health.MARK_LINE)
    assert lines[at - 2] == ")" and lines[at - 1] == "", "the marker belongs after the last real import (the multi-line one)"
    assert lines.index('SCRIPT = """') > at and out.count(health.MARK_LINE) == 1
    compile(out, "x.py", "exec")
    assert health.insert_mark(out) == out, "adding twice changes nothing"
    assert health.remove_mark(out) == SOURCE, "removing gives the file back exactly"
    with pytest.raises(ValueError):
        health.insert_mark("import os\n")


def test_apply_edits_only_the_files_it_is_told_to_and_keeps_line_endings(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "TESTS", str(tmp_path))
    (tmp_path / "test_a.py").write_bytes(b"import pytest\r\n\r\n\r\ndef test_a():\r\n    pass\r\n")
    (tmp_path / "test_b.py").write_bytes(b"import pytest\n\n\ndef test_b():\n    pass\n")
    done = health.apply([("mark", "test_a", "m"), ("core", "CORE", "c")])
    assert done == ["test_a: marked slow"]
    a = (tmp_path / "test_a.py").read_bytes()
    assert health.MARK_LINE.encode() in a and b"\r\n" in a and b"\n" not in a.replace(b"\r\n", b"")
    assert (tmp_path / "test_b.py").read_bytes() == b"import pytest\n\n\ndef test_b():\n    pass\n"


def test_the_report_goes_to_the_ci_summary_and_never_fails_the_run(tmp_path, monkeypatch, capsys):
    x = tmp_path / "a.xml"
    x.write_text(JUNIT, encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert health.main([str(x), str(tmp_path / "missing-*.xml")]) == 0, "warnings must not fail a run, and a missing file is ignored"
    out = capsys.readouterr().out
    assert "::warning" in out and "test_big_unmarked" in out
    assert "Needs attention" in summary.read_text(encoding="utf-8")
    assert health.main([str(x), "--strict"]) == 1


def test_the_health_tool_finds_the_marker_in_the_real_test_files():
    assert "test_migrations" in health.slow_files()
    assert "test_fresh_db" not in health.slow_files()


# ---------------------------------------------------------------- CI
def test_ci_compiles_every_file_reports_timings_and_never_fails_on_them():
    wf = read(".github", "workflows", "tests.yml")
    assert "tools/quick_tests.py --compile" in wf, "the compile step must cover every Python file, so a split into modules is included"
    assert "py_compile kelp_erp_server.py" not in wf
    for xml in ("fresh_db.xml", "migrations.xml", "rest.xml"):
        assert "--junitxml=.test_timings/" + xml in wf
    step = wf[wf.index("Test timing report"):]
    assert "if: always()" in step and "--strict" not in step, "the timing report is advisory"
    assert ".test_timings/" in read(".gitignore").split()


def test_tier_health_uses_only_the_standard_library():
    import re
    imports = set(re.findall(r"^(?:import|from)\s+(\w+)", read("tools", "tier_health.py"), re.M))
    assert imports <= {"ast", "glob", "os", "re", "sys", "xml"}, sorted(imports)
