#!/usr/bin/env python3
"""KelpWorks records sync: copies the production-run archive and the nightly backups from the KelpWorks server into a folder
(normally the synced SharePoint library "KelpWorks-Records"). Standard library only; run it on an office PC with Task Scheduler.

    python kelpworks_archive_sync.py --url https://your-service.onrender.com --root "C:\\...\\KelpWorks-Records" --key-file key.txt

What it does (see docs/records-archive.md):
  * creates the folder skeleton (01_System-Backups ... 05_Fulfillment) and README if they are missing;
  * for every finalized production run, downloads each document the server lists into its run folder, under the names the server chose;
    a file that changed on the server is downloaded again and the old copy is moved to an `_superseded` folder beside it;
  * downloads the newest nightly backups into 01_System-Backups/daily and keeps weekly and monthly copies, pruning only what it created;
  * NEVER deletes a record: a document removed in KelpWorks stays in the archive.

Exit code 0 = everything done, 1 = some files failed (the rest were still copied), 2 = bad settings or the server could not be reached.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

STATE_NAME = "_sync-state.json"
NIGHTLY = re.compile(r"^kelp_erp_(\d{4}-\d{2}-\d{2})\.zip$")
WEEKLY = re.compile(r"^kelp_erp_\d{4}-W\d{2}\.zip$")
MONTHLY = re.compile(r"^kelp_erp_\d{4}-\d{2}\.zip$")
PREMIGRATE = re.compile(r"^pre-migrate-[0-9A-Za-z-]+\.db\.gz$")
BACKUP_DIR = "01_System-Backups"


class SyncError(Exception):
    pass


def say(msg):
    print("%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg.encode("ascii", "replace").decode("ascii")), flush=True)


class Client:
    def __init__(self, base_url, key, timeout=300):
        self.base, self.key, self.timeout = base_url.rstrip("/"), key, timeout

    def _open(self, path):
        req = urllib.request.Request(self.base + path, headers={"X-Archive-Key": self.key})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            raise SyncError("the server answered %s for %s %s" % (e.code, path.split("?")[0], detail))
        except (urllib.error.URLError, OSError) as e:
            raise SyncError("could not reach the server (%s)" % getattr(e, "reason", e))

    def get_json(self, path):
        with self._open(path) as r:
            return json.loads(r.read().decode("utf-8"))

    def download(self, path, dest):
        """Stream into `dest`; returns the response headers."""
        with self._open(path) as r, open(dest, "wb") as out:
            shutil.copyfileobj(r, out, 1 << 20)
            return dict((k.lower(), v) for k, v in r.headers.items())


def long_prefix(path):
    """Windows stops at 260 characters unless a path starts with the extended-length marker; the library can be deeper than that, so every
    path the script uses carries it (a network share starts with two backslashes and gets the UNC form)."""
    marker = "\\\\" + "?" + "\\"
    if os.name != "nt" or path.startswith(marker):
        return path
    return marker + "UNC\\" + path[2:] if path.startswith("\\\\") else marker + path


def safe_join(root, rel):
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if rel.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
        raise SyncError("the server sent an unsafe path: %r" % rel)
    return os.path.join(root, *parts)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def supersede(path):
    """Move a file that is being replaced into `_superseded` beside it (never delete it)."""
    folder, name = os.path.split(path)
    old = os.path.join(folder, "_superseded")
    os.makedirs(old, exist_ok=True)
    stem, ext = os.path.splitext(name)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")
    target, n = os.path.join(old, "%s_old-%s%s" % (stem, stamp, ext)), 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(old, "%s_old-%s-%d%s" % (stem, stamp, n, ext))
    os.replace(path, target)
    return target


def load_state(root):
    try:
        with open(os.path.join(root, STATE_NAME), encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("files", {})
        st.setdefault("tiers", {})
        return st
    except (OSError, ValueError):
        return {"files": {}, "tiers": {}}


def save_state(root, state):
    tmp = os.path.join(root, STATE_NAME + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(root, STATE_NAME))


# ---- the run archive ----

def sync_files(client, root, state, dry_run=False):
    manifest = client.get_json("/api/archive/manifest")
    if manifest.get("format") != "kelpworks-records-manifest":
        raise SyncError("that server does not offer a records archive")
    if not dry_run:
        for d in manifest["skeleton"]:
            os.makedirs(safe_join(root, d), exist_ok=True)
    counts = {"downloaded": 0, "current": 0, "superseded": 0, "failed": 0}
    for p in manifest.get("problems", []):
        say("WARNING: %s - %s: %s" % (p.get("run"), p.get("what"), p.get("error")))
    for f in manifest["files"]:
        try:
            dest = safe_join(root, f["path"])
            if len(dest) > 360:
                say("WARNING: the path is %d characters long; SharePoint stops syncing at 400: %s" % (len(dest), dest))
            known = state["files"].get(f["path"])
            exists = os.path.isfile(dest)
            if exists and known is None and f.get("size") is not None and os.path.getsize(dest) == f["size"]:
                state["files"][f["path"]] = f["version"]                  # a copy made before the state file existed, same size: take it as current
                known = f["version"]
            if exists and known == f["version"]:
                counts["current"] += 1
            else:
                if dry_run:
                    say("would %s %s" % ("replace" if exists else "download", f["path"]))
                    counts["downloaded"] += 1
                    continue
                query = {"path": f["path"]}
                if f.get("runId"):
                    query["run"] = f["runId"]
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                part = dest + ".part"
                headers = client.download("/api/archive/file?" + urllib.parse.urlencode(query), part)
                want = headers.get("x-archive-sha256")
                if want and sha256_file(part) != want:
                    os.remove(part)
                    raise SyncError("the download of %s was damaged (checksum differs)" % f["path"])
                if exists:
                    supersede(dest)
                    counts["superseded"] += 1
                os.replace(part, dest)
                state["files"][f["path"]] = headers.get("x-archive-version") or f["version"]
                counts["downloaded"] += 1
            for old in f.get("supersedes", []):
                old_path = safe_join(root, old)
                if os.path.isfile(old_path) and not dry_run:
                    supersede(old_path)
                    counts["superseded"] += 1
        except (SyncError, OSError) as e:
            counts["failed"] += 1
            say("FAILED: %s: %s" % (f.get("path"), e))
    say("Run archive: %d runs, %d files: %d downloaded, %d already current, %d earlier copies kept in _superseded, %d failed"
        % (manifest.get("runs", 0), len(manifest["files"]), counts["downloaded"], counts["current"], counts["superseded"], counts["failed"]))
    return counts


# ---- backups ----

def tier_names(day):
    """(daily, weekly, monthly) file names a nightly backup of `day` (a date) belongs to."""
    iso = day.isocalendar()
    return ("kelp_erp_%s.zip" % day.isoformat(), "kelp_erp_%d-W%02d.zip" % (iso[0], iso[1]), "kelp_erp_%04d-%02d.zip" % (day.year, day.month))


def prune(folder, pattern, keep):
    """Remove the oldest files beyond `keep` among those that match `pattern` (anything else in the folder is left alone). Returns the names removed."""
    try:
        names = sorted(n for n in os.listdir(folder) if pattern.match(n))
    except OSError:
        return []
    removed = []
    for n in names[:max(0, len(names) - max(1, keep))]:
        try:
            os.remove(os.path.join(folder, n))
            removed.append(n)
        except OSError:
            pass
    return removed


def check_zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            return "kelp_erp.db" in z.namelist() and "manifest.json" in z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def copy_atomic(src, dst):
    tmp = dst + ".part"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def sync_backups(client, root, state, keep_daily, keep_weekly, keep_monthly, keep_premigrate, dry_run=False):
    listing = client.get_json("/api/archive/backups")["backups"]
    base = os.path.join(root, BACKUP_DIR)
    daily, weekly, monthly, pre = (os.path.join(base, d) for d in ("daily", "weekly", "monthly", "pre-migrate"))
    failed = new = 0
    nightly = sorted((b for b in listing if NIGHTLY.match(b["name"])), key=lambda b: b["name"] if b["kind"] == "nightly" else "")
    nightly = [b for b in nightly if b["kind"] == "nightly"]
    have = {n for n in (os.listdir(daily) if os.path.isdir(daily) else []) if NIGHTLY.match(n)}
    window = sorted(have | {b["name"] for b in nightly}, reverse=True)[:max(1, keep_daily)]          # the daily files retention will keep
    for b in nightly:
        day = datetime.date.fromisoformat(NIGHTLY.match(b["name"]).group(1))
        dn, wn, mn = tier_names(day)
        target = os.path.join(daily, dn)
        want_daily = not os.path.isfile(target) and dn in window
        tiers = [(weekly, wn), (monthly, mn)]
        want_tiers = [(f, n) for f, n in tiers
                      if not os.path.isfile(os.path.join(f, n)) or state["tiers"].get("%s/%s/%s" % (BACKUP_DIR, os.path.basename(f), n), "") < day.isoformat()]
        if not want_daily and not want_tiers:
            continue
        if dry_run:
            say("would download backup %s (%d MB)" % (b["name"], b["size"] // 1048576))
            continue
        try:
            os.makedirs(daily, exist_ok=True)
            part = target + ".part"
            client.download("/api/archive/backup?" + urllib.parse.urlencode({"name": b["name"]}), part)
            if os.path.getsize(part) != b["size"] or not check_zip(part):
                os.remove(part)
                raise SyncError("the backup %s arrived damaged" % b["name"])
            for folder, name in want_tiers:                               # the newest backup of the week / month replaces the earlier one
                os.makedirs(folder, exist_ok=True)
                copy_atomic(part, os.path.join(folder, name))
                state["tiers"]["%s/%s/%s" % (BACKUP_DIR, os.path.basename(folder), name)] = day.isoformat()
            if want_daily:
                os.replace(part, target)
            else:
                os.remove(part)
            new += 1
            say("Backup downloaded: %s (%d MB)" % (b["name"], b["size"] // 1048576))
        except (SyncError, OSError) as e:
            failed += 1
            say("FAILED: backup %s: %s" % (b["name"], e))
    for b in sorted((b for b in listing if b["kind"] == "pre-migrate"), key=lambda b: b["name"]):
        if not PREMIGRATE.match(b["name"]) or os.path.isfile(os.path.join(pre, b["name"])) or dry_run:
            continue
        try:
            os.makedirs(pre, exist_ok=True)
            part = os.path.join(pre, b["name"] + ".part")
            client.download("/api/archive/backup?" + urllib.parse.urlencode({"name": b["name"]}), part)
            if os.path.getsize(part) != b["size"]:
                os.remove(part)
                raise SyncError("the snapshot %s arrived damaged" % b["name"])
            os.replace(part, os.path.join(pre, b["name"]))
            new += 1
        except (SyncError, OSError) as e:
            failed += 1
            say("FAILED: snapshot %s: %s" % (b["name"], e))
    removed = []
    if not dry_run:
        removed += prune(daily, NIGHTLY, keep_daily) + prune(weekly, WEEKLY, keep_weekly) + prune(monthly, MONTHLY, keep_monthly)
        removed += prune(pre, PREMIGRATE, keep_premigrate)
        for n in removed:
            say("Old backup removed (beyond the retention): %s" % n)
    say("Backups: %d downloaded, %d removed by retention, %d failed" % (new, len(removed), failed))
    return {"downloaded": new, "removed": len(removed), "failed": failed}


def parse_args(argv):
    p = argparse.ArgumentParser(description="Copy the KelpWorks production-run archive and nightly backups into a records folder.")
    p.add_argument("--url", default=os.environ.get("KELPWORKS_URL"), help="the KelpWorks address, e.g. https://kelpworks.onrender.com (or env KELPWORKS_URL)")
    p.add_argument("--root", default=os.environ.get("KELPWORKS_RECORDS_DIR"), help="the KelpWorks-Records folder (or env KELPWORKS_RECORDS_DIR)")
    p.add_argument("--key", default=None, help="the archive key (better: --key-file or env KELPWORKS_ARCHIVE_KEY, so it is not on the command line)")
    p.add_argument("--key-file", default=None, help="a text file holding the archive key")
    p.add_argument("--keep-daily", type=int, default=14)
    p.add_argument("--keep-weekly", type=int, default=12)
    p.add_argument("--keep-monthly", type=int, default=24)
    p.add_argument("--keep-premigrate", type=int, default=6)
    p.add_argument("--skip-runs", action="store_true", help="only sync the backups")
    p.add_argument("--skip-backups", action="store_true", help="only sync the production-run documents")
    p.add_argument("--dry-run", action="store_true", help="show what would be copied; change nothing")
    p.add_argument("--init", action="store_true", help="create --root if it does not exist (otherwise a wrong path is an error)")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])
    key = a.key
    if not key and a.key_file:
        try:
            with open(a.key_file, encoding="utf-8") as f:
                key = f.read().strip()
        except OSError as e:
            say("Cannot read the key file: %s" % e)
            return 2
    key = key or os.environ.get("KELPWORKS_ARCHIVE_KEY", "")
    if not (a.url and a.root and key):
        say("Needs --url, --root and an archive key (--key-file, or env KELPWORKS_ARCHIVE_KEY). Run with --help.")
        return 2
    root = long_prefix(os.path.abspath(a.root))
    if not os.path.isdir(root):
        if not a.init:
            say("The folder %s does not exist. Check the path (is the SharePoint library synced?), or add --init to create it." % root)
            return 2
        os.makedirs(root, exist_ok=True)
    client = Client(a.url, key)
    state = load_state(root)
    problems = 0
    try:
        if not a.skip_runs:
            problems += sync_files(client, root, state, a.dry_run)["failed"]
        if not a.skip_backups:
            problems += sync_backups(client, root, state, a.keep_daily, a.keep_weekly, a.keep_monthly, a.keep_premigrate, a.dry_run)["failed"]
    except SyncError as e:
        say("STOPPED: %s" % e)
        return 2
    finally:
        if not a.dry_run:
            save_state(root, state)
    say("SYNC OK" if not problems else "SYNC FINISHED WITH %d PROBLEM(S): see the FAILED lines above" % problems)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
