@echo off
setlocal EnableExtensions
title QuantHawk - Share on Tailnet

echo ============================================================
echo   Share the QuantHawk dashboard with your team over Tailscale
echo ============================================================
echo.
echo This proxies https://^<this-pc^>.^<your-tailnet^>.ts.net  to the
echo server on 127.0.0.1:8000. The server stays bound to loopback -
echo it is NEVER exposed on your home network or the public internet.
echo Only devices you have invited to your tailnet can reach it.
echo.

where tailscale >nul 2>nul
if errorlevel 1 (
  echo [X] tailscale command not found. Install Tailscale for Windows
  echo     from https://tailscale.com/download and sign in first.
  echo.
  pause
  exit /b 1
)

echo Checking Tailscale is signed in...
tailscale status >nul 2>nul
if errorlevel 1 (
  echo [X] Tailscale is not connected. Open the Tailscale app and sign in.
  echo.
  pause
  exit /b 1
)

echo.
echo [1] Start sharing   ^(tailscale serve --bg 8000^)
echo [2] Show the share URL
echo [3] Stop sharing
echo [0] Close
echo.
set /p "choice=Choose [1]: "
if "%choice%"=="" set "choice=1"

if "%choice%"=="1" goto start
if "%choice%"=="2" goto show
if "%choice%"=="3" goto stop
goto end

:start
echo.
echo Starting the tailnet proxy for port 8000...
tailscale serve --bg 8000
if errorlevel 1 (
  echo.
  echo [X] tailscale serve failed. The usual cause is HTTPS certificates
  echo     not being enabled for your tailnet. Fix once:
  echo       login.tailscale.com  -^>  DNS  -^>  "HTTPS Certificates"  -^>  Enable
  echo     Also make sure MagicDNS is on. Then run this again.
  echo.
  pause
  exit /b 1
)
echo.
echo Done. Share URL:
tailscale serve status
echo.
echo Give your developer / tester:
echo   1. an invite to this tailnet  (login.tailscale.com -^> Users -^> Invite,
echo      or "Share" a node - simplest is inviting them as a user)
echo   2. the https://...ts.net URL printed above
echo   3. the DASHBOARD_PASSWORD if you set one in .env
echo.
echo Keep this PC on and the QuantHawk server running (Start Index Options AI.cmd).
echo.
pause
exit /b 0

:show
echo.
tailscale serve status
echo.
pause
exit /b 0

:stop
echo.
echo Stopping the tailnet proxy for port 8000...
tailscale serve --bg 8000 off
echo Sharing stopped. The URL will 502 until you start it again.
echo (If a stale mapping lingers, run:  tailscale serve reset  )
echo.
pause
exit /b 0

:end
endlocal
