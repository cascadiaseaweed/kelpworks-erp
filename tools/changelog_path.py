"""Where the KelpWorks change log lives: ONE place for every path and name (KW-62, KW-66). Standard library only.

* MASTER  = C:\\KelpWorks\\ChangeControl\\LOG_KelpWorks_ChangeControl.xlsx -- the only file any tool writes. It is OUTSIDE OneDrive on purpose (OneDrive put earlier copies back over good
  saves). tools/changelog_write.py is the only writer; Excel may open it for hand edits.
* SNAPSHOT = the read-only copy in SharePoint (QAQC > KelpWorks ERP > Change_Control, the same file name). It is published from the master after every tool write (or with --publish);
  Claude (chat) and everyone else READ it. No tool writes to it any other way.
* BACKUPS = <master folder>\\Backups (newest 30) and a second copy in SharePoint Change_Control\\Backups (newest 30, best effort).
* INBOX = SharePoint Change_Control\\Inbox: Claude (chat) drops note files there; `changelog_write.py --apply-inbox` validates and applies them.

The environment variables KELPWORKS_CHANGELOG (master), KELPWORKS_SNAPSHOT, KELPWORKS_BACKUP_MIRROR and KELPWORKS_INBOX point the tools somewhere else (another PC, or the tests). The
SharePoint locations are used ONLY together with the default master: a master given with --file or KELPWORKS_CHANGELOG has no snapshot, mirror or inbox unless its variable is set, so a
trial on a copy can never touch the real SharePoint files.
A tool that WRITES refuses any file that is not CHANGE_LOG_NAME inside a folder called MASTER_FOLDER_NAME and refuses anything inside OneDrive. A read-only look (--show, --dry-run,
the CSV export) may use any copy.
"""
import os
import re

CHANGE_LOG_NAME = "LOG_KelpWorks_ChangeControl.xlsx"
MASTER_FOLDER_NAME = "ChangeControl"
DEFAULT_PATH = r"C:\KelpWorks\ChangeControl\LOG_KelpWorks_ChangeControl.xlsx"
SHAREPOINT_FOLDER = r"C:\Users\nwran\OneDrive - Cascadia Seaweed Corp\Operations - Operations\Processing Operations\QAQC\KelpWorks ERP\Change_Control"
DEFAULT_SNAPSHOT = SHAREPOINT_FOLDER + "\\" + CHANGE_LOG_NAME
DEFAULT_MIRROR = SHAREPOINT_FOLDER + "\\Backups"
DEFAULT_INBOX = SHAREPOINT_FOLDER + "\\Inbox"
BACKUP_KEEP = 30
# KW-71: the working folder for DRAFT revisions of the Quality Manual (QM-001). tools/qm_draft_revision.py reads a draft copy of the manual from here and writes only here: never the
# controlled copy. KELPWORKS_QM_DRAFTS points it somewhere else (another PC, or the tests).
DEFAULT_DRAFTS = r"C:\Users\nwran\OneDrive - Cascadia Seaweed Corp\Operations - Operations\Processing Operations\QAQC\1_General\General_WIP_Drafts"


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def configured_path():
    """The master."""
    return os.environ.get("KELPWORKS_CHANGELOG") or DEFAULT_PATH


def _for(master, variable, default):
    value = os.environ.get(variable)
    if value:
        return value
    return default if _same(master or configured_path(), DEFAULT_PATH) else None


def snapshot_for(master=None):
    """The read-only SharePoint copy of `master` (None = not configured for this master)."""
    return _for(master, "KELPWORKS_SNAPSHOT", DEFAULT_SNAPSHOT)


def mirror_for(master=None):
    """Where the second copy of each backup goes (None = not configured)."""
    return _for(master, "KELPWORKS_BACKUP_MIRROR", DEFAULT_MIRROR)


def inbox_for(master=None):
    """The folder Claude (chat) leaves notes in (None = not configured)."""
    return _for(master, "KELPWORKS_INBOX", DEFAULT_INBOX)


def drafts_folder():
    """The folder for DRAFT Quality Manual revisions (the only place qm_draft_revision.py reads a manual from or writes to)."""
    return os.environ.get("KELPWORKS_QM_DRAFTS") or DEFAULT_DRAFTS


def backup_dir(master=None):
    return os.path.join(os.path.dirname(os.path.abspath(master or configured_path())), "Backups")


def inside_onedrive(path):
    parts = [p for p in re.split(r"[\\/]", os.path.abspath(path).lower()) if p]
    return any(p.startswith("onedrive") or p == "sharepoint" for p in parts)


def check_writable(path):
    """Raise ValueError unless `path` is the master: the right file name in a folder called ChangeControl, not in OneDrive and not the SharePoint snapshot."""
    full = os.path.abspath(path)
    folder, name = os.path.split(full)
    if name.lower() != CHANGE_LOG_NAME.lower() or os.path.basename(folder).lower() != MASTER_FOLDER_NAME.lower():
        raise ValueError("'%s' is not the change log master. Only %s in a folder called %s may be written (master: %s)." % (path, CHANGE_LOG_NAME, MASTER_FOLDER_NAME, configured_path()))
    if inside_onedrive(full) or _same(full, DEFAULT_SNAPSHOT) or (os.environ.get("KELPWORKS_SNAPSHOT") and _same(full, os.environ["KELPWORKS_SNAPSHOT"])):
        raise ValueError("'%s' is inside OneDrive or is the read-only SharePoint snapshot. The master lives outside OneDrive (%s); edit that one." % (path, DEFAULT_PATH))
