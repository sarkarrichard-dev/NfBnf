@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

title QuantTape - ML extras (data + Hugging Face)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  pause
  exit /b 1
)

echo.
echo   Installing optional ML stacks: [data] ^(Excel/Parquet^) + [hf] ^(datasets / Hub^)
echo   This may take a minute on first run.
echo.

python -m pip install -e ".[data,hf]"
if errorlevel 1 (
  echo.
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)

echo.
echo   Done. You can run:
echo     scripts\windows\Preview-ML-HF-Stream.cmd
echo     scripts\windows\Run-ML-Ingest-DataForML.cmd
echo.
pause
