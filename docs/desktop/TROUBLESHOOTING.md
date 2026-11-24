# Title Search Desktop — Troubleshooting

## `spawn python ENOENT` after installing the .exe

The packaged app does **not** ship a full Python install. Install **Python 3.11+** with “Add to PATH”, then launch Title Search again so first-run bootstrap can create `%LOCALAPPDATA%\TitleSearch\runtime\venv`.

## Workflow stuck in Queued

Confirm `JOB_RUNNER_ENABLED=true` (automatic when `DESKTOP_MODE=1`). Restart the application; the local job runner polls queued runs every second.

## Tax / browser node fails immediately (“Browser automation error”)

On Windows, Playwright needs **ProactorEventLoop** (subprocess support). Desktop dev starts the API via `python -m app.desktop_server` so uvicorn `--reload` does not force SelectorEventLoop. **Restart** with `npm run dev` in `desktop/` after changes.

If the backend exits immediately, check the terminal for startup errors (do not use `uvicorn ... --loop none` on the CLI — that flag is only supported through `app.desktop_server`).

If it still fails, install browsers in the backend venv:

```powershell
cd backend
.\.venv\Scripts\playwright.exe install chromium
```

## Live browser view blank

Desktop mode uses in-process frame storage (no Redis). Ensure the run status is `RUNNING` and `BROWSER_LIVE_VIEW_ENABLED=true`. A **404** on `browser-live-view` is normal until Playwright has started and published a frame.

## Database errors after upgrade

Delete only if instructed by support: `%LOCALAPPDATA%\TitleSearch\database\titlesearch.db`. Normal upgrades run `create_all` on startup for new tables.

## Security challenge (Cloudflare / CAPTCHA)

Complete verification in the browser window or live view, then confirm in the execution UI. The platform does not bypass challenges.

## Credentials

Store API keys via **Settings** (superuser) → desktop secrets API, or Windows Credential Manager service `TitleSearchDesktop`.