"""
County-taxes.net / Cloudflare-style human verification (Performing security verification).

Selectors and flow are aligned with Tax_Verification_HTML_CSS_JS/tax-verification-design
(index.html #verification-action, .verification-action — "Verify you are human").
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from playwright.async_api import Frame, Page

logger = logging.getLogger(__name__)

# DOM contract from tax-verification-design/index.html (+ common Cloudflare variants)
VERIFICATION_ACTION_SELECTORS: tuple[str, ...] = (
    "#verification-action",
    ".verification-action",
    "button.verification-action",
    "#verification-widget button[type='button']",
    "#challenge-form input[type='checkbox']",
    "#challenge-form label",
    ".cf-browser-verification input[type='checkbox']",
    ".cf-turnstile",
    "iframe[src*='challenges.cloudflare.com']",
)

VERIFICATION_BODY_MARKERS: tuple[str, ...] = (
    "performing security verification",
    "verify you are human",
    "verifies you are not a bot",
    "security service to protect against malicious bots",
)

VERIFICATION_TITLE_MARKERS: tuple[str, ...] = (
    "performing security verification",
    "just a moment",
    "attention required",
)


async def _body_matches_verification(page: Page) -> bool:
    try:
        body = (await page.locator("body").inner_text(timeout=4000) or "").lower()
    except Exception:
        body = ""
    return any(m in body for m in VERIFICATION_BODY_MARKERS)


async def is_county_taxes_verification_page(page: Page | None) -> bool:
    if not page or page.is_closed():
        return False
    try:
        title = (await page.title() or "").lower()
    except Exception:
        title = ""
    if any(m in title for m in VERIFICATION_TITLE_MARKERS):
        return True
    if await _body_matches_verification(page):
        return True
    for sel in ("#verification-widget", "#verification-action", ".verification-action"):
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=800):
                return True
        except Exception:
            continue
    return False


async def _click_verification_in_scope(scope: Page | Frame) -> bool:
    text_buttons = (
        "button:has-text('Verify you are human')",
        "button:has-text('Verify')",
        "[role='button']:has-text('Verify you are human')",
    )
    for sel in (*VERIFICATION_ACTION_SELECTORS, *text_buttons):
        try:
            loc = scope.locator(sel).first
            if await loc.count() == 0:
                continue
            if not await loc.is_visible(timeout=1200):
                continue
            if "iframe" in sel:
                box = await loc.bounding_box(timeout=2000)
                if box:
                    page = scope.page if hasattr(scope, "page") else scope  # type: ignore[assignment]
                    if isinstance(page, Page):
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        await page.mouse.click(cx, cy)
                        await page.wait_for_timeout(400)
                try:
                    await loc.click(force=True, timeout=3000)
                except Exception:
                    pass
            else:
                await loc.click(timeout=4000)
            logger.info("Clicked county-taxes verification control: %s", sel)
            return True
        except Exception:
            continue
    return False


async def attempt_county_taxes_human_verification(
    page: Page,
    *,
    max_wait_sec: float = 45.0,
    poll_sec: float = 1.0,
) -> bool:
    """
    When the Cloudflare / county-taxes human checkbox is visible, click it and wait
    for the challenge to clear or the portal search UI to appear.
    Returns True if the verification screen is no longer blocking.
    """
    if page.is_closed():
        return False

    if not await is_county_taxes_verification_page(page):
        from app.browser.security_detector import SecurityDetector

        sec = await SecurityDetector.inspect_page(page)
        if not sec.is_detected:
            return False

    clicked = await _click_verification_in_scope(page)
    if not clicked:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if await _click_verification_in_scope(frame):
                clicked = True
                break

    if not clicked:
        logger.warning("County-taxes verification UI detected but no clickable control found")
        return False

    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        await page.wait_for_timeout(int(poll_sec * 1000))
        if page.is_closed():
            return False
        still_challenge = await is_county_taxes_verification_page(page)
        if not still_challenge:
            logger.info("County-taxes human verification cleared")
            return True
        try:
            if await page.locator("input[type='search'], input[placeholder*='Folio' i], input[placeholder*='Account' i]").first.is_visible(timeout=500):
                logger.info("Tax search inputs visible after verification click")
                return True
        except Exception:
            pass
        widget = page.locator("#verification-widget")
        try:
            if await widget.count() > 0:
                state = await widget.get_attribute("data-state")
                if state == "verified":
                    await page.wait_for_timeout(2000)
                    continue
        except Exception:
            pass

    return not await is_county_taxes_verification_page(page)


def county_taxes_security_diagnostic_steps() -> list[dict[str, Any]]:
    """Playwright steps mirroring tax-verification-design interaction + Turnstile fallbacks."""
    return [
        {
            "action": "wait",
            "duration": 1500,
            "description": "Let county-taxes security verification render",
        },
        {
            "action": "wait_for_selector",
            "selector": (
                "#verification-action, .verification-action, "
                "button:has-text('Verify you are human'), "
                ".cf-turnstile, iframe[src*='challenges.cloudflare.com']"
            ),
            "state": "visible",
            "timeout_ms": 25000,
            "optional": False,
        },
        {
            "action": "click_if_visible",
            "selector": "#verification-action, .verification-action",
            "description": "Verify you are human (county-taxes widget)",
            "optional": True,
        },
        {
            "action": "click_if_visible",
            "selector": "button:has-text('Verify you are human')",
            "description": "Verify you are human (text match)",
            "optional": True,
        },
        {
            "action": "click_if_visible",
            "selector": ".cf-turnstile, iframe[src*='challenges.cloudflare.com']",
            "description": "Activate Cloudflare Turnstile",
            "optional": True,
        },
        {
            "action": "wait",
            "duration": 6000,
            "description": "Wait for verification to complete",
        },
    ]