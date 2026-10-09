@echo off
REM Start the KelpWorks ERP server (http://localhost:8002)
REM Local development: a NEW database gets the seed admin admin@kelp.local / kelp1234 (see README)
set KELP_ERP_ENV=development
python "%~dp0kelp_erp_server.py"
