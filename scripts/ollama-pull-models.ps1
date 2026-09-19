<#
.SYNOPSIS
    Pull the fast/accurate models into the dedicated local Ollama server
    started by scripts\ollama-serve-local.ps1 (OLLAMA_HOST 127.0.0.1:11435,
    model store at <repo>\models).

.DESCRIPTION
    Run scripts\ollama-serve-local.ps1 first (in its own terminal) and leave
    it running, then run this script in a second terminal.

    Model names are read from atomik-meme.yaml (repo root, or the current
    directory) if present - every `name: <tag>` found under `models:` is
    pulled, deduplicated (vision and text usually share the same fast/
    accurate tags, so this normally means exactly two downloads). If no
    config file is found, it falls back to the documented defaults:
    qwen3.5:4b (fast) and qwen3.5:9b (accurate).
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $RepoRoot "atomik-meme.yaml"
if (-not (Test-Path $ConfigPath)) {
    $CwdConfigPath = Join-Path (Get-Location) "atomik-meme.yaml"
    if (Test-Path $CwdConfigPath) {
        $ConfigPath = $CwdConfigPath
    }
}

$Models = New-Object System.Collections.Generic.List[string]

if (Test-Path $ConfigPath) {
    Write-Host "Reading model names from $ConfigPath"
    $content = Get-Content $ConfigPath -Raw
    $found = [regex]::Matches($content, 'name:\s*([^\s,}]+)')
    foreach ($m in $found) {
        $name = $m.Groups[1].Value.Trim()
        if (-not $Models.Contains($name)) {
            $Models.Add($name) | Out-Null
        }
    }
}

if ($Models.Count -eq 0) {
    Write-Host "No atomik-meme.yaml found (or no models listed in it); using defaults."
    $Models.Add("qwen3.5:4b") | Out-Null
    $Models.Add("qwen3.5:9b") | Out-Null
}

$env:OLLAMA_HOST = "127.0.0.1:11435"
Write-Host ""
Write-Host "Pulling into the local server at http://$($env:OLLAMA_HOST) ..."

foreach ($m in $Models) {
    Write-Host ""
    Write-Host ">>> ollama pull $m"
    ollama pull $m
}

Write-Host ""
Write-Host "Done. Verify with: `$env:OLLAMA_HOST = '127.0.0.1:11435'; ollama list"
