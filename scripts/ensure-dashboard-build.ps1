# Rebuild dashboard/dist when missing or when dashboard/src is newer than dist.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dashboard = Join-Path $root 'dashboard'
$distIndex = Join-Path $dashboard 'dist\index.html'

function Needs-Rebuild {
    if (-not (Test-Path $distIndex)) { return $true }
    $distTime = (Get-Item $distIndex).LastWriteTimeUtc
    $srcRoot = Join-Path $dashboard 'src'
    if (-not (Test-Path $srcRoot)) { return $false }
    foreach ($file in Get-ChildItem $srcRoot -Recurse -File) {
        if ($file.LastWriteTimeUtc -gt $distTime) { return $true }
    }
    $pkg = Join-Path $dashboard 'package.json'
    if ((Test-Path $pkg) -and ((Get-Item $pkg).LastWriteTimeUtc -gt $distTime)) { return $true }
    return $false
}

if (-not (Needs-Rebuild)) {
    Write-Host 'Dashboard build is up to date.'
    exit 0
}

Write-Host 'Building React dashboard...'
Push-Location $dashboard
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
Write-Host 'Dashboard ready at http://127.0.0.1:8000/'
exit 0
