# First-launch backend runtime: venv in %LOCALAPPDATA%\TitleSearch\runtime\venv
param(
    [Parameter(Mandatory = $true)][string]$BackendSource,
    [Parameter(Mandatory = $true)][string]$VenvDir,
    [Parameter(Mandatory = $true)][string]$SystemPython
)

$ErrorActionPreference = "Stop"
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path $venvPython) {
    exit 0
}

if (-not (Test-Path $BackendSource)) {
    throw "Backend source not found: $BackendSource"
}
$requirements = Join-Path $BackendSource "requirements.txt"
if (-not (Test-Path $requirements)) {
    throw "requirements.txt not found: $requirements"
}
if (-not (Test-Path $SystemPython)) {
    throw "System Python not found: $SystemPython"
}

Write-Host "[bootstrap] Creating venv at $VenvDir"
New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir) | Out-Null
& $SystemPython -m venv $VenvDir
if (-not (Test-Path $venvPython)) {
    throw "venv creation failed"
}

Write-Host "[bootstrap] Installing Python dependencies (this can take a few minutes)..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r $requirements
Write-Host "[bootstrap] Installing Playwright Chromium..."
& $venvPython -m playwright install chromium
Write-Host "[bootstrap] Done."