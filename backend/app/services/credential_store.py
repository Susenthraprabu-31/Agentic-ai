"""Secure credential storage using Windows Credential Manager (keyring)."""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

SERVICE_NAME = "TitleSearchDesktop"

SUPPORTED_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    }
)


def set_secret(name: str, value: str) -> bool:
    if name not in SUPPORTED_KEYS:
        return False
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, name, value)
        return True
    except Exception as exc:
        logger.warning("credential_store.set_failed", name=name, error=str(exc))
        return False


def get_secret(name: str) -> str | None:
    if name not in SUPPORTED_KEYS:
        return None
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, name)
    except Exception:
        return None


def delete_secret(name: str) -> bool:
    if name not in SUPPORTED_KEYS:
        return False
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, name)
        return True
    except Exception:
        return False


def apply_stored_secrets_to_settings() -> None:
    """Overlay keyring values onto runtime settings (desktop startup)."""
    from app.core.config import settings

    for key in SUPPORTED_KEYS:
        val = get_secret(key)
        if not val:
            continue
        if key == "AWS_ACCESS_KEY_ID":
            settings.S3_ACCESS_KEY = val
        elif key == "AWS_SECRET_ACCESS_KEY":
            settings.S3_SECRET_KEY = val
        elif hasattr(settings, key):
            setattr(settings, key, val)