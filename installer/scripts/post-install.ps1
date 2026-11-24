# Title Search Desktop — post-install bootstrap (run elevated or from installer)
param(
    [string]$DataDir = "$env:LOCALAPPDATA\TitleSearch",
    [string]$BackendDir = "$PSScriptRoot\..\..\backend"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "$DataDir\database", "$DataDir\documents", "$DataDir\logs" | Out-Null

$python = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$env:DESKTOP_MODE = "1"
$env:TITLESEARCH_DATA_DIR = $DataDir
Push-Location $BackendDir
& $python -m playwright install chromium
Pop-Location

Write-Host "Title Search data directory: $DataDir"