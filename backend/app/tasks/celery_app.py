"""Celery application factory."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "titlemvp",
    include=[
        "app.tasks.execution_tasks",
        "app.tasks.recovery_tasks",
        "app.tasks.ai_tasks",
    ],
)

# Pull CELERY_* prefixed settings from our Settings object
celery_app.config_from_object(settings, namespace="CELERY")

celery_app.conf.task_routes = {
    "app.tasks.execution_tasks.*": {"queue": "executions"},
    "app.tasks.recovery_tasks.*": {"queue": "executions"},
    "app.tasks.ai_tasks.*": {"queue": "ai"},
}

celery_app.conf.beat_schedule = {
    "worker-heartbeat-check": {
        "task": "worker_heartbeat_check",
        "schedule": float(settings.WORKER_MONITOR_INTERVAL_SECONDS),
    },
    "recover-orphaned-workflows": {
        "task": "recover_orphaned_workflows",
        "schedule": float(settings.EXECUTION_RECOVERY_INTERVAL_SECONDS),
    },
    "cleanup-download-temp-files": {
        "task": "cleanup_download_temp_files",
        "schedule": float(settings.DOCUMENT_TEMP_CLEANUP_INTERVAL_SECONDS),
    },
    "recover-stale-document-downloads": {
        "task": "recover_stale_document_downloads",
        "schedule": float(settings.DOCUMENT_DOWNLOAD_STALE_TIMEOUT_SECONDS),
    },
}

celery_app.conf.update(
    task_default_queue="executions",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
)
