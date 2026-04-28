@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

title QuantTape - SQLite ML catalog ingest ^(data for ml^)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  pause
  exit /b 1
)

set "ROOT=%CD%\data for ml"
if not exist "%ROOT%" (
  echo [ERROR] Folder not found: "%ROOT%"
  pause
  exit /b 1
)

echo.
echo   Ingesting tabular profiles into SQLite ^(catalog for desk + AI digest^)
echo   Root: "%ROOT%"
echo   Tip: add --also-builtins to the command in this file if you also want data\ scanned.
echo.

python -m nbnf.ml.ingest_cli "%ROOT%"
echo.
pause
