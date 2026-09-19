<#
.SYNOPSIS
    Build the atomik-meme-web frontend (web/dist) so `uv run atomik-meme-web` can serve it.

.DESCRIPTION
    Thin wrapper around `npm ci && npm run build` in the `web/` folder. Requires
    Node/npm to be installed; the Python backend and its tests do not need this
    script to have been run.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$webDir = Join-Path $repoRoot "web"

if (-not (Test-Path $webDir)) {
    Write-Error "web/ folder not found at $webDir"
    exit 1
}

Push-Location $webDir
try {
    Write-Host "Installing frontend dependencies (npm ci)..." -ForegroundColor Cyan
    npm ci
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Building the frontend (npm run build)..." -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Built web/dist. Run 'uv run atomik-meme-web' to serve it." -ForegroundColor Green
}
finally {
    Pop-Location
}
