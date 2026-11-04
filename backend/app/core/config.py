"""Application configuration using pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BASE_DIR / ".env"


class Settings(BaseSettings):
    # -------------------------------------------------------------------------
    # App
    # -------------------------------------------------------------------------
    APP_NAME: str = "Title Search Platform"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # -------------------------------------------------------------------------
    # API
    # -------------------------------------------------------------------------
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    DATABASE_URL: str  # required — no default
    DATABASE_ECHO: bool = False

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # -------------------------------------------------------------------------
    # Celery
    # -------------------------------------------------------------------------
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # -------------------------------------------------------------------------
    # Security / JWT
    # -------------------------------------------------------------------------
    SECRET_KEY: str  # required — no default
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    REFRESH_COOKIE_NAME: str = "titlemvp_refresh_token"
    REFRESH_COOKIE_SECURE: bool = False  # Set to True in HTTPS production
    REFRESH_COOKIE_SAMESITE: str = "lax"
    REFRESH_COOKIE_PATH: str = "/api/v1/auth"

    # -------------------------------------------------------------------------
    # OpenAI & LLM (Phase 2 & 3)
    # -------------------------------------------------------------------------
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-6-luna"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # -------------------------------------------------------------------------
    # Mistral OCR & Visual Document Understanding
    # -------------------------------------------------------------------------
    MISTRAL_API_KEY: str = ""
    MISTRAL_OCR_ENABLED: bool = True
    MISTRAL_OCR_MODEL: str = "mistral-ocr-2512"
    MISTRAL_CHAT_MODEL: str = "mistral-large-latest"

    # -------------------------------------------------------------------------
    # S3 (Phase 2)
    # -------------------------------------------------------------------------
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET_NAME: str = "titlemvp"

    # -------------------------------------------------------------------------
    # Playwright Automation Engine (Phase 3)
    # -------------------------------------------------------------------------
    PLAYWRIGHT_ENABLED: bool = True
    PLAYWRIGHT_HEADLESS: bool = False
    PLAYWRIGHT_SLOW_MO_MS: int = 300
    PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS: int = 5
    PLAYWRIGHT_BROWSER_MODE: str = "local_visible"
    PLAYWRIGHT_BROWSER: str = "chromium"
    PLAYWRIGHT_NAVIGATION_TIMEOUT_MS: int = 60000
    PLAYWRIGHT_ACTION_TIMEOUT_MS: int = 25000
    PLAYWRIGHT_EVIDENCE_DIR: str = "storage/evidence"
    PLAYWRIGHT_ALLOW_LOCAL_URLS: bool = True

    # -------------------------------------------------------------------------
    # Document & PDF Retrieval (Phase 3C)
    # -------------------------------------------------------------------------
    DOCUMENT_STORAGE_BACKEND: str = "local"
    DOCUMENT_STORAGE_DIR: str = "storage/documents"
    DOCUMENT_MAX_FILE_SIZE_BYTES: int = 26214400  # 25 MB
    DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS: int = 30

    # -------------------------------------------------------------------------
    # Resilience & Retry Engine (Phase 3D.1)
    # -------------------------------------------------------------------------
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_INITIAL_DELAY_SECONDS: float = 2.0
    RETRY_MAX_DELAY_SECONDS: float = 60.0
    RETRY_EXPONENTIAL_BASE: float = 2.0
    RETRY_JITTER_ENABLED: bool = True
    RETRY_MAX_DURATION_SECONDS: float = 300.0

    # -------------------------------------------------------------------------
    # Worker Recovery & Circuit Breakers (Phase 3D.3)
    # -------------------------------------------------------------------------
    WORKER_HEARTBEAT_INTERVAL_SECONDS: int = 15
    WORKER_HEARTBEAT_TIMEOUT_SECONDS: int = 60
    WORKER_MONITOR_INTERVAL_SECONDS: int = 20

    EXECUTION_STALE_TIMEOUT_SECONDS: int = 300
    EXECUTION_MAX_RECOVERY_ATTEMPTS: int = 3
    EXECUTION_RECOVERY_INTERVAL_SECONDS: int = 30

    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_WINDOW_SECONDS: int = 120
    CIRCUIT_BREAKER_OPEN_SECONDS: int = 60
    CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS: int = 2
    CIRCUIT_BREAKER_SUCCESS_THRESHOLD: int = 2

    # -------------------------------------------------------------------------
    # Browser & Document Download Resilience (Phase 3D.4)
    # -------------------------------------------------------------------------
    BROWSER_LAUNCH_TIMEOUT_MS: int = 30000
    BROWSER_NAVIGATION_TIMEOUT_MS: int = 60000
    BROWSER_MAX_PROCESSES_PER_WORKER: int = 2
    BROWSER_MAX_CONTEXTS_PER_WORKER: int = 5
    BROWSER_MAX_PAGES_PER_EXECUTION: int = 5
    BROWSER_CONTEXT_CLEANUP_TIMEOUT_SECONDS: int = 15
    BROWSER_MAX_SESSION_LIFETIME_SECONDS: int = 1800

    DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS: int = 120
    DOCUMENT_DOWNLOAD_MAX_SIZE_MB: int = 25
    DOCUMENT_DOWNLOAD_CHUNK_SIZE_BYTES: int = 65536
    DOCUMENT_DOWNLOAD_MAX_ATTEMPTS: int = 3
    DOCUMENT_DOWNLOAD_STALE_TIMEOUT_SECONDS: int = 300

    DOCUMENT_TEMP_CLEANUP_INTERVAL_SECONDS: int = 600
    DOCUMENT_TEMP_MAX_AGE_HOURS: int = 24
    DOCUMENT_TEMP_STORAGE_DIR: str = "tmp/downloads"

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")


# Singleton — import this throughout the application
settings = Settings()
