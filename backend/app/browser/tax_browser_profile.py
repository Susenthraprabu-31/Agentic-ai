"""
Dedicated Miami-Dade tax browser profile (persistent user_data_dir + policy file).
Workers import storage_state from auth.json; profile is created on a desktop via the helper script.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from app.browser.miami_dade_tax_auth import resolve_miami_dade_tax_auth_path
from app.browser.security_clearance import tax_portal_search_ready
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MIAMI_DADE_TAX_PORTAL_URL = "https://county-taxes.net/fl-miamidade/property-tax"

_DEFAULT_POLICY: dict[str, Any] = {
    "viewport": {"width": 1280, "height": 800},
    "locale": "en-US",
    "timezone_id": "America/New_York",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "ignore_default_args": ["--enable-automation"],
    "args": [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ],
}


def backend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def resolve_profile_dir() -> Path:
    rel = getattr(settings, "TAX_BROWSER_PROFILE_DIR", "storage/browser_profiles/miami_dade_tax")
    p = Path(rel)
    if not p.is_absolute():
        p = backend_root() / rel
    return p


def resolve_policy_file() -> Path:
    rel = getattr(settings, "TAX_BROWSER_POLICY_FILE", "config/tax_browser_policy.json")
    p = Path(rel)
    if not p.is_absolute():
        p = backend_root() / rel
    return p


def load_tax_browser_policy() -> dict[str, Any]:
    policy = dict(_DEFAULT_POLICY)
    policy_path = resolve_policy_file()
    if policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                policy.update(loaded)
        except Exception as exc:
            logger.warning("Could not load tax browser policy %s: %s", policy_path, exc)
    extra = (getattr(settings, "PLAYWRIGHT_EXTRA_LAUNCH_ARGS", "") or "").strip()
    if extra:
        extra_args = [a.strip() for a in extra.split(",") if a.strip()]
        policy.setdefault("args", [])
        policy["args"] = list(policy["args"]) + extra_args
    return policy


def resolve_auth_output_path() -> Path:
    auth = resolve_miami_dade_tax_auth_path()
    if auth:
        return auth
    rel = getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "auth.json")
    p = Path(rel)
    if not p.is_absolute():
        p = backend_root() / rel
    return p


def profile_exists() -> bool:
    d = resolve_profile_dir()
    return d.is_dir() and any(d.iterdir()) if d.exists() else False


def should_apply_tax_browser_context() -> bool:
    if not getattr(settings, "MIAMI_DADE_TAX_AUTH_ENABLED", True):
        return False
    return bool(resolve_miami_dade_tax_auth_path() or profile_exists())


def get_tax_browser_context_options(
    *,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
) -> dict[str, Any]:
    """Options merged into BrowserManager.new_context for tax portal runs."""
    policy = load_tax_browser_policy()
    vp = policy.get("viewport") if isinstance(policy.get("viewport"), dict) else {}
    opts: dict[str, Any] = {
        "viewport": {
            "width": int(vp.get("width") or viewport_width),
            "height": int(vp.get("height") or viewport_height),
        },
        "locale": str(policy.get("locale") or locale),
        "timezone_id": str(policy.get("timezone_id") or timezone_id),
        "ignore_https_errors": True,
        "accept_downloads": True,
    }
    ua = policy.get("user_agent")
    if ua:
        opts["user_agent"] = str(ua)
    auth_path = resolve_miami_dade_tax_auth_path()
    if auth_path:
        opts["storage_state"] = str(auth_path)
    return opts


def get_persistent_context_launch_kwargs(*, headless: bool = False) -> dict[str, Any]:
    policy = load_tax_browser_policy()
    channel = (getattr(settings, "PLAYWRIGHT_CHANNEL", "") or "chrome").strip() or None
    kwargs: dict[str, Any] = {
        "headless": headless,
        "slow_mo": int(getattr(settings, "PLAYWRIGHT_SLOW_MO_MS", 0) or 200),
        "locale": str(policy.get("locale") or "en-US"),
        "timezone_id": str(policy.get("timezone_id") or "America/New_York"),
        "viewport": policy.get("viewport") or {"width": 1280, "height": 800},
        "args": list(policy.get("args") or []),
        "ignore_default_args": list(policy.get("ignore_default_args") or ["--enable-automation"]),
    }
    ua = policy.get("user_agent")
    if ua:
        kwargs["user_agent"] = str(ua)
    if channel:
        kwargs["channel"] = channel
    return kwargs


async def open_tax_browser_for_manual_setup(
    *,
    portal_url: str = MIAMI_DADE_TAX_PORTAL_URL,
    wait_timeout_sec: float = 900.0,
    poll_sec: float = 2.0,
    output_path: Path | None = None,
) -> tuple[Path, Path]:
    """
    Opens a persistent Chrome profile on a machine with a desktop, waits for tax search UI,
    exports auth.json for workers.
    """
    profile_dir = resolve_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    auth_out = output_path or resolve_auth_output_path()
    auth_out.parent.mkdir(parents=True, exist_ok=True)

    launch_kw = get_persistent_context_launch_kwargs(headless=False)
    deadline = time.monotonic() + wait_timeout_sec

    async with async_playwright() as pw:
        logger.info("Opening tax browser profile at %s → %s", profile_dir, portal_url)
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            **launch_kw,
        )
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        await page.goto(portal_url, wait_until="domcontentloaded", timeout=120_000)

        while time.monotonic() < deadline:
            if await tax_portal_search_ready(page):
                break
            await asyncio.sleep(poll_sec)

        await context.storage_state(path=str(auth_out))
        logger.info("Saved tax storage_state to %s", auth_out)
        await context.close()

    return profile_dir, auth_out


def _rel_hint(path: Path) -> str:
    try:
        return str(path.relative_to(backend_root()))
    except ValueError:
        return str(path)


def profile_status_metadata() -> dict[str, Any]:
    profile_dir = resolve_profile_dir()
    auth_path = resolve_miami_dade_tax_auth_path()
    auth_mtime: str | None = None
    if auth_path and auth_path.is_file():
        auth_mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(auth_path.stat().st_mtime))
    profile_mtime: str | None = None
    if profile_dir.is_dir():
        try:
            profile_mtime = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(profile_dir.stat().st_mtime),
            )
        except Exception:
            pass
    return {
        "profile_dir": profile_dir.name if profile_dir else "",
        "profile_configured": profile_exists(),
        "profile_path_hint": _rel_hint(profile_dir),
        "profile_updated_at": profile_mtime,
        "auth_updated_at": auth_mtime,
        "policy_file": resolve_policy_file().name if resolve_policy_file().is_file() else None,
    }