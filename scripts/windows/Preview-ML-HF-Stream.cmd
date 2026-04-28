@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

title QuantTape - HF CSV stream preview

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
echo   Hugging Face streaming preview ^(first 5 rows^)
echo   Root: "%ROOT%"
echo.

python -m nbnf.ml.hf_cli --root "%ROOT%" --preview-rows 5
echo.
pause
