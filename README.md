# Title Search Platform

A production-ready web application for searching public property records (Assessor + Tax) across US county websites using AI-assisted recipe generation and deterministic Playwright automation.

---

## Architecture Overview

```text
titlemvp/
├── frontend/                     # React + Vite + TypeScript SPA
├── backend/                      # FastAPI + SQLAlchemy + Alembic
├── docker/                       # PostgreSQL initialization + Nginx config
├── docker-compose.yml             # Main Docker configuration
├── docker-compose.override.yml    # Development overrides (auto-loaded)
└── .env.example                  # Environment configuration template
```

### Key Design Principles

- **Recipe-first execution** — AI is only called during recipe generation, not during normal searches.
- **No hardcoded county data** — URLs, selectors, and field mappings are stored in the database.
- **Security-compliant** — Detects Cloudflare, CAPTCHA, and Turnstile and halts execution without bypassing security controls.
- **Async-first** — FastAPI, SQLAlchemy 2.0 async, and asyncpg throughout the backend.

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Docker Desktop | 24+ | Container orchestration |
| Docker Compose | v2 | Service management |
| Node.js | 20+ | Frontend development |
| Python | 3.12+ | Backend development |
| Git | Any | Version control |

---

## Quick Start — Docker

Docker is the recommended way to run the complete application.

### 1. Clone the Repository

```bash
git clone <repo-url>
cd titlemvp
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Generate a secure secret key:

```bash
openssl rand -hex 32
```

Add the generated value to your `.env` file:

```env
SECRET_KEY=<generated-secret-key>
```

Configure the remaining environment variables as required.

### 3. Start All Services

```bash
docker compose up --build
```

This starts the following services:

| Service | Port | Description |
|---|---|---|
| PostgreSQL | `5432` | Database |
| Redis | `6379` | Cache and Celery broker |
| Backend | `8000` | FastAPI application |
| Frontend | `5173` | React + Vite application |
| Celery Worker | — | Background task execution |
| Celery Beat | — | Task scheduling |

### 4. Run Database Migrations

After the services are running:

```bash
docker compose exec backend alembic upgrade head
```

### 5. Verify the Application

| Component | URL |
|---|---|
| Backend Health | http://localhost:8000/api/v1/health |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| Frontend | http://localhost:5173 |

---

## Local Development

Run the backend and frontend locally without Docker.

### Backend Setup

#### 1. Navigate to the Backend

```bash
cd backend
```

#### 2. Create a Virtual Environment

```bash
python -m venv .venv
```

#### 3. Activate the Virtual Environment

**Windows PowerShell**

```powershell
.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
source .venv/bin/activate
```

#### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

#### 5. Configure Environment Variables

```bash
cp .env.example .env
```

Configure your local database connection and other required environment variables.

Example:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/titlemvp
REDIS_URL=redis://localhost:6379/0
```

#### 6. Run Database Migrations

```bash
alembic upgrade head
```

#### 7. Start the Backend

```bash
uvicorn app.main:app --reload --port 8000
```

---

### Frontend Setup

#### 1. Navigate to the Frontend

```bash
cd frontend
```

#### 2. Install Dependencies

```bash
npm install
```

#### 3. Configure the API URL

Create `.env.local`:

```env
VITE_API_URL=http://localhost:8000/api/v1
```

#### 4. Start the Development Server

```bash
npm run dev
```

The frontend will be available at:

```text
http://localhost:5173
```

---

### Redis Setup

Redis is required for background task processing.

#### macOS

```bash
brew services start redis
```

#### Windows

Run Redis using Docker:

```bash
docker run -d \
  --name titlemvp-redis \
  -p 6379:6379 \
  redis:7-alpine
```

#### Verify Redis

```bash
redis-cli ping
```

Expected output:

```text
PONG
```

---

### Celery Worker

Start the Celery worker from the backend directory. Use **concurrency 2** so up to two workflow automations can run in parallel (matches `WORKFLOW_MAX_CONCURRENT_EXECUTIONS` in `.env`).

**Linux / macOS:**

```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info --queues=executions,ai --concurrency=2
```

**Windows (required: `threads` pool — the default `solo` pool runs one task at a time):**

```powershell
cd backend
.\.venv\Scripts\python.exe -m celery -A app.tasks.celery_app worker -l info -P threads --concurrency=2 -Q executions,ai
```

Or run `backend\start_celery.ps1`, which uses the same settings.

---

## Database Migrations

### Create a Migration

```bash
docker compose exec backend alembic revision --autogenerate -m "description"
```

### Apply Pending Migrations

```bash
docker compose exec backend alembic upgrade head
```

### Roll Back One Migration

```bash
docker compose exec backend alembic downgrade -1
```

### View Migration History

```bash
docker compose exec backend alembic history
```

---

## Environment Variables

Refer to `.env.example` for the complete list of environment variables.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL async connection URL |
| `SECRET_KEY` | Yes | JWT signing key |
| `REDIS_URL` | Yes | Redis connection URL |
| `CELERY_BROKER_URL` | Yes | Celery message broker |
| `CELERY_RESULT_BACKEND` | Yes | Celery result backend |
| `OPENAI_API_KEY` | Phase 2 | OpenAI API key for recipe generation |
| `MISTRAL_API_KEY` | Phase 2 | Mistral API key for OCR |
| `S3_*` | Phase 2 | S3-compatible storage configuration |

### Example Database URL

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/titlemvp
```

### Example Redis Configuration

```env
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
```

---

## API Documentation

API documentation is automatically generated by FastAPI.

| Documentation | URL |
|---|---|
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |

### Key API Endpoints — Phase 1

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Full health check — database and Redis |
| `GET` | `/api/v1/health/live` | Liveness probe |
| `GET` | `/api/v1/health/ready` | Readiness probe |
| `POST` | `/api/v1/auth/register` | User registration |
| `POST` | `/api/v1/auth/login` | User authentication |
| `GET` | `/api/v1/workflows` | List workflows |
| `GET` | `/api/v1/county-sources` | List county sources |

---

## Frontend Pages

| Route | Page | Phase |
|---|---|---|
| `/login` | Login | Phase 1 — UI Shell |
| `/dashboard` | Dashboard + System Health | Phase 1 |
| `/workflow-builder` | React Flow Canvas | Phase 1 — Canvas Shell |
| `/workflows` | Workflow Management | Phase 1 — Table Shell |
| `/county-sources` | County Source Management | Phase 1 — Table Shell |
| `/recipe-templates` | Recipe Template Management | Phase 1 — Table Shell |
| `/execution-history` | Execution History | Phase 1 — Table Shell |
| `/execution-results` | Assessor + Tax Results | Phase 1 — Table Shell |
| `/security-events` | Security Events Log | Phase 1 — Table Shell |
| `/settings` | Settings | Phase 1 — UI Shell |

---

## Database Entities

| Entity | Description |
|---|---|
| `users` | Application users |
| `workflows` | Named workflows |
| `workflow_versions` | Versioned workflow definitions |
| `county_sources` | County website configurations |
| `ui_templates` | Reusable automation templates |
| `template_versions` | Versioned template definitions |
| `source_configurations` | Template-to-county bindings and field mappings |
| `recipes` | Automation recipes for county and record type |
| `recipe_versions` | Versioned Playwright step sequences |
| `validation_cases` | Recipe validation test cases |
| `workflow_runs` | Workflow execution records |
| `node_runs` | Individual node execution records |
| `assessor_results` | Extracted assessor data |
| `tax_results` | Extracted tax data |
| `security_events` | Cloudflare, CAPTCHA, and Turnstile detection events |
| `ai_usage_logs` | OpenAI and Mistral usage and cost tracking |

---

## Development Phases

### Phase 1 — Foundation ✅

- Project structure
- FastAPI backend
- SQLAlchemy 2.0
- Alembic migrations
- PostgreSQL integration
- Redis integration
- Celery configuration
- Frontend application shell
- Dashboard and application pages
- Docker Compose orchestration
- Health checks

### Phase 2 — Core Backend Logic

- JWT authentication
- County Source CRUD APIs
- Recipe Template CRUD APIs
- Workflow CRUD APIs
- React Flow workflow builder
- Custom workflow nodes
- Zustand workflow state management

### Phase 3 — Automation Engine

- Playwright browser automation
- Recipe execution engine
- AI-assisted recipe generation
- Mistral OCR integration
- Security detection module

### Phase 4 — Production Features

- S3 screenshot and document storage
- AI-assisted recipe repair
- Execution history
- Results viewer
- Multi-user support
- Rate limiting
- API security enhancements

---

## Testing

### Backend Tests

```bash
cd backend
pytest tests/ -v
```

### Frontend Type Checking

```bash
cd frontend
npx tsc --noEmit
```

### Frontend Build

```bash
cd frontend
npm run build
```

---

## Troubleshooting

### PostgreSQL Connection Refused

Check whether Docker services are running:

```bash
docker compose ps
```

Check PostgreSQL health:

```bash
docker compose exec postgres pg_isready
```

### Alembic Cannot Find Models

Ensure all models are imported in:

```text
app/models/__init__.py
```

### Frontend Displays a Blank Page

- Check the browser console for errors.
- Ensure the Vite development server is running.
- Verify the frontend API URL.
- Confirm that the backend is accessible.

### Celery Tasks Are Not Running

Check Celery worker logs:

```bash
docker compose logs celery_worker
```

Verify Redis:

```bash
docker compose exec redis redis-cli ping
```

### Backend Health Check Fails

Check the backend logs:

```bash
docker compose logs backend
```

Verify database and Redis connectivity.

---

## License

Proprietary — All rights reserved.
