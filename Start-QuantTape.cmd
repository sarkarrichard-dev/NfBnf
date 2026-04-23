@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title QuantTape — Indian markets desk

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found on PATH. Install from https://www.python.org/ ^(check "Add to PATH"^).
  pause
  exit /b 1
)

echo.
echo  QuantTape — starting server at http://127.0.0.1:8000/
echo  A browser tab will open in a few seconds. Close this window to stop the server.
echo.

start "" "http://127.0.0.1:8000/"
ping 127.0.0.1 -n 6 >nul

python -m uvicorn nbnf.server.app:app --host 127.0.0.1 --port 8000
echo.
echo Server exited.
pause
