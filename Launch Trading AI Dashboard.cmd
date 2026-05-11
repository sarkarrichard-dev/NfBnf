@echo off
setlocal EnableDelayedExpansion
title Trading AI - Start / Stop

set "TA_LOG_DIR=%~dp0logs"
set "TA_LOG=%TA_LOG_DIR%\launcher.log"
if not exist "%TA_LOG_DIR%" mkdir "%TA_LOG_DIR%" 2>nul
call :log_line "=== Launcher opened ==="

if not "%~1"=="" set "TRADING_AI_PORT=%~1"
if not defined TRADING_AI_PORT set "TRADING_AI_PORT=8000"
call :log_line "TRADING_AI_PORT=!TRADING_AI_PORT!"

:menu
cls
echo.
echo   ----------------------------------------
echo     Trading AI workstation
echo   ----------------------------------------
echo     URL:  http://127.0.0.1:!TRADING_AI_PORT!/
echo     Port: !TRADING_AI_PORT!  ^(optional: drag script onto cmd, or set TRADING_AI_PORT^)
echo   ----------------------------------------
call :is_listening
if "!LISTENING_PID!"=="" (
  echo     Status: STOPPED
) else (
  echo     Status: RUNNING  ^(PID !LISTENING_PID!^)
)
echo   ----------------------------------------
echo.
echo     [1]  Start
echo     [2]  Stop
echo     [0]  Exit
echo.
set /p "_sel=   Choose 0-2: "

call :log_line "Choice: !_sel!"
if "!_sel!"=="0" goto :done
if "!_sel!"=="1" call :do_start
if "!_sel!"=="2" call :do_stop
goto :menu

:done
call :log_line "Exit"
goto :eof


:log_line
>>"%TA_LOG%" echo [%date% %time%] %~1
goto :eof


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
  echo   Already running on port !TRADING_AI_PORT! ^(PID !LISTENING_PID!^).
  echo   Use [2] Stop first if you need a fresh server ^(e.g. after code updates^).
  call :log_line "Start refused - port in use PID !LISTENING_PID!"
  pause
  exit /b
)
echo.
echo   Starting ^(minimized server window^)...
call :log_line "Start python -m trading_ai_engine.server.main port=!TRADING_AI_PORT!"
start "Trading AI server" /D "%~dp0" /MIN cmd /k "set NO_COLOR=1&& set PYTHONUNBUFFERED=1&& set TRADING_AI_PORT=!TRADING_AI_PORT!&& python -m trading_ai_engine.server.main"
timeout /t 2 /nobreak >nul
call :is_listening
if "!LISTENING_PID!"=="" (
  echo   Not listening yet - check the minimized window for errors.
  call :log_line "ERROR: not listening after start"
) else (
  echo   Running ^(PID !LISTENING_PID!^). Opening dashboard...
  call :log_line "Listening PID !LISTENING_PID!"
  start "" "http://127.0.0.1:!TRADING_AI_PORT!/"
)
echo.
pause
exit /b


:do_stop
echo.
echo   Stopping process^(es^) on port !TRADING_AI_PORT!...
call :log_line "Stop port !TRADING_AI_PORT!"
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":!TRADING_AI_PORT!" ^| findstr LISTENING') do (
  call :log_line "taskkill /F /PID %%p"
  taskkill /F /PID %%p 1>>"%TA_LOG%" 2>>&1
)
timeout /t 1 /nobreak >nul
call :is_listening
if "!LISTENING_PID!"=="" (
  echo   Stopped. Port !TRADING_AI_PORT! is free.
  call :log_line "Port free"
) else (
  echo   Still listening ^(PID !LISTENING_PID!^) - close it in Task Manager or retry.
  call :log_line "ERROR: still listening PID !LISTENING_PID!"
)
echo.
pause
exit /b
