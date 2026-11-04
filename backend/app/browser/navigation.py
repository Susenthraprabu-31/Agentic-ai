"""Resilient, step-by-step portal navigation and stability handler."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse
from typing import Any

from playwright.async_api import Page, Response

from app.core.config import settings

logger = logging.getLogger(__name__)


async def navigate_portal_step_by_step(
    page: Page,
    url: str,
    timeout_ms: int | None = None,
    workflow_run_id: str | None = None,
    node_id: str | None = None,
    wait_for_mount: bool = True,
) -> Response | None:
    """
    Performs dynamic, resilient, step-by-step navigation to any county portal.
    
    1. Avoids redundant re-navigation if page is already at target origin.
    2. Handles client-side redirects (e.g. silent auth, base64 sessions) without
       crashing on Chromium net::ERR_ABORTED.
    3. Uses progressive wait_until='commit' with DOM mounting polling to handle
       heavy government SPAs (e.g. large Angular/ArcGIS bundles) without timing out.
    4. Automatically dismisses modals/disclaimers step-by-step.
    """
    if not url:
        return None

    eff_timeout = int(timeout_ms or getattr(settings, "BROWSER_NAVIGATION_TIMEOUT_MS", 60000))
    target_parsed = urlparse(url)
    curr_url = page.url or ""
    curr_parsed = urlparse(curr_url)

    # 1. Check if page is already on this target domain/host
    is_same_host = bool(
        curr_parsed.netloc
        and target_parsed.netloc
        and curr_parsed.netloc.lower() == target_parsed.netloc.lower()
        and not curr_url.startswith("about:")
    )

    if is_same_host:
        logger.info(
            "Page is already loaded at portal host %s (current URL: %s). Waiting step-by-step for page readiness.",
            target_parsed.netloc,
            curr_url,
        )
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=6000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        response = None
    else:
        logger.info("Step-by-step navigating to portal URL: %s (timeout: %dms)", url, eff_timeout)
        response = None
        try:
            # Navigate using 'commit' first to get initial server response headers immediately,
            # preventing timeouts on massive 5MB+ JavaScript bundles.
            response = await page.goto(url, wait_until="commit", timeout=eff_timeout)
        except Exception as nav_exc:
            err_str = str(nav_exc).lower()
            if "err_aborted" in err_str:
                # Client-side script redirected the page (e.g., ?tried_silent_auth=1 or session redirect)
                logger.info(
                    "Navigation redirected or aborted by client script (%s). Waiting step-by-step for final URL: %s",
                    nav_exc,
                    page.url,
                )
                await page.wait_for_timeout(2000)
            elif "timeout" in err_str:
                logger.warning("Initial navigation commit timed out for %s: %s", url, nav_exc)
                # Retry once with domcontentloaded
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=eff_timeout)
                except Exception as retry_exc:
                    if "err_aborted" in str(retry_exc).lower():
                        logger.info("Retry navigation redirected: %s", retry_exc)
                    else:
                        raise retry_exc
            else:
                raise nav_exc

        # Pacing wait for network and DOM to settle
        await page.wait_for_timeout(1500)

    # 2. Wait for SPA framework or DOM root to mount
    if wait_for_mount:
        try:
            # Wait for any interactive elements (input, button, table, or link) to be present in DOM
            for attempt in range(15):
                await page.wait_for_timeout(1000)
                # Check frames as well for embedded portals (TaxSys / county-taxes.net)
                input_count = await page.locator("input, button, a[href], [role='tab'], table").count()
                if input_count > 0:
                    break
                for f in page.frames:
                    if f != page.main_frame:
                        f_count = await f.locator("input, button, table").count()
                        if f_count > 0:
                            input_count = f_count
                            break
                if input_count > 0:
                    break
        except Exception as mount_err:
            logger.debug("DOM mount polling note: %s", mount_err)

    # 3. Step-by-step modal / disclaimer detection and dismissal
    try:
        from app.browser.modal_detector import detect_and_dismiss_modal_ai
        modal_res = await detect_and_dismiss_modal_ai(
            page,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
        )
        if modal_res.get("has_disclaimer"):
            logger.info("AI Modal Detector dismissed modal: %s", modal_res.get("description"))
            await page.wait_for_timeout(1000)
    except Exception as d_err:
        logger.debug("Step-by-step modal check note: %s", d_err)

    # 4. Final step stabilization delay
    await page.wait_for_timeout(1000)
    return response

