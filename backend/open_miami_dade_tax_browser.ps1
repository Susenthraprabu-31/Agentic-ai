Set-Location (Join-Path $PSScriptRoot ".")
$env:PYTHONPATH = "."
$env:PLAYWRIGHT_HEADLESS = "false"
$env:PLAYWRIGHT_BROWSER_MODE = "local_visible"
$env:PLAYWRIGHT_CHANNEL = "chrome"
& ".\.venv\Scripts\python.exe" ".\scripts\open_miami_dade_tax_browser.py"