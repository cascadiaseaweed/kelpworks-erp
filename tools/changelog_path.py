"""Where the KelpWorks change log lives: ONE place for its name, folder and default path (KW-62). Standard library only.

Every tool that touches the workbook (changelog_write.py, update_backlog_xlsx.py, export_backlog.py) takes the path from here. The environment variable KELPWORKS_CHANGELOG
points the tools at the same file on another PC (a different OneDrive path); it does not change the name. A tool that WRITES refuses any file that is not called
CHANGE_LOG_NAME inside a folder called CHANGE_LOG_FOLDER, so a copy ("... - Copy.xlsx"), an old name or a file on the desktop is never changed by mistake.
A read-only look (--show, --dry-run, the CSV export) may use any copy.
"""
import os

CHANGE_LOG_NAME = "LOG_KelpWorks_ChangeControl.xlsx"
CHANGE_LOG_FOLDER = "Change_Control"
DEFAULT_PATH = r"C:\Users\nwran\OneDrive - Cascadia Seaweed Corp\Operations - Operations\Processing Operations\QAQC\KelpWorks ERP\Change_Control\LOG_KelpWorks_ChangeControl.xlsx"


def configured_path():
    return os.environ.get("KELPWORKS_CHANGELOG") or DEFAULT_PATH


def check_writable(path):
    """Raise ValueError unless `path` is the change log itself (right file name, in the Change_Control folder)."""
    full = os.path.abspath(path)
    folder, name = os.path.split(full)
    if name.lower() != CHANGE_LOG_NAME.lower() or os.path.basename(folder).lower() != CHANGE_LOG_FOLDER.lower():
        raise ValueError("'%s' is not the change log. Only %s in a folder called %s may be written (configured: %s)." % (path, CHANGE_LOG_NAME, CHANGE_LOG_FOLDER, configured_path()))
