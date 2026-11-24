# Title Search Desktop — Architecture Assessment

Summary of the web platform and the target Windows desktop architecture. See the project migration plan for phased implementation.

## Current platform (web)

- **Frontend**: React 19 + Vite + TypeScript + React Flow (`frontend/`)
- **API**: FastAPI (`backend/app/`)
- **Database**: PostgreSQL + asyncpg, 12 Alembic revisions, 17 ORM models
- **Jobs**: Celery + Redis (executions, ai queues; Beat for recovery)
- **Automation**: Playwright Python — recipe-first assessor/tax workflows
- **Documents**: Local filesystem (`DocumentStorage`); S3 settings present but backend not implemented
- **Auth**: JWT + HttpOnly refresh cookies; owner-based workflow access + `is_superuser`

## Desktop target

- **Shell**: Electron (main + preload + packaged React build)
- **Backend**: Same FastAPI app, bound to `127.0.0.1`, spawned by Electron
- **Database**: SQLite (WAL) under `%LOCALAPPDATA%/TitleSearch/` or `TITLESEARCH_DATA_DIR`
- **Jobs**: `LocalJobRunner` (asyncio) replaces Celery when `JOB_RUNNER_ENABLED=true`
- **IPC**: In-process store replaces Redis for live view, input, cancel signals
- **Cloud**: AWS S3 only for document sync (optional offline operation)

## Configuration contract (`DESKTOP_MODE`)

| Variable | Desktop default |
|----------|-----------------|
| `DESKTOP_MODE` | `true` |
| `TITLESEARCH_DATA_DIR` | `%LOCALAPPDATA%/TitleSearch` |
| `DATABASE_URL` | `sqlite+aiosqlite:///{data_dir}/database/titlesearch.db` |
| `CELERY_ENABLED` | `false` |
| `JOB_RUNNER_ENABLED` | `true` |
| `USE_REDIS` | `false` |
| `API_BIND_HOST` | `127.0.0.1` |
| `ALLOWED_ORIGINS` | `http://127.0.0.1:*`, `file://` |
| `DOCUMENT_STORAGE_DIR` | `{data_dir}/documents` |
| `WEBSHARE_PROXY_*` | empty (no proxy by default) |

Secrets (`SECRET_KEY`, API keys) are loaded from `{data_dir}/config/` or Windows Credential Manager via the settings API.

## Reuse matrix

| Keep | Replace / new |
|------|----------------|
| UI pages, workflow engine, browser/*, extraction/* | Celery/Redis job dispatch |
| Idempotency, recovery, security detector | PostgreSQL (desktop SQLite) |
| FastAPI routes and services | Electron shell, installer, S3 backend |

## Risks

1. SQLite migration compatibility — desktop bootstrap uses `create_all` + Alembic stamp when dialect is SQLite.
2. Playwright must run in the interactive user session (not Session 0).
3. Python runtime packaging in the Windows installer.