"""The quick test tier (KW-70): the checks worth running while you build and before you open a pull request. Standard library only (pytest and the optional
pytest-xdist are dev tools, see requirements-dev.txt).

    py tools/quick_tests.py                 compile, then the core tests + the test files of what you changed
    py tools/quick_tests.py --list          show what would run and why, run nothing
    py tools/quick_tests.py --also receiving --also test_sites     add test files by name
    py tools/quick_tests.py --all           the WHOLE suite, in parallel (what CI runs, faster on a many-core machine), then the timing report
    py tools/quick_tests.py --compile       only the syntax check (CI's compile step: it covers every Python file, so new modules are included)
    py tools/quick_tests.py --serial        one test at a time (the default when pytest-xdist is not installed)
    py tools/quick_tests.py --no-escalate   never switch to the whole suite by itself

What it runs, and why:
  1. A syntax check of every Python file outside tests/ (a syntax error anywhere).
  2. CORE: a small fixed set that guards the things every change can break (a fresh database boots, the API answers, sign-in and sessions, release gate and
     integrity, uploads, request limits, the integrity check, the clean-backup table lists, the live release still upgrades). PATH_RULES adds test files for
     changed files the function selector cannot read (public/styles.css and index.html -> test_theme).
  3. The test files you added or changed on this branch (against the base: the merge-base with origin/main, plus uncommitted work).
  4. Test files that look related to the code you changed: a test file that mentions a function whose lines you changed (kelp_erp_server.py, tools/, public/*.js),
     or whose file name shares a word with that function's name (route_samples -> test_samples_*).
The quick tier switches to the WHOLE suite by itself when you changed the shared test machinery (tests/harness.py, conftest.py, factories.py, pytest.ini,
requirements-dev.txt, the CI workflow) or when your change reaches more than MAX_RELATED related test files.
A whole-suite run (--all) writes the timings of every test to .test_timings/ and prints the report of tools/tier_health.py: which files should be marked `slow`,
which no longer need it, and whether the core set is over its budget (`py tools/tier_health.py --apply` then edits the markers).
The slow files (migrations from older releases, staging / restore, the records archive, the historical import ... marked `slow`) are NOT in the quick tier
unless you changed them or ask for them with --also. The quick tier does not replace CI: the full suite is the required check on every pull request, and run
locally (--all) whenever a change touches migrations, release controls or security.
"""
import argparse
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# What belongs here: a test file that guards a rule ANY change can break (not one tied to a feature: the diff selects those), fails fast and clearly, and is cheap.
# Review it when a new cross-cutting guard test appears, when CI fails on a test file the quick tier did not run for that PR (the CI report names it), or when the
# timing report says the core set is over its share (docs/release-guide.md, "Keeping the core set right"). Every file here must exist and must not be marked slow.
CORE = ["test_fresh_db", "test_api_smoke", "test_secrets_sessions", "test_release_gate", "test_release_integrity",
        "test_uploads_xss", "test_limits_logging", "test_integrity_cleanup",
        "test_clean_backup",            # a new table must be listed in KEEP or CLEAR (schema changes are the commonest kind of change)
        "test_upgrade_latest"]          # the release that is live now still upgrades (a change to the schema or to migrate())
# Changed FILES that the function-name selector cannot see (it reads .py and .js code): a path (or a prefix ending in /) -> the test files to run.
PATH_RULES = {"public/styles.css": ["test_theme"], "public/index.html": ["test_theme"],
              "tools/make_clean_backup.py": ["test_clean_backup"], "tests/legacy_commits.txt": ["test_upgrade_latest"]}
# words that say nothing about WHAT a function is about
GENERIC = {"route", "routes", "build", "make", "get", "set", "put", "post", "delete", "check", "apply", "handle", "public", "list", "create", "update",
           "add", "new", "run", "read", "write", "load", "save", "find", "from", "with", "for", "the", "and", "all", "item", "items", "data", "info", "text",
           "name", "names", "value", "values", "rows", "row", "api", "page", "send", "json", "body", "file", "files", "key", "keys", "tests", "test", "state"}
SOURCE_SUFFIXES = (".py", ".js")
MAX_RELATED = 12                    # a very broad change would select half the suite: run all of it instead
SHARED_MACHINERY = ("tests/harness.py", "tests/conftest.py", "tests/factories.py", "pytest.ini", "requirements-dev.txt", ".github/workflows/tests.yml")
TIMINGS_DIR = os.path.join(ROOT, ".test_timings")


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


# ---------------------------------------------------------------- what changed
def merge_base(base):
    for ref in ([base] if base else ["origin/main", "main"]):
        r = run(["git", "merge-base", "HEAD", ref])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def changed_paths(base_commit):
    """(paths changed on the branch or uncommitted, untracked paths). Deleted files are left out."""
    paths = set()
    if base_commit:
        paths |= set(run(["git", "diff", "--name-only", "--diff-filter=d", base_commit]).stdout.split("\n"))
    else:
        paths |= set(run(["git", "diff", "--name-only", "--diff-filter=d", "HEAD"]).stdout.split("\n"))
    untracked = set(run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.split("\n"))
    return {p.strip() for p in paths | untracked if p.strip()}


def changed_lines(base_commit, path):
    """The line numbers (in the new file) that a diff touched; every line when the file is new."""
    if base_commit and run(["git", "cat-file", "-e", "%s:%s" % (base_commit, path)]).returncode != 0:
        return None                                      # not in the base: new file, everything counts
    d = run(["git", "diff", "-U0", base_commit or "HEAD", "--", path]).stdout
    if not d:
        return None
    lines = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", d, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + max(count, 1)))
    return lines


# ---------------------------------------------------------------- which functions those lines are in
def python_names(text, lines):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    hit = [n for n in ast.walk(tree) if isinstance(n, defs) and (lines is None or any(n.lineno <= x <= n.end_lineno for x in lines))]
    # the innermost only: a class or def that merely CONTAINS a changed one is not what changed
    names = {n.name for n in hit if not any(c is not n and c.lineno >= n.lineno and c.end_lineno <= n.end_lineno for c in hit)}
    for i, l in enumerate(text.split("\n"), 1):                       # module constants (STATUS_RULES = ...)
        m = re.match(r"([A-Z][A-Z0-9_]{3,}) *=", l)
        if m and (lines is None or i in lines):
            names.add(m.group(1))
    return names


def js_names(text, lines):
    src = text.split("\n")
    names = set()
    for i, l in enumerate(src, 1):
        m = re.match(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", l) or re.match(r"(?:const|let|var)\s+([A-Z][A-Z0-9_]{3,}|[a-z][\w$]*)\s*=", l)
        if not m:
            continue
        j = i
        while j < len(src) and not re.match(r"(?:async\s+)?function\s|(?:const|let|var)\s", src[j]):   # to the next top-level declaration
            j += 1
        if lines is None or any(i <= n <= j for n in lines):
            names.add(m.group(1))
    return names


def tokens(name):
    out = set()
    for part in re.split(r"[_$]+|(?<=[a-z0-9])(?=[A-Z])", name):
        part = part.lower()
        if len(part) >= 4 and part not in GENERIC:
            out.add(part)
    return out


# ---------------------------------------------------------------- which tests
def list_test_files():
    return sorted(f[:-3] for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))


def related_tests(names, tests, texts):
    """{test file: why} for the test files that mention one of the names (as written, or as a route: release_from_hold -> release-from-hold) or share a word
    with one. Words are compared by their first five letters, so receive_lot finds test_receiving and route_samples finds test_samples_lab."""
    picked = {}
    for name in sorted(names):
        bare = name.lstrip("_")
        word = re.compile(r"\b%s\b" % re.escape(bare))
        route = bare.replace("_", "-") if "_" in bare else None
        toks = tokens(name)
        for t in tests:
            if t in picked:
                continue
            if len(name) >= 6 and (word.search(texts[t]) or (route and len(route) >= 8 and route in texts[t])):
                picked[t] = "mentions %s" % name
            else:
                shared = sorted(tok for tok in toks if tok[:5] in t)
                if shared:
                    picked[t] = "name shares '%s' with %s" % (shared[0], name)
    return picked


def plan(base=None, also=()):
    """(ordered {test file: reason}, notes, why the WHOLE suite should run instead or None)."""
    tests = list_test_files()
    texts = {t: open(os.path.join(TESTS, t + ".py"), encoding="utf-8", errors="replace").read() for t in tests}
    chosen = {t: "core" for t in CORE if t in tests}
    notes, full = [], None
    bc = merge_base(base)
    if not bc:
        notes.append("no merge-base with origin/main found: only uncommitted changes are considered")
    paths = changed_paths(bc)
    shared = sorted(p for p in paths if p in SHARED_MACHINERY)
    if shared:
        full = "shared test machinery changed (%s)" % ", ".join(shared)
    names = set()
    for p in sorted(paths):
        for rule, rule_tests in PATH_RULES.items():
            if p == rule or (rule.endswith("/") and p.startswith(rule)):
                for t in rule_tests:
                    if t in tests:
                        chosen.setdefault(t, "path rule: %s" % p)
        stem = os.path.basename(p)[:-3]
        if p.startswith("tests/") and p.endswith(".py"):
            if stem in tests:
                chosen.setdefault(stem, "changed")
            continue
        if p.endswith(SOURCE_SUFFIXES) and os.path.exists(os.path.join(ROOT, p)):
            text = open(os.path.join(ROOT, p), encoding="utf-8", errors="replace").read()
            lines = changed_lines(bc, p)
            names |= python_names(text, lines) if p.endswith(".py") else js_names(text, lines)
    related = related_tests(names, tests, texts)
    skipped = []
    for t, why in related.items():
        if t not in chosen and re.search(r"^pytestmark\s*=\s*pytest\.mark\.slow\b", texts[t], re.M):
            skipped.append(t)                                    # related, but slow: the quick tier leaves it out and says so
        else:
            chosen.setdefault(t, why)
    if skipped:
        notes.append("related slow files not run: %s (add them with --also, or run --all)" % ", ".join(skipped))
    reached = len([1 for w in chosen.values() if w not in ("core", "changed")])
    if reached > MAX_RELATED and not full:
        full = "the change reaches %d related test files (more than %d)" % (reached, MAX_RELATED)
    for a in also:
        t = a if a.startswith("test_") else "test_" + a
        t = t[:-3] if t.endswith(".py") else t
        if t in tests:
            chosen[t] = chosen.get(t, "asked for (--also)")
        else:
            notes.append("--also %s: no such test file" % a)
    if not names and not [1 for w in chosen.values() if w != "core"]:
        notes.append("nothing outside the core set matched your changes (code not under a function, or docs only)")
    return chosen, notes, full


# ---------------------------------------------------------------- running
def compile_all():
    bad = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "tests", "__pycache__", ".legacy_cache", "node_modules", ".claude", "backups", "uploads")]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(base, f)
                try:
                    with open(path, "rb") as fh:
                        compile(fh.read(), path, "exec")             # parse only: nothing is written to disk
                except (SyntaxError, ValueError) as e:
                    print("COMPILE ERROR in %s: %s" % (os.path.relpath(path, ROOT), e))
                    bad += 1
    return bad == 0


def have_xdist():
    try:
        import xdist  # noqa: F401
        return True
    except ImportError:
        return False


def workers():
    return max(2, min(os.cpu_count() or 2, 8))


def pytest_cmd(files, parallel, dist, junit=None):
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if parallel:
        cmd += ["-n", str(workers()), "--dist", dist]
    if junit:
        cmd.append("--junitxml=%s" % os.path.join(TIMINGS_DIR, junit))
    return cmd + files


def run_all(parallel):
    """The whole suite (three groups) with the timings written to .test_timings/, then the timing report. Returns the exit code of the tests."""
    if not parallel:
        print("pytest-xdist is not installed: running the whole suite one test at a time (python -m pip install -r requirements-dev.txt)")
    if not compile_all():
        return 1
    os.makedirs(TIMINGS_DIR, exist_ok=True)
    for old in os.listdir(TIMINGS_DIR):
        if old.endswith(".xml"):
            os.remove(os.path.join(TIMINGS_DIR, old))
    # the upgrade checks are independent of each other, so they spread over every worker; the rest keep each file's tests together on one worker
    first = subprocess.run(pytest_cmd(["tests/test_fresh_db.py"], False, "load", "fresh_db.xml"), cwd=ROOT).returncode
    second = subprocess.run(pytest_cmd(["tests/test_migrations.py"], parallel, "load", "migrations.xml"), cwd=ROOT).returncode
    third = subprocess.run(pytest_cmd(["--ignore=tests/test_fresh_db.py", "--ignore=tests/test_migrations.py"], parallel, "loadfile", "rest.xml"), cwd=ROOT).returncode
    print()
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tier_health.py")], cwd=ROOT)       # advisory: its warnings never change the exit code
    return first or second or third


def main(argv=None):
    ap = argparse.ArgumentParser(description="KelpWorks quick test tier (see the top of this file)")
    ap.add_argument("--base", help="compare with this ref (default: the merge-base with origin/main)")
    ap.add_argument("--also", action="append", default=[], help="add a test file by name (repeatable)")
    ap.add_argument("--list", action="store_true", help="show what would run and why; run nothing")
    ap.add_argument("--all", action="store_true", help="the whole suite, in parallel")
    ap.add_argument("--serial", action="store_true", help="do not use pytest-xdist even when it is installed")
    ap.add_argument("--compile", action="store_true", help="only the syntax check of every Python file outside tests/")
    ap.add_argument("--no-escalate", action="store_true", help="do not switch to the whole suite by itself")
    a = ap.parse_args(argv)
    parallel = have_xdist() and not a.serial
    if a.compile:
        return 0 if compile_all() else 1
    if a.all:
        return run_all(parallel)
    chosen, notes, full = plan(a.base, a.also)
    if full and not a.no_escalate and not a.list:
        print("Running the WHOLE suite instead of the quick tier: %s." % full)
        return run_all(parallel)
    if full:
        notes.append("%s: this would run the WHOLE suite (--all)" % full if not a.no_escalate else "%s: consider --all" % full)
    print("Quick tier: %d test files" % len(chosen))
    for t, why in chosen.items():
        print("  %-34s %s" % (t, why))
    for n in notes:
        print("  note: %s" % n)
    if a.list:
        return 0
    if not compile_all():
        return 1
    files = [os.path.join("tests", t + ".py") for t in chosen]
    if not parallel:
        print("(running one test at a time: pytest-xdist is not installed or --serial was given)")
    return subprocess.run(pytest_cmd(files, parallel, "loadfile"), cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
