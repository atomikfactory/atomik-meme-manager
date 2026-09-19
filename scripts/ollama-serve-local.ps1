<#
.SYNOPSIS
    Run a second, dedicated Ollama server whose model store lives inside this
    repository, instead of the user's normal Ollama installation.

.DESCRIPTION
    This starts `ollama serve` with:
      - OLLAMA_MODELS pointed at <repo>\models (resolved relative to this
        script, so it works no matter where the repo is checked out)
      - OLLAMA_HOST set to 127.0.0.1:11435 (NOT the default 11434, so it does
        not collide with the user's normal Ollama tray application, which
        keeps listening on 11434 with its own, separate model store).

    Run this in its own terminal/window and leave it running. Then, in
    atomik-meme.yaml, point `ollama.host` at this server:

        ollama:
          host: http://127.0.0.1:11435

    ...or set the environment variable before invoking atomik-meme (this
    overrides `ollama.host` from the config file):

        $env:OLLAMA_HOST = "127.0.0.1:11435"
        atomik-meme check

    Use scripts\ollama-pull-models.ps1 (in a second terminal, once this
    server is up) to download the fast/accurate models into this repo's
    models folder.

.NOTES
    Requires `ollama` to be installed and on PATH. This does not affect the
    user's normal Ollama server on port 11434 in any way - they are two
    independent processes with two independent model stores.
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ModelsDir = Join-Path $RepoRoot "models"

if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
}

Write-Host "Starting a dedicated Ollama server for atomik-meme:"
Write-Host "  OLLAMA_MODELS = $ModelsDir"
Write-Host "  OLLAMA_HOST   = 127.0.0.1:11435"
Write-Host ""
Write-Host "Point atomik-meme.yaml's ollama.host at http://127.0.0.1:11435"
Write-Host "(or set `$env:OLLAMA_HOST = '127.0.0.1:11435'` before running atomik-meme)."
Write-Host ""

$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_HOST = "127.0.0.1:11435"

ollama serve
