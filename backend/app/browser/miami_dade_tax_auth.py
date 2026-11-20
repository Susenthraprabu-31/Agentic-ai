"""Miami-Dade county-taxes.net Playwright storage state (cookies + local storage)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def resolve_miami_dade_tax_auth_path() -> Path | None:
    if not getattr(settings, "MIAMI_DADE_TAX_AUTH_ENABLED", True):
        return None
    rel = (getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "auth.json") or "auth.json").strip()
    backend_root = Path(__file__).resolve().parent.parent.parent
    path = Path(rel) if Path(rel).is_absolute() else backend_root / rel
    if path.is_file():
        return path
    logger.debug("Miami-Dade tax auth file not found at %s (export cookies from a real Chrome login).", path)
    return None