"""OpenAI-first chat completions with automatic Groq fallback on quota/rate-limit errors."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_FALLBACK_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "insufficient_quota",
    "credit_balance_exhausted",
    "you exceeded your current quota",
    "no credits remaining",
    "credits remaining",
    "billing",
    "quota",
)


def should_fallback_to_groq(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _FALLBACK_MARKERS)


def llm_configured() -> bool:
    return bool(settings.OPENAI_API_KEY or settings.GROQ_API_KEY)


def _get_groq_client(api_key: str) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")


def _get_openai_client(api_key: str) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def _groq_create_kwargs(create_kwargs: dict[str, Any], groq_model: str) -> dict[str, Any]:
    kwargs = dict(create_kwargs)
    kwargs["model"] = groq_model
    if "max_completion_tokens" in kwargs and "max_tokens" not in kwargs:
        kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
    return kwargs


def _openai_create_kwargs(create_kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(create_kwargs)
    # Convert max_tokens -> max_completion_tokens for modern OpenAI models (gpt-4o-mini, o3-mini, o1)
    if "max_tokens" in kwargs and "max_completion_tokens" not in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
    model = str(kwargs.get("model", "")).lower()
    # Newer reasoning and luna models (o1, o3, gpt-6, luna) only accept default temperature
    if any(m in model for m in ("o1", "o3", "gpt-6", "luna")):
        kwargs.pop("temperature", None)
    return kwargs


def _create_with_groq_fallback(client: Any | None, create_kwargs: dict[str, Any]) -> tuple[Any, str]:
    model_label = str(create_kwargs.get("model", "openai"))
    groq_model = getattr(settings, "GROQ_MODEL", None) or "llama-3.3-70b-versatile"

    # If no OpenAI client or key, use Groq directly if available
    if not client or not settings.OPENAI_API_KEY:
        if settings.GROQ_API_KEY:
            groq_client = _get_groq_client(settings.GROQ_API_KEY)
            groq_kwargs = _groq_create_kwargs(create_kwargs, groq_model)
            response = groq_client.chat.completions.create(**groq_kwargs)
            return response, f"groq:{groq_model}"
        raise ValueError("Neither OPENAI_API_KEY nor GROQ_API_KEY is configured.")

    try:
        openai_kwargs = _openai_create_kwargs(create_kwargs)
        try:
            response = client.chat.completions.create(**openai_kwargs)
            return response, model_label
        except Exception as api_err:
            err_str = str(api_err).lower()
            # Handle temperature incompatibility dynamically
            if "temperature" in err_str and "temperature" in openai_kwargs:
                logger.info("OpenAI rejected custom temperature (%s); retrying with default temperature...", api_err)
                openai_kwargs.pop("temperature", None)
                response = client.chat.completions.create(**openai_kwargs)
                return response, model_label
            # Handle max_tokens / max_completion_tokens incompatibility dynamically
            if "max_tokens" in err_str and "max_tokens" in openai_kwargs:
                openai_kwargs["max_completion_tokens"] = openai_kwargs.pop("max_tokens")
                response = client.chat.completions.create(**openai_kwargs)
                return response, model_label
            if "max_completion_tokens" in err_str and "max_completion_tokens" in openai_kwargs:
                openai_kwargs["max_tokens"] = openai_kwargs.pop("max_completion_tokens")
                response = client.chat.completions.create(**openai_kwargs)
                return response, model_label
            raise
    except Exception as exc:
        if not settings.GROQ_API_KEY or not should_fallback_to_groq(exc):
            raise
        logger.warning(
            "OpenAI chat completion failed (%s). Falling back to Groq model=%s",
            exc,
            groq_model,
        )
        groq_client = _get_groq_client(settings.GROQ_API_KEY)
        groq_kwargs = _groq_create_kwargs(create_kwargs, groq_model)
        response = groq_client.chat.completions.create(**groq_kwargs)
        return response, f"groq:{groq_model}"


def chat_completions_create_sync(
    *,
    openai_client: Any | None = None,
    create_kwargs: dict[str, Any],
) -> tuple[Any, str]:
    client = openai_client
    if client is None and settings.OPENAI_API_KEY:
        client = _get_openai_client(settings.OPENAI_API_KEY)
    return _create_with_groq_fallback(client, create_kwargs)


async def chat_completions_create(
    *,
    openai_client: Any | None = None,
    create_kwargs: dict[str, Any],
) -> tuple[Any, str]:
    client = openai_client
    if client is None and settings.OPENAI_API_KEY:
        client = _get_openai_client(settings.OPENAI_API_KEY)
    return await asyncio.to_thread(_create_with_groq_fallback, client, create_kwargs)

