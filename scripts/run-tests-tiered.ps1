# Tiered test run (Interface -> ... -> Unit). E2E: set RUN_E2E=1 and install: pip install -e ".[e2e]" ; playwright install chromium
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "`n=== Interface + Security ===" -ForegroundColor Cyan
python -m pytest tests/test_api_smoke.py tests/test_security_http.py -v --tb=short
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Unit markers ===" -ForegroundColor Cyan
python -m pytest tests/ -m "unit" -v --tb=short
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Integration / smoke ===" -ForegroundColor Cyan
python -m pytest tests/ -m "integration or smoke" -v --tb=short
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Full suite (e2e excluded by default) ===" -ForegroundColor Cyan
python -m pytest tests/ -q --tb=short
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Ruff ===" -ForegroundColor Cyan
python -m ruff check trading_ai_engine tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($env:RUN_E2E -eq "1") {
  Write-Host "`n=== E2E (Playwright) ===" -ForegroundColor Cyan
  python -m pytest tests/test_dashboard_e2e.py -m e2e -v --tb=short
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
  Write-Host "`n(Skip E2E: set RUN_E2E=1 to run Playwright dashboard test)" -ForegroundColor DarkGray
}

Write-Host "`nAll selected tiers passed." -ForegroundColor Green
