@echo off
rem KelpWorks records sync -- edit the two "set" lines below, then schedule this file (see docs\records-archive.md).
rem The archive key stays in archive-key.txt beside this file (one line, the same value as KELP_ERP_ARCHIVE_KEY on the server). Never e-mail or commit it.
set KELPWORKS_URL=https://kelpworks-erp.onrender.com
set KELPWORKS_RECORDS_DIR=C:\Users\nwran\OneDrive - Cascadia Seaweed Corp\Operations - Operations\KelpWorks-Records
set KEYFILE=%~dp0archive-key.txt

rem Windows installs Python as "py" (the launcher); some computers also have "python".
set PYEXE=python
where py >nul 2>nul && set PYEXE=py

echo ==== %date% %time% >> "%~dp0sync.log"
%PYEXE% "%~dp0kelpworks_archive_sync.py" --key-file "%KEYFILE%" >> "%~dp0sync.log" 2>&1
exit /b %errorlevel%
