"""Heuristics for whether automation can resume after Cloudflare / manual verification."""

from __future__ import annotations

from playwright.async_api import Page

from app.browser.security_detector import SecurityDetectionResult, SecurityDetector


async def tax_portal_search_ready(page: Page) -> bool:
    try:
        from app.extraction.tax_collector_reports import _tax_search_input_visible

        return await _tax_search_input_visible(page, None, 1200)
    except Exception:
        return False


async def automation_may_resume(page: Page, portal_url: str | None = None) -> bool:
    if page.is_closed():
        return False
    if await tax_portal_search_ready(page):
        return True
    try:
        body = (await page.locator("body").inner_text(timeout=2500) or "").lower()
        url = (page.url or "").lower()
        if "county-taxes" in url and "performing security verification" not in body:
            if any(k in body for k in ("folio", "account", "property tax", "search")):
                return True
    except Exception:
        pass
    check: SecurityDetectionResult = await SecurityDetector.inspect_page(page)
    return not check.is_detected