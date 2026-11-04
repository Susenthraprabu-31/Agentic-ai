Set-Location "d:\Client projects\skp\titlemvp\backend"
$env:PYTHONPATH = "."
$env:PLAYWRIGHT_HEADLESS = "false"
$env:PLAYWRIGHT_BROWSER_MODE = "local_visible"
$env:PLAYWRIGHT_SLOW_MO_MS = "300"
$env:PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS = "5"
& ".\.venv\Scripts\python.exe" -m celery -A app.tasks.celery_app worker -l info -P solo -Q executions,celery
