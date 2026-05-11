@echo off
setlocal EnableDelayedExpansion
title Trading AI Workstation — launcher

REM Optional: drag-drop or first argument sets port, e.g.  Launch Trading AI Dashboard.cmd 8001
if not "%~1"=="" set "TRADING_AI_PORT=%~1"
if not defined TRADING_AI_PORT set "TRADING_AI_PORT=8000"

:menu
cls
echo.
echo   ========================================
echo     Trading AI Workstation  —  launcher
echo   ========================================
echo     Dashboard: http://127.0.0.1:!TRADING_AI_PORT!/
echo     Port: !TRADING_AI_PORT!
echo   ========================================
call :is_listening
if "!LISTENING_PID!"=="" (
  echo     Server: not running
) else (
  echo     Server: running ^(PID !LISTENING_PID!^)
)
echo   ========================================
echo.
echo     [1]  Start server   ^(new minimized window^)
echo     [2]  Stop server   ^(free port !TRADING_AI_PORT!^)
echo     [3]  Open dashboard in browser
echo     [4]  Change port
echo     [0]  Exit launcher
echo.
set /p "_sel=   Choose 0-4: "

if "!_sel!"=="0" goto :eof
if "!_sel!"=="1" call :do_start
if "!_sel!"=="2" call :do_stop
if "!_sel!"=="3" call :do_browser
if "!_sel!"=="4" call :do_setport
goto :menu


:is_listening
set "LISTENING_PID="
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":!TRADING_AI_PORT!" ^| findstr LISTENING') do (
  set "LISTENING_PID=%%p"
  goto :eof
)
goto :eof


:do_start
call :is_listening
if not "!LISTENING_PID!"=="" (
  echo.
  echo   Server already running on port !TRADING_AI_PORT! ^(PID !LISTENING_PID!^).
  echo   Use [2] Stop first if you want a clean restart.
  pause
  exit /b
)
echo.
echo   Starting server ^(minimized window — leave it open while you trade^)...
REM /D sets working directory so paths with spaces work; server reads TRADING_AI_PORT from env
start "Trading AI — server" /D "%~dp0" /MIN cmd /k "set TRADING_AI_PORT=!TRADING_AI_PORT!&& python -m trading_ai_engine.server.main"
timeout /t 2 /nobreak >nul
call :is_listening
if "!LISTENING_PID!"=="" (
  echo   Warning: server may have failed to start. Check the minimized window for errors.
) else (
  echo   Server listening ^(PID !LISTENING_PID!^).
  start "" "http://127.0.0.1:!TRADING_AI_PORT!/"
)
echo.
pause
exit /b


:do_stop
echo.
echo   Stopping listener^(s^) on port !TRADING_AI_PORT!...
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":!TRADING_AI_PORT!" ^| findstr LISTENING') do (
  echo     taskkill /PID %%p
  taskkill /F /PID %%p 1>nul 2>&1
)
timeout /t 1 /nobreak >nul
call :is_listening
if "!LISTENING_PID!"=="" (echo   Port !TRADING_AI_PORT! is free.) else (echo   PID !LISTENING_PID! still listening — try again or close that process manually.)
echo.
pause
exit /b


:do_browser
start "" "http://127.0.0.1:!TRADING_AI_PORT!/"
exit /b


:do_setport
echo.
set /p "_np=   New port number ^(ENTER=cancel^): "
if "!_np!"=="" exit /b
set "TRADING_AI_PORT=!_np!"
echo   Port set to !TRADING_AI_PORT!
pause
exit /b
