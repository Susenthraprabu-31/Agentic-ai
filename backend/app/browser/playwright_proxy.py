"""Playwright launch proxy configuration (e.g. Webshare residential)."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_playwright_proxy() -> dict[str, Any] | None:
    """
    Build Playwright ``proxy`` launch kwargs from settings / env.
    Returns None when WEBSHARE_PROXY_SERVER is unset (direct connection).
    """
    server = (getattr(settings, "WEBSHARE_PROXY_SERVER", "") or "").strip()
    if not server:
        return None
    proxy: dict[str, Any] = {"server": server}
    username = (getattr(settings, "WEBSHARE_PROXY_USERNAME", "") or "").strip()
    password = (getattr(settings, "WEBSHARE_PROXY_PASSWORD", "") or "").strip()
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy


def log_proxy_in_use() -> None:
    proxy = build_playwright_proxy()
    if not proxy:
        return
    server = proxy.get("server", "")
    masked_user = ""
    if proxy.get("username"):
        u = str(proxy["username"])
        masked_user = u[:3] + "***" if len(u) > 3 else "***"
    logger.info(
        "Playwright using residential proxy (server=%s, username=%s)",
        server,
        masked_user or "(none)",
    )