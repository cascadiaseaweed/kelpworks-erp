@echo off
rem KelpWorks records sync -- edit the three lines below, then schedule this file (see docs\records-archive.md).
rem The archive key stays in archive-key.txt beside this file (one line, the same value as KELP_ERP_ARCHIVE_KEY on the server). Never e-mail or commit it.
set KELPWORKS_URL=https://YOUR-SERVICE.onrender.com
set KELPWORKS_RECORDS_DIR=C:\Users\YOU\Cascadia Seaweed Corp\Operations - KelpWorks-Records
set KEYFILE=%~dp0archive-key.txt

echo ==== %date% %time% >> "%~dp0sync.log"
python "%~dp0kelpworks_archive_sync.py" --key-file "%KEYFILE%" >> "%~dp0sync.log" 2>&1
exit /b %errorlevel%
