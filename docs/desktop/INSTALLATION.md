# Title Search Desktop — Installation

## Development

1. Backend: `cd backend`, create venv, `pip install -r requirements.txt`, `playwright install chromium`
2. Set `DESKTOP_MODE=1` and optional `TITLESEARCH_DATA_DIR=../local_data`
3. Run API: `uvicorn app.main:app --host 127.0.0.1 --port 8765`
4. Frontend: `cd frontend && npm install && npm run dev`
5. Desktop shell (all-in-one): `cd desktop && npm install && npm run dev`

   Or run backend manually from `backend` (you are already there — do not `cd backend` again):

   ```powershell
   $env:DESKTOP_MODE="1"
   $env:DESKTOP_USE_SQLITE="0"
   $env:TITLESEARCH_DATA_DIR="D:\Client projects\skp\titlemvp\local_data"
   # Uses DATABASE_URL + SECRET_KEY from backend/.env (PostgreSQL)
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
   ```

## Production installer

Build:

```bash
cd frontend && npm run build
cd ../desktop && npm install && npm run dist
```

The NSIS installer is written to `dist-desktop/` (or `dist-desktop/pack-*` if a previous build folder was locked).

Build runs `predist`, which bundles Python + dependencies into the installer (~large `.exe`).

**Customer machines** do not need Python installed. App data (SQLite DB, documents, Playwright browsers cache) lives under `%LOCALAPPDATA%\TitleSearch`.

Re-run the **new** `TitleSearchSetup-0.1.1.exe` after each `npm run dist` — do not reuse an old `TitleSearchSetup-0.1.0.exe` from an earlier build.

## Troubleshooting

- **Backend won't start**: Confirm port 8765 is free; check `logs/` under the data directory.
- **Playwright**: Run `playwright install chromium` or ensure Google Chrome is installed (`PLAYWRIGHT_CHANNEL=chrome`).
- **S3 sync offline**: Searches continue locally; pending uploads resume when connectivity returns.