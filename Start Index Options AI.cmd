@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Index Options AI Controller

set "APP_URL=http://127.0.0.1:8000"
set "PID_FILE=%~dp0memory\server.pid"

:menu
cls
echo ========================================
echo        Index Options AI Controller
echo ========================================
echo.
call :show_status
echo.
echo [1] Start AI server ^(scanner auto-starts when Dhan is ready^)
echo [2] Stop AI server
echo [3] Restart AI server
echo [4] Open dashboard
echo [5] Refresh status
echo [6] Start Algo ^(server + scanner + dashboard^)
echo [0] Close this controller
echo.
set /p "choice=Choose an option: "

if "%choice%"=="1" goto start_server
if "%choice%"=="2" goto stop_server
if "%choice%"=="3" goto restart_server
if "%choice%"=="4" goto open_dashboard
if "%choice%"=="5" goto menu
if "%choice%"=="6" goto start_algo
if "%choice%"=="0" goto end

echo.
echo Unknown option.
timeout /t 3 /nobreak >nul
goto menu

:start_server
call :find_pid
if defined RUNNING_PID (
  echo.
  echo AI server is already running on PID !RUNNING_PID!.
  echo Dashboard: %APP_URL%
  timeout /t 3 /nobreak >nul
  goto menu
)

call :start_server_core
timeout /t 3 /nobreak >nul
goto menu

:start_server_core
if not exist "%~dp0memory" mkdir "%~dp0memory"
if not exist "%~dp0dashboard\dist\index.html" (
  echo Building React dashboard ^(first run^)...
  pushd "%~dp0dashboard"
  call npm run build
  if errorlevel 1 (
    echo Dashboard build failed. Install Node.js and run: cd dashboard ^&^& npm install ^&^& npm run build
    popd
    exit /b 1
  )
  popd
)
echo.
echo Starting AI server in the background...
start "Index Options AI Server" /D "%~dp0" /min python -m index_ai.server
timeout /t 2 /nobreak >nul
call :find_pid

if defined RUNNING_PID (
  echo !RUNNING_PID!>"%PID_FILE%"
  echo AI server started on PID !RUNNING_PID!.
  echo Use option 4 to open the dashboard.
) else (
  echo AI server did not start. Run python -m index_ai.server to see the error.
)
exit /b

:stop_server
call :find_pid
if not defined RUNNING_PID (
  echo.
  echo AI server is not running.
  if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
  timeout /t 3 /nobreak >nul
  goto menu
)

echo.
echo Stopping AI server on PID !RUNNING_PID!...
taskkill /PID !RUNNING_PID! /T /F >nul 2>nul
if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
timeout /t 1 /nobreak >nul
call :find_pid
if defined RUNNING_PID (
  echo Port 8000 is still open on PID !RUNNING_PID!.
) else (
  echo AI server stopped. Port 8000 is closed.
)
timeout /t 3 /nobreak >nul
goto menu

:restart_server
call :find_pid
if defined RUNNING_PID (
  echo.
  echo Stopping current AI server on PID !RUNNING_PID!...
  taskkill /PID !RUNNING_PID! /T /F >nul 2>nul
  timeout /t 1 /nobreak >nul
)
if exist "%PID_FILE%" del "%PID_FILE%" >nul 2>nul
goto start_server

:open_dashboard
echo.
echo Opening dashboard...
start "" "%APP_URL%"
timeout /t 3 /nobreak >nul
goto menu

:start_algo
echo.
echo Launching algo from one place...
call :find_pid
if not defined RUNNING_PID (
  call :start_server_core
  timeout /t 2 /nobreak >nul
  call :find_pid
)

if not defined RUNNING_PID (
  echo Could not start AI server, so scanner was not started.
  timeout /t 4 /nobreak >nul
  goto menu
)

echo Starting scanner...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Method Post -Uri '%APP_URL%/api/auto/start' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo Scanner start request failed. Open dashboard for details.
) else (
  echo Algo scanner started.
)
echo Opening dashboard...
start "" "%APP_URL%"
timeout /t 4 /nobreak >nul
goto menu

:show_status
call :find_pid
if defined RUNNING_PID (
  echo Status: RUNNING
  echo PID: !RUNNING_PID!
  echo Dashboard: %APP_URL%
) else (
  echo Status: STOPPED
  echo Dashboard: %APP_URL%
)
exit /b

:find_pid
set "RUNNING_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set "RUNNING_PID=%%P"
exit /b

:end
echo.
echo Controller closed. If the AI server is running, use option 2 next time to stop it.
endlocal
