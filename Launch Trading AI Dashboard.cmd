@echo off
setlocal
cd /d "%~dp0"
title Trading AI Workstation

echo Starting Trading AI Workstation...
echo.
echo A browser window will open at http://127.0.0.1:8000
echo Keep this window open while you use the dashboard.
echo.

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8000"
python -m trading_ai_engine.server.main

echo.
echo The dashboard stopped. Press any key to close this window.
pause >nul
