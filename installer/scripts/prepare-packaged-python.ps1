# Build-time: isolated backend venv + minimal stdlib for the NSIS installer (avoids bloating with global site-packages).
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$backendDir = Join-Path $repoRoot "backend"
$venvPy = Join-Path $backendDir ".venv\Scripts\python.exe"
$requirements = Join-Path $backendDir "requirements.txt"
$outRoot = Join-Path $repoRoot "installer\packaged-python"
$embedDir = Join-Path $outRoot "embed"
$marker = Join-Path $outRoot ".prepare-done"
$runtimePy = Join-Path $embedDir "Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    throw "Backend venv not found. Run: cd backend; python -m venv .venv; pip install -r requirements.txt"
}
if (-not (Test-Path $requirements)) {
    throw "Missing requirements.txt"
}

$cfg = Get-Content (Join-Path $backendDir ".venv\pyvenv.cfg")
$homeLine = ($cfg | Where-Object { $_ -match '^\s*home\s*=' }) | Select-Object -First 1
if (-not $homeLine) {
    throw "Could not read home= from backend\.venv\pyvenv.cfg"
}
$pythonHome = ($homeLine -split '=', 2)[1].Trim()

$reqTime = (Get-Item $requirements).LastWriteTimeUtc.Ticks
if ((Test-Path $marker) -and (Test-Path $runtimePy)) {
    $markerText = Get-Content $marker -Raw
    if ($markerText -match "requirements_ticks=$reqTime") {
        Write-Host "[prepare-packaged-python] Up to date: $embedDir"
        exit 0
    }
}

Write-Host "[prepare-packaged-python] Creating portable venv at $embedDir"
if (Test-Path $outRoot) {
    Remove-Item -Recurse -Force $outRoot
}
New-Item -ItemType Directory -Force -Path $embedDir | Out-Null

& $venvPy -m venv $embedDir --copies
if (-not (Test-Path $runtimePy)) {
    throw "Failed to create packaged venv"
}

$baseDir = Join-Path $embedDir "python-base"
New-Item -ItemType Directory -Force -Path $baseDir | Out-Null
$stdlibItems = @("python.exe", "pythonw.exe", "python3.dll", "python313.dll", "python313.zip", "vcruntime140.dll", "vcruntime140_1.dll")
foreach ($item in $stdlibItems) {
    $src = Join-Path $pythonHome $item
    if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination $baseDir -Force
    }
}
robocopy (Join-Path $pythonHome "DLLs") (Join-Path $baseDir "DLLs") /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy DLLs failed" }
robocopy (Join-Path $pythonHome "Lib") (Join-Path $baseDir "Lib") /E /NFL /NDL /NJH /NJS /XD "site-packages" "test" "tests" "idlelib" "tkinter" "turtledemo" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy Lib failed" }

$pyvenvPath = Join-Path $embedDir "pyvenv.cfg"
$versionLine = ($cfg | Where-Object { $_ -match '^\s*version\s*=' }) | Select-Object -First 1
$version = if ($versionLine) { ($versionLine -split '=', 2)[1].Trim() } else { "3.13.0" }
# Relative home breaks when Electron cwd is resources\backend; main.cjs patches to absolute at launch.
Set-Content -Path $pyvenvPath -Value @(
    "home = $baseDir"
    "include-system-site-packages = false"
    "version = $version"
    "executable = $runtimePy"
    "command = $venvPy -m venv $embedDir"
)

Write-Host "[prepare-packaged-python] Installing requirements only (smaller than full system Python)..."
$env:PYTHONNOUSERSITE = "1"
& $runtimePy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
& $runtimePy -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed with exit code $LASTEXITCODE" }
& $runtimePy -c "import fastapi, uvicorn, sqlalchemy, playwright"
if ($LASTEXITCODE -ne 0) { throw "Packaged runtime import smoke test failed" }
Write-Host "[prepare-packaged-python] Installing Playwright Chromium..."
& $runtimePy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed with exit code $LASTEXITCODE" }

Set-Content -Path $marker -Value "requirements_ticks=$reqTime"
Write-Host "[prepare-packaged-python] Ready: $runtimePy"