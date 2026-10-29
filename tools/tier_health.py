"""Keeps the test tiers healthy (KW-70). Standard library only. Reads the JUnit XML files a full test run writes and says what needs attention:

    py tools/tier_health.py                    read .test_timings/*.xml (written by `py tools/quick_tests.py --all`)
    py tools/tier_health.py a.xml b.xml        read these files (CI passes the ones its pytest steps wrote)
    py tools/tier_health.py --apply            ALSO mark / unmark `slow` files as suggested (edits tests/*.py; review the diff)

It reports, per test file, the time the tests took (the sum over the tests) and its SHARE of the whole suite. Shares, not seconds, are what it judges: a test
takes about twice as long when several run at once (CPU contention), so absolute seconds from a parallel run (CI, `--all`) are not comparable with a serial one,
but a file's share of the total is. It warns when
  * a file that is NOT marked slow has more than SLOW_FILE_SHARE of the suite's time (and at least MIN_SLOW_SECONDS): it makes the quick tier slow -> mark it `slow`;
  * a file marked slow has less than FAST_AGAIN_SHARE: it no longer needs to be skipped -> unmark it;
  * the CORE set (tools/quick_tests.py) has more than CORE_BUDGET_SHARE of the suite's time: the quick tier is growing out of proportion -> trim CORE or speed those tests up;
  * a test FAILED in this run in a file the quick tier would not have run for this branch's change (a slow-marked file is skipped by design and does not count):
    the quick tier let a failure through -> add that file to CORE or add a path rule. This is how CORE is kept right from real failures, not guesses.
In CI the report goes to the job summary and each warning is also a ::warning:: line. Warnings never fail a run (--strict makes them fail).
What it cannot decide: what belongs in CORE (a judgement about what every change can break). A person edits CORE in tools/quick_tests.py.
"""
import ast
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
DEFAULT_GLOB = os.path.join(ROOT, ".test_timings", "*.xml")

SLOW_FILE_SHARE = 0.03          # an unmarked file with more than 3% of the suite's test time belongs outside the quick tier ...
MIN_SLOW_SECONDS = 10           # ... but never a file under this many seconds (a tiny suite would flag everything)
FAST_AGAIN_SHARE = 0.007        # a slow-marked file under 0.7% of the suite's time can come back in
CORE_BUDGET_SHARE = 0.15        # the quick tier's core set may hold at most this share of the suite's time (about 12% when it was set up: 8 files of 68)
MARK_LINE = 'pytestmark = pytest.mark.slow    # KW-70: skipped by the quick tier (-m "not slow"); CI runs it'
MARK_RE = re.compile(r"^pytestmark\s*=\s*pytest\.mark\.slow\b.*$", re.M)


# ---------------------------------------------------------------- reading the timings
def read_timings(paths):
    """({test file: seconds}, {test file: number of tests}) from JUnit XML files."""
    secs, count = {}, {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            cls = case.get("classname") or ""
            parts = [p for p in cls.split(".") if p.startswith("test_")]
            if not parts:
                continue
            f = parts[0]
            secs[f] = secs.get(f, 0.0) + float(case.get("time") or 0)
            count[f] = count.get(f, 0) + 1
    return secs, count


def read_failures(paths):
    """The test files that have a failed or errored test in these JUnit XML files."""
    failed = set()
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                parts = [p for p in (case.get("classname") or "").split(".") if p.startswith("test_")]
                if parts:
                    failed.add(parts[0])
    return failed


def missed_by_quick_tier(failed, chosen, slow, full):
    """[(kind, file, message)] for each failed test file the quick tier would NOT have run for this change. Nothing when the quick tier would have run the whole
    suite anyway, and a slow-marked file is skipped by design, so it is not a miss."""
    if full:
        return []
    return [("missed", f, "%s failed here but the quick tier would not have run it for this change: add it to CORE or add a path rule in tools/quick_tests.py "
                          "(docs/release-guide.md, 'Keeping the core set right')" % f)
            for f in sorted(failed) if f not in chosen and f not in slow]


def quick_tier_choice():
    """(chosen, full) for the current branch, or None when it cannot be worked out (no git, no origin/main)."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import quick_tests
        chosen, _, full = quick_tests.plan()
        return chosen, full
    except Exception as e:                                          # advisory only: never let this break the report
        print("(could not work out the quick tier's choice: %s)" % e)
        return None


def slow_files():
    out = set()
    for name in os.listdir(TESTS):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(TESTS, name), encoding="utf-8", errors="replace") as f:
                if MARK_RE.search(f.read()):
                    out.add(name[:-3])
    return out


def core_files():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import quick_tests
    return list(quick_tests.CORE)


# ---------------------------------------------------------------- judging them
def judge(secs, slow, core):
    """[(kind, file, message)]. kind: mark | unmark | core."""
    total = sum(secs.values()) or 1.0
    out = []
    for f, t in sorted(secs.items(), key=lambda kv: -kv[1]):
        share = t / total
        if f not in slow and f not in core and share > SLOW_FILE_SHARE and t >= MIN_SLOW_SECONDS:
            out.append(("mark", f, "%s is %.1f%% of the suite's test time (%d s) and is not marked slow: it slows the quick tier whenever it is selected (mark it slow)" % (f, share * 100, round(t))))
        if f in slow and share < FAST_AGAIN_SHARE:
            out.append(("unmark", f, "%s is marked slow but is only %.1f%% of the suite's test time (%d s): it can come back into the quick tier" % (f, share * 100, round(t))))
    core_total = sum(secs.get(f, 0.0) for f in core)
    if core_total / total > CORE_BUDGET_SHARE:
        out.append(("core", "CORE", "the core set is %.0f%% of the suite's test time (budget %.0f%%): trim CORE in tools/quick_tests.py or speed those tests up" % (core_total / total * 100, CORE_BUDGET_SHARE * 100)))
    return out


# ---------------------------------------------------------------- editing the marker (ast, so an import inside a string is never mistaken for one)
def insert_mark(text):
    """The file's text with the slow marker added after its last top-level import; unchanged when it is already there or has no pytest import."""
    nl = "\r\n" if "\r\n" in text else "\n"
    if MARK_RE.search(text):
        return text
    tree = ast.parse(text)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if not any(isinstance(n, ast.Import) and any(a.name == "pytest" for a in n.names) for n in imports):
        raise ValueError("no top-level `import pytest`")
    last = max(n.end_lineno for n in imports)
    lines = text.split(nl)
    lines[last:last] = ["", MARK_LINE]
    return nl.join(lines)


def remove_mark(text):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    keep = []
    for i, l in enumerate(lines):
        if MARK_RE.match(l):
            if keep and keep[-1] == "" and i + 1 < len(lines) and lines[i + 1] == "":
                keep.pop()                                  # do not leave two blank lines behind
            continue
        keep.append(l)
    return nl.join(keep)


def apply(findings):
    changed = []
    for kind, f, _ in findings:
        if kind not in ("mark", "unmark"):
            continue
        path = os.path.join(TESTS, f + ".py")
        with open(path, "rb") as fh:
            old = fh.read().decode("utf-8")
        new = insert_mark(old) if kind == "mark" else remove_mark(old)
        if new != old:
            with open(path, "wb") as fh:
                fh.write(new.encode("utf-8"))
            changed.append("%s: %s" % (f, "marked slow" if kind == "mark" else "slow marker removed"))
    return changed


# ---------------------------------------------------------------- the report
def report(secs, count, slow, core, findings):
    total = sum(secs.values())
    lines = ["## Test timings", "",
             "%d tests in %d files, **%d s** of test time in total (the sum over the tests: a parallel run takes less wall-clock time)." % (sum(count.values()), len(secs), round(total)),
             "Core set (quick tier): %d s, %.0f%% of the total." % (round(sum(secs.get(f, 0.0) for f in core)), sum(secs.get(f, 0.0) for f in core) / (total or 1) * 100),
             "", "| File | Seconds | Share | Tests | |", "|---|---|---|---|---|"]
    for f, t in sorted(secs.items(), key=lambda kv: -kv[1])[:12]:
        lines.append("| %s | %d | %.1f%% | %d | %s |" % (f, round(t), t / (total or 1) * 100, count[f], "slow" if f in slow else ("core" if f in core else "")))
    lines.append("")
    if findings:
        lines += ["### Needs attention"] + ["- %s" % m for _, _, m in findings]
        lines += ["", "Locally: `py tools/quick_tests.py --all` then `py tools/tier_health.py --apply` marks / unmarks the slow files for you (review the diff); the core set is a person's choice."]
    else:
        lines.append("Nothing needs attention: every unmarked file is a small share of the suite, every slow file is still big, the core set is within its budget.")
    return "\n".join(lines)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    do_apply, strict = "--apply" in args, "--strict" in args
    paths = [a for a in args if not a.startswith("--")] or sorted(glob.glob(DEFAULT_GLOB))
    paths = [p for p in paths if os.path.exists(p)]             # a step that failed before writing its file leaves a literal "*.xml" behind in CI
    if not paths:
        print("No timing files: run `py tools/quick_tests.py --all` first (it writes .test_timings/*.xml), or pass JUnit XML files.")
        return 0
    secs, count = read_timings(paths)
    if not secs:
        print("The files hold no test results.")
        return 0
    slow, core = slow_files(), core_files()
    findings = judge(secs, slow, core)
    failed = read_failures(paths)
    if failed:                                                      # the feedback loop for CORE: what failed here that the quick tier would have missed?
        choice = quick_tier_choice()
        if choice:
            findings += missed_by_quick_tier(failed, choice[0], slow, choice[1])
    md = report(secs, count, slow, core, findings)
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
        for _, _, m in findings:
            print("::warning title=Test tiers::%s" % m)
    if do_apply:
        done = apply(findings)
        print("\n" + ("\n".join(done) if done else "--apply: nothing to change."))
    return 1 if (strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
