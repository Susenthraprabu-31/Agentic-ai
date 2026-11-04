"""Re-export celery_app for backwards compatibility and convenience."""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]

