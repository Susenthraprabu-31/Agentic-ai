Set-Location "d:\Client projects\skp\titlemvp\backend"
$env:PYTHONPATH = "."
$env:PLAYWRIGHT_HEADLESS = "false"
$env:PLAYWRIGHT_BROWSER_MODE = "local_visible"
$env:PLAYWRIGHT_CHANNEL = "chrome"
$env:BROWSER_LIVE_VIEW_FORCE_HEADLESS = "false"
$env:PLAYWRIGHT_SLOW_MO_MS = "300"
$env:PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS = "5"

$concurrency = if ($env:CELERY_WORKER_CONCURRENCY) { $env:CELERY_WORKER_CONCURRENCY } else { "2" }

# -P solo runs only one workflow at a time; threads allows WORKFLOW_MAX_CONCURRENT_EXECUTIONS (default 2).
& ".\.venv\Scripts\python.exe" -m celery -A app.tasks.celery_app worker -l info -P threads --concurrency=$concurrency -Q executions,ai