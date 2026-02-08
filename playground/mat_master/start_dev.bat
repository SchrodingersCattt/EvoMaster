@echo off
REM Start MatMaster backend only. Run from EvoMaster project root.
REM For frontend: cd playground\mat_master\frontend && npm install && npm run dev

cd /d "%~dp0\..\.."
echo Starting MatMaster backend on :8000...
cd playground\mat_master\service
start /B python server.py
echo API: http://localhost:8000
echo To start frontend: cd playground\mat_master\frontend ^&^& npm run dev
pause
