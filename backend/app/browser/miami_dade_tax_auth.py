"""Load saved Miami-Dade county tax portal session (cookies + web storage) from auth.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_AUTH_PATH = Path(__file__).resolve().parent.parent / "auth.json"
_applied_context_ids: set[int] = set()
_SAMESITE_MAP = {"strict": "Strict", "lax": "Lax", "none": "None"}


def is_miami_dade_tax_portal(url: str) -> bool:
    """True only for Miami-Dade property tax collector (county-taxes / GovHub), not assessor GIS."""
    u = (url or "").strip().lower()
    if "fl-miamidade" not in u and "miami-dade" not in u and "miamidade" not in u:
        return False
    if not any(
        k in u
        for k in (
            "county-taxes.net",
            "county-taxes.com",
            "property-tax",
            "govhub",
            "taxsys",
        )
    ):
        return False
    # Assessor PropertySearch is not the tax portal bundle target
    if "propertysearch" in u or "miamidade.gov/apps" in u:
        return False
    return True


def resolve_auth_file_path() -> Path:
    raw = (getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_absolute():
            return p
        # Project root is parent of backend/
        backend_dir = Path(__file__).resolve().parent.parent.parent
        return (backend_dir / raw).resolve()
    return _DEFAULT_AUTH_PATH


def load_miami_dade_tax_auth() -> dict[str, Any] | None:
    if not getattr(settings, "MIAMI_DADE_TAX_AUTH_ENABLED", True):
        return None
    path = resolve_auth_file_path()
    if not path.is_file():
        logger.warning("Miami-Dade tax auth file not found: %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read Miami-Dade tax auth file %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def playwright_cookies_from_auth(raw_cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in raw_cookies or []:
        if not c.get("name") or c.get("value") is None:
            continue
        cookie: dict[str, Any] = {
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": str(c.get("domain") or "county-taxes.net"),
            "path": str(c.get("path") or "/"),
        }
        expires = c.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = float(expires)
        if "httpOnly" in c:
            cookie["httpOnly"] = bool(c["httpOnly"])
        if "secure" in c:
            cookie["secure"] = bool(c["secure"])
        same = c.get("sameSite")
        if isinstance(same, str):
            key = same.strip().lower()
            if key in _SAMESITE_MAP:
                cookie["sameSite"] = _SAMESITE_MAP[key]
        out.append(cookie)
    return out


def miami_dade_tax_seed_url(auth: dict[str, Any], portal_url: str = "") -> str:
    for candidate in (
        portal_url,
        auth.get("url") or "",
        "https://county-taxes.net/fl-miamidade/property-tax/",
    ):
        c = (candidate or "").strip()
        if not c:
            continue
        parsed = urlparse(c)
        if parsed.scheme and parsed.netloc:
            base = f"{parsed.scheme}://{parsed.netloc}"
            if "/fl-miamidade/property-tax" in parsed.path.lower():
                return f"{base}/fl-miamidade/property-tax/"
            return c
    return "https://county-taxes.net/fl-miamidade/property-tax/"


async def _inject_web_storage(page: Page, auth: dict[str, Any]) -> None:
    local_storage = auth.get("local_storage") or {}
    session_storage = auth.get("session_storage") or {}
    if not isinstance(local_storage, dict):
        local_storage = {}
    if not isinstance(session_storage, dict):
        session_storage = {}
    if not local_storage and not session_storage:
        return
    await page.evaluate(
        """([ls, ss]) => {
            for (const [k, v] of Object.entries(ls || {})) {
                try { window.localStorage.setItem(k, String(v)); } catch (_) {}
            }
            for (const [k, v] of Object.entries(ss || {})) {
                try { window.sessionStorage.setItem(k, String(v)); } catch (_) {}
            }
        }""",
        [local_storage, session_storage],
    )


async def apply_miami_dade_tax_auth(
    page: Page,
    portal_url: str = "",
    *,
    force: bool = False,
) -> bool:
    """
    Apply cookies, localStorage, and sessionStorage for Miami-Dade tax portal only.
    Runs once per browser context unless force=True.
    """
    check_url = portal_url or (page.url if page and not page.is_closed() else "")
    if not is_miami_dade_tax_portal(check_url):
        return False

    auth = load_miami_dade_tax_auth()
    if not auth:
        return False

    context: BrowserContext = page.context
    ctx_id = id(context)
    if not force and ctx_id in _applied_context_ids:
        return True

    cookies = playwright_cookies_from_auth(auth.get("cookies") or [])
    if cookies:
        try:
            await context.add_cookies(cookies)
            logger.info(
                "Loaded %d cookie(s) for Miami-Dade tax portal from auth file",
                len(cookies),
            )
        except Exception as exc:
            logger.warning("Miami-Dade tax auth: add_cookies failed: %s", exc)

    seed_url = miami_dade_tax_seed_url(auth, portal_url or check_url)
    try:
        await page.goto(seed_url, wait_until="domcontentloaded", timeout=settings.BROWSER_NAVIGATION_TIMEOUT_MS)
        await page.wait_for_timeout(800)
    except Exception as exc:
        logger.warning("Miami-Dade tax auth seed navigation failed (%s): %s", seed_url, exc)

    try:
        await _inject_web_storage(page, auth)
        logger.info(
            "Injected Miami-Dade tax portal localStorage (%d keys) and sessionStorage (%d keys)",
            len(auth.get("local_storage") or {}),
            len(auth.get("session_storage") or {}),
        )
    except Exception as exc:
        logger.warning("Miami-Dade tax web storage injection failed: %s", exc)

    _applied_context_ids.add(ctx_id)
    return True


async def ensure_miami_dade_tax_auth_for_navigation(page: Page, target_url: str) -> None:
    """Called before navigating to Miami-Dade tax URLs."""
    if not is_miami_dade_tax_portal(target_url):
        return
    await apply_miami_dade_tax_auth(page, portal_url=target_url)