"""KW-70: the quick test tier, the slow marker, the parallel runs and the cache of older-release databases.

None of this changes what the full suite checks: these tests pin that the quick tier and the slow group cannot lose a test, that the selector picks what it
says it picks, that the cached older-release databases are only ever used as copies, and that CI still runs everything.
"""
import os
import re
import sqlite3
import subprocess
import sys
import time

import pytest

from harness import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import quick_tests as qt  # noqa: E402
import test_migrations as tm  # noqa: E402
import tier_health as health  # noqa: E402

pytestmark = pytest.mark.slow    # KW-70: skipped by the quick tier (-m "not slow"); CI runs it


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- the slow marker
def test_the_slow_marker_is_always_written_in_the_form_the_health_tool_edits():
    """tools/tier_health.py finds, adds and removes the marker by its exact form; a marker written any other way (a decorator, pytestmark = [..]) would be
    invisible to it and to the quick tier's report. Which files are slow is NOT listed here: the tool decides that from the timings."""
    for name in qt.list_test_files():
        text = read("tests", name + ".py")
        other_forms = re.findall(r"^(?:@pytest\.mark\.slow|pytestmark\s*=(?!\s*pytest\.mark\.slow\b)).*$", text, re.M)       # decorators, lists, other marks
        assert not other_forms, "%s marks tests slow in a form tools/tier_health.py cannot find: write `pytestmark = pytest.mark.slow` (%s)" % (name, other_forms[0])


def test_the_marker_is_registered_and_slow_plus_quick_is_the_whole_suite():
    assert re.search(r"^\s*slow:", read("pytest.ini"), re.M), "pytest.ini must register the slow marker (a typo would silently select nothing)"

    def count(*args):
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"] + list(args), cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        m = re.search(r"(\d+)(?:/\d+)? tests? (?:collected|selected)", r.stdout) or re.search(r"(\d+) tests? collected", r.stdout)
        assert m, r.stdout[-400:] + r.stderr[-400:]
        return int(m.group(1))
    everything, slow, quick = count(), count("-m", "slow"), count("-m", "not slow")
    assert slow > 0 and quick > 0
    assert slow + quick == everything, "slow %d + quick %d != all %d: a test would be lost" % (slow, quick, everything)


def test_test_ids_are_the_same_in_every_process():
    """pytest-xdist stops when two workers collect different test ids (an id built from random bytes, a timestamp, a temp path ...). Two separate collections
    must list exactly the same ids, so a parametrized test that is not deterministic is caught here and not only on a parallel run."""
    def ids():
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:xdist"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return [l for l in r.stdout.splitlines() if "::" in l]
    first = ids()
    time.sleep(2.2)                               # a zip built at collection time carries a 2-second timestamp: the second collection must be in a later tick
    second = ids()
    assert len(first) > 900
    assert first == second, "test ids differ between processes: %s" % [x for x in first if x not in set(second)][:3]


# ---------------------------------------------------------------- the quick tier selector
def test_the_core_set_names_real_fast_files():
    files = qt.list_test_files()
    for name in qt.CORE:
        assert name in files, "%s is in CORE but there is no such test file" % name
        assert name not in health.slow_files(), "%s is marked slow: the core set must stay quick" % name


def test_changed_functions_are_found_by_line_number_and_only_the_innermost():
    text = "\n".join([
        "STATUS_RULES = {1: 2}",                         # 1
        "def outer():",                                  # 2
        "    x = 1",                                     # 3
        "    def inner():",                              # 4
        "        return 2",                              # 5
        "    return inner",                              # 6
        "class Handler:",                                # 7
        "    def route_samples(self):",                  # 8
        "        pass",                                  # 9
        "def untouched():",                              # 10
        "    pass",                                      # 11
    ])
    assert qt.python_names(text, {5}) == {"inner"}
    assert qt.python_names(text, {3}) == {"outer"}
    assert qt.python_names(text, {9}) == {"route_samples"}
    assert qt.python_names(text, {1}) == {"STATUS_RULES"}
    assert qt.python_names(text, {11}) == {"untouched"}
    assert qt.python_names(text, None) >= {"inner", "route_samples", "untouched", "STATUS_RULES"}
    assert qt.python_names("def broken(:\n", {1}) == set(), "a file that does not parse selects nothing and does not crash"


def test_changed_javascript_functions_and_constants_are_found():
    js = "\n".join([
        "const CALCULATIONS = [",        # 1
        "  { key: 'a' },",               # 2
        "];",                            # 3
        "function pageSamples(v) {",     # 4
        "  return 1;",                   # 5
        "}",                             # 6
        "async function openRelease() {",  # 7
        "  await 1;",                    # 8
        "}",                             # 9
    ])
    assert qt.js_names(js, {2}) == {"CALCULATIONS"}
    assert qt.js_names(js, {5}) == {"pageSamples"}
    assert qt.js_names(js, {8}) == {"openRelease"}


def test_name_words_ignore_the_generic_ones():
    assert qt.tokens("route_samples") == {"samples"}
    assert qt.tokens("_release_from_hold") == {"release", "hold"}
    assert qt.tokens("pageSamples") == {"samples"}
    assert qt.tokens("get") == set()


def test_related_tests_are_found_by_mention_or_by_a_shared_word():
    tests = ["test_samples_lab_small", "test_release_gate", "test_sites"]
    texts = {"test_samples_lab_small": "", "test_release_gate": "def t():\n    release_gate(conn, run)\n", "test_sites": "x"}
    got = qt.related_tests({"route_samples", "release_gate"}, tests, texts)
    assert "test_samples_lab_small" in got and "samples" in got["test_samples_lab_small"]
    assert "test_release_gate" in got
    assert "test_sites" not in got
    # a word is matched by its first five letters (receive_lot -> test_receiving), and a route name is found as written in a test
    assert "test_receiving" in qt.related_tests({"_receive_lot"}, ["test_receiving", "test_sites"], {"test_receiving": "", "test_sites": ""})
    assert "test_x" in qt.related_tests({"_release_from_hold"}, ["test_x"], {"test_x": "post('/api/release/runs/1/release-from-hold')"})


def test_the_plan_always_has_the_core_and_includes_a_file_you_ask_for():
    chosen, notes, full = qt.plan(also=["sites", "test_receiving.py", "no_such_file"])
    assert set(qt.CORE) <= set(chosen)
    assert chosen["test_sites"].startswith("asked for") or chosen["test_sites"] in ("changed", "core") or "mentions" in chosen["test_sites"] or "shares" in chosen["test_sites"]
    assert "test_receiving" in chosen
    assert any("no_such_file" in n for n in notes)


def test_the_quick_tier_lists_without_running_anything():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "quick_tests.py"), "--list"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Quick tier:" in r.stdout and "test_fresh_db" in r.stdout


# ---------------------------------------------------------------- the cache of older-release databases
def test_the_cache_key_follows_the_commit_and_the_build_script(monkeypatch):
    a, b = tm.legacy_cache_path("aaaaaaa"), tm.legacy_cache_path("bbbbbbb")
    assert a != b and os.path.basename(a).startswith("aaaaaaa_")
    monkeypatch.setattr(tm, "BUILD_LEGACY", tm.BUILD_LEGACY + "\nprint('changed')\n")
    assert tm.legacy_cache_path("aaaaaaa") != a, "a changed build script must not reuse an old cached database"


def test_a_cached_database_is_only_ever_handed_out_as_a_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "LEGACY_CACHE_DIR", str(tmp_path / "cache"))
    built = tmp_path / "built.db"
    conn = sqlite3.connect(built)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.execute("INSERT INTO t VALUES (1), (2)")
    conn.commit()                                       # the rows may still be in the WAL file: the cache must hold them
    cached = tm.legacy_cache_path("cafe123")
    tm._store_legacy_cache(str(built), cached)
    conn.close()
    assert not [f for f in os.listdir(os.path.dirname(cached)) if f.endswith(".part")], "no half-written file may be left behind"
    assert sqlite3.connect(cached).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2

    work = tmp_path / "work"
    work.mkdir()
    handed = tm.build_legacy_db("cafe123", str(work))     # a cache hit: no git, no old code is run
    assert os.path.dirname(handed) == str(work) and handed != cached
    c = sqlite3.connect(handed)
    c.execute("INSERT INTO t VALUES (3)")
    c.commit()
    c.close()
    assert sqlite3.connect(cached).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2, "a test changed the cached database"


def test_the_cache_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "LEGACY_CACHE_DIR", "off")
    monkeypatch.setattr(tm.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("would build")))
    with pytest.raises(RuntimeError, match="would build"):
        tm.build_legacy_db("cafe123", str(tmp_path))


def test_the_cache_folder_is_not_committed():
    assert ".legacy_cache/" in read(".gitignore").split()


# ---------------------------------------------------------------- CI keeps running everything
def test_ci_still_runs_the_whole_suite_and_never_deselects_slow_tests():
    wf = read(".github", "workflows", "tests.yml")
    assert re.search(r"^  test:", wf, re.M), "the job must stay named `test` (the required status check)"
    assert "tests/test_fresh_db.py" in wf and "tests/test_migrations.py" in wf
    assert "--ignore=tests/test_fresh_db.py --ignore=tests/test_migrations.py" in wf, "the rest of the suite must still run"
    # "python -m pytest" is fine; a marker expression (-m "not slow", -m slow) or -k would deselect tests
    assert "not slow" not in wf and " -m slow" not in wf and not [l for l in wf.splitlines() if "pytest" in l and (" -k " in l or " -m \"" in l or " -m '" in l)], \
        "CI must not deselect any test"
    assert "fetch-depth: 0" in wf, "the upgrade checks need full history"
    assert ".legacy_cache" in wf and "actions/cache" in wf


def test_pytest_xdist_is_a_dev_dependency_only():
    assert "pytest-xdist" in read("requirements-dev.txt")
    for shipped in ("Dockerfile", "render.yaml", "Procfile", "runtime.txt"):
        assert "xdist" not in read(shipped).lower()
    assert "pytest" not in read("Dockerfile").lower().replace("requirements-dev", "")


def test_the_quick_tier_script_uses_only_the_standard_library():
    tree_imports = set(re.findall(r"^(?:import|from)\s+(\w+)", read("tools", "quick_tests.py"), re.M))
    stdlib = {"argparse", "ast", "os", "re", "subprocess", "sys"}
    assert tree_imports <= stdlib, "quick_tests.py imports %s" % sorted(tree_imports - stdlib)
