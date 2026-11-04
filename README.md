# Title Search Platform

A production-ready web application for searching public property records (Assessor + Tax) across US county websites, using AI-assisted recipe generation and deterministic Playwright automation.

---

## Architecture Overview

```
titlemvp/
├── frontend/     React + Vite + TypeScript SPA
├── backend/      FastAPI + SQLAlchemy + Alembic
├── docker/       PostgreSQL init + Nginx config
├── docker-compose.yml
├── docker-compose.override.yml   (dev overrides — auto-loaded)
└── .env.example
```

**Key design principles:**
- **Recipe-first execution** — AI is only called during recipe *generation*, not normal searches
- **No hardcoded county data** — all URLs, selectors, and field mappings live in the database
- **Security-compliant** — detects Cloudflare/CAPTCHA/Turnstile and halts (never bypasses)
- **Async-first** — FastAPI + SQLAlchemy 2.0 async + asyncpg throughout

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Docker Desktop | 24+ | Container orchestration |
| Docker Compose | v2 (bundled) | Service management |
| Node.js | 20+ | Frontend development |
| Python | 3.12+ | Backend development |
| Git | Any | Version control |

---

## Quick Start (Docker — Recommended)

### 1. Clone and configure

```bash
git clone <repo-url> titlemvp
cd titlemvp

# Create your local environment file
cp .env.example .env

# Generate a secure SECRET_KEY
openssl rand -hex 32
# Paste the output into .env as SECRET_KEY=<value>
```

### 2. Start all services

```bash
docker compose up --build
```

This starts:
- **PostgreSQL** on port `5432`
- **Redis** on port `6379`
- **Backend (FastAPI)** on port `8000`
- **Frontend (Vite)** on port `5173`
- **Celery Worker** (background tasks)
- **Celery Beat** (task scheduler)

### 3. Run database migrations

```bash
# In a separate terminal, after services are running:
docker compose exec backend alembic upgrade head
```

### 4. Verify health

```bash
# Backend health check
curl http://localhost:8000/api/v1/health

# Backend API docs
open http://localhost:8000/docs

# Frontend
open http://localhost:5173
```

---

## Local Development (Without Docker)

### Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Activate (macOS/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env — set DATABASE_URL to point to your local PostgreSQL

# Run migrations
alembic upgrade head

# Start FastAPI
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Copy environment file
echo "VITE_API_URL=http://localhost:8000/api/v1" > .env.local

# Start Vite dev server
npm run dev
```

### Redis (local)

```bash
# macOS (Homebrew)
brew services start redis

# Windows (WSL or Docker)
docker run -d -p 6379:6379 redis:7-alpine

# Verify
redis-cli ping   # should return PONG
```

### Celery Worker (local)

```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info --queues=executions,ai
```

---

## Database Migrations

```bash
# Create a new migration
docker compose exec backend alembic revision --autogenerate -m "description"

# Apply all pending migrations
docker compose exec backend alembic upgrade head

# Roll back one migration
docker compose exec backend alembic downgrade -1

# Show migration history
docker compose exec backend alembic history
```

---

## Environment Variables

See [`.env.example`](.env.example) for all available variables.

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | ✅ | JWT signing key — generate with `openssl rand -hex 32` |
| `REDIS_URL` | ✅ | Redis connection URL |
| `CELERY_BROKER_URL` | ✅ | Celery broker (Redis) |
| `CELERY_RESULT_BACKEND` | ✅ | Celery result backend (Redis) |
| `OPENAI_API_KEY` | Phase 2 | OpenAI API key for recipe generation |
| `MISTRAL_API_KEY` | Phase 2 | Mistral OCR API key |
| `S3_*` | Phase 2 | S3-compatible storage credentials |

---

## API Documentation

When the backend is running:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

### Key Endpoints (Phase 1)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Full health check (DB + Redis) |
| GET | `/api/v1/health/live` | Liveness probe (immediate 200) |
| GET | `/api/v1/health/ready` | Readiness probe (checks dependencies) |
| POST | `/api/v1/auth/register` | Register user (Phase 2) |
| POST | `/api/v1/auth/login` | Login (Phase 2) |
| GET | `/api/v1/workflows` | List workflows (Phase 2) |
| GET | `/api/v1/county-sources` | List county sources (Phase 2) |

---

## Frontend Pages

| Path | Page | Phase |
|------|------|-------|
| `/login` | Login | 1 (UI shell) |
| `/dashboard` | Dashboard + System Health | 1 |
| `/workflow-builder` | React Flow Canvas | 1 (canvas shell) |
| `/workflows` | Workflow Management | 1 (table shell) |
| `/county-sources` | County Source Management | 1 (table shell) |
| `/recipe-templates` | Recipe Template Management | 1 (table shell) |
| `/execution-history` | Execution History | 1 (table shell) |
| `/execution-results` | Assessor + Tax Results | 1 (table shell) |
| `/security-events` | Security Events Log | 1 (table shell) |
| `/settings` | Settings | 1 (UI shell) |

---

## Database Entities

| Entity | Description |
|--------|-------------|
| `users` | Application users |
| `workflows` | Named workflows |
| `workflow_versions` | Versioned workflow definitions (JSON) |
| `county_sources` | County website configurations |
| `ui_templates` | Reusable automation templates |
| `template_versions` | Versioned template definitions |
| `source_configurations` | Template-to-county bindings with field maps |
| `recipes` | Automation recipes for a county + record type |
| `recipe_versions` | Versioned Playwright step sequences (JSON) |
| `validation_cases` | Test cases for recipe validation |
| `workflow_runs` | Execution run records |
| `node_runs` | Individual node execution records |
| `assessor_results` | Extracted assessor data |
| `tax_results` | Extracted tax data |
| `security_events` | Cloudflare/CAPTCHA/Turnstile detections |
| `ai_usage_logs` | OpenAI/Mistral API usage and cost tracking |

---

## Development Phases

### Phase 1 (Current) — Foundation ✅
- Project structure
- FastAPI + SQLAlchemy + Alembic
- PostgreSQL + Redis
- Celery configuration stubs
- Frontend shell with all pages
- Docker Compose orchestration
- Health checks

### Phase 2 — Core Backend Logic
- Full JWT authentication flow
- County Source CRUD API
- Recipe Template CRUD API
- Workflow CRUD API
- React Flow workflow builder with custom nodes
- Zustand workflow state management

### Phase 3 — Automation Engine
- Playwright browser automation
- Recipe execution engine
- OpenAI recipe generation
- Mistral OCR integration
- Security detection module

### Phase 4 — Production Features
- S3 screenshot storage
- AI-controlled recipe repair
- Execution history and result viewer
- Multi-user support
- Rate limiting and API security

---

## Testing

```bash
# Backend tests
cd backend
pytest tests/ -v

# Frontend type check
cd frontend
npx tsc --noEmit

# Frontend build check
cd frontend
npm run build
```

---

## Troubleshooting

**PostgreSQL connection refused**
- Ensure Docker is running: `docker compose ps`
- Check pg health: `docker compose exec postgres pg_isready`

**Alembic can't find models**
- Ensure all models are imported in `app/models/__init__.py`

**Frontend shows blank page**
- Check browser console for errors
- Ensure Vite dev server is running on port 5173
- Check `VITE_API_URL` is set correctly in `.env`

**Celery tasks not running**
- Check worker logs: `docker compose logs celery_worker`
- Verify Redis is running: `docker compose exec redis redis-cli ping`

---

## License

Proprietary — All rights reserved.

#   A g e n t i c - a i  
 