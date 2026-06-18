@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Index Options AI

set "APP_URL=http://127.0.0.1:8000"
set "PID_FILE=%~dp0memory\server.pid"

:menu
cls
echo ========================================
echo        Index Options AI
echo ========================================
echo.
call :show_status
echo.
echo Dashboard: %APP_URL%  ^(React UI — same URL for everything^)
echo.
echo [1] Start  ^(server + dashboard in browser^)
echo [2] Start algo  ^(server + scanner + dashboard^)
echo [3] Stop server
echo [4] Restart server
echo [5] Open dashboard only
echo [0] Close
echo.
set /p "choice=Choose an option [1]: "
if "%choice%"=="" set "choice=1"

if "%choice%"=="1" goto start_with_dashboard
if "%choice%"=="2" goto start_algo
if "%choice%"=="3" goto stop_server
if "%choice%"=="4" goto restart_server
if "%choice%"=="5" goto open_dashboard
if "%choice%"=="0" goto end

echo.
echo Unknown option.
timeout /t 2 /nobreak >nul
goto menu

:start_with_dashboard
call :find_pid
if defined RUNNING_PID (
  echo.
  echo Server already running on PID !RUNNING_PID!.
  goto open_dashboard_once
)
call :ensure_dashboard
if errorlevel 1 goto menu
call :start_server_core
if errorlevel 1 goto menu
timeout /t 2 /nobreak >nul
goto open_dashboard_once

:start_algo
call :find_pid
if not defined RUNNING_PID (
  call :ensure_dashboard
  if errorlevel 1 goto menu
  call :start_server_core
  if errorlevel 1 goto menu
  timeout /t 2 /nobreak >nul
  call :find_pid
)
if not defined RUNNING_PID (
  echo Could not start server.
  timeout /t 3 /nobreak >nul
  goto menu
)
echo Starting scanner...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Method Post -Uri '%APP_URL%/api/auto/start' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo Scanner start failed — check Dhan token in the dashboard.
) else (
  echo Scanner started.
)
goto open_dashboard_once

:ensure_dashboard
echo.
echo Preparing dashboard...
if not exist "%~dp0dashboard\node_modules" (
  echo Installing dashboard dependencies ^(first run^)...
  pushd "%~dp0dashboard"
  call npm install
  if errorlevel 1 (
    echo npm install failed. Install Node.js 20+ from nodejs.org
    popd
    exit /b 1
  )
  popd
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure-dashboard-build.ps1"
if errorlevel 1 (
  echo Dashboard build failed.
  exit /b 1
)
exit /b 0

:start_server_core
if not exist "%~dp0memory" mkdir "%~dp0memory"
if not exist "%~dp0dashboard\dist\index.html" (
  echo Dashboard build missing — run option 1 again.
  exit /b 1
)
echo Starting server...
echo Logs: memory\server.log
start "Index Options AI Server" /D "%~dp0" cmd /k "set NO_COLOR=1&& set PYTHONUNBUFFERED=1&& python -u -m index_ai.server"
timeout /t 2 /nobreak >nul
call :find_pid
if defined RUNNING_PID (
  echo !RUNNING_PID!>"%PID_FILE%"
  echo Server running on PID !RUNNING_PID!.
  exit /b 0
)
echo Server did not start. Run: python -m index_ai.server
exit /b 1

:stop_server
call :find_pid
if not defined RUNNING_PID (
  echo.
  echo Server is not running.
  if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
  timeout /t 2 /nobreak >nul
  goto menu
)
echo.
echo Stopping server on PID !RUNNING_PID!...
taskkill /PID !RUNNING_PID! /T /F >nul 2>nul
if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
timeout /t 1 /nobreak >nul
call :find_pid
if defined RUNNING_PID (
  echo Port 8000 still in use on PID !RUNNING_PID!.
) else (
  echo Server stopped.
)
timeout /t 2 /nobreak >nul
goto menu

:restart_server
call :find_pid
if defined RUNNING_PID (
  echo Stopping current server...
  taskkill /PID !RUNNING_PID! /T /F >nul 2>nul
  timeout /t 1 /nobreak >nul
)
if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
goto start_with_dashboard

:open_dashboard
call :find_pid
if not defined RUNNING_PID (
  echo.
  echo Server is not running. Choose option 1 to start.
  timeout /t 3 /nobreak >nul
  goto menu
)
goto open_dashboard_once

:open_dashboard_once
echo.
echo Opening dashboard at %APP_URL%
start "" "%APP_URL%"
timeout /t 2 /nobreak >nul
goto menu

:show_status
call :find_pid
if defined RUNNING_PID (
  echo Status: RUNNING  ^(PID !RUNNING_PID!^)
) else (
  echo Status: STOPPED
)
exit /b

:find_pid
set "RUNNING_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set "RUNNING_PID=%%P"
exit /b

:end
echo.
echo Done. Use option 3 next time to stop the server.
endlocal
