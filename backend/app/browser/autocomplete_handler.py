"""
Dynamic AI and DOM Autocomplete / Suggestion Handler for county portals.
Detects suggestion popups (jQuery UI, Bootstrap, Angular Material, Kendo UI,
Typeahead, ARIA listbox options) when typing queries into search fields,
clicks the matching suggestion item, and logs live session activity.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from playwright.async_api import Page, Frame

logger = logging.getLogger(__name__)

AUTOCOMPLETE_DETECTION_JS = """
(expectedValue) => {
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const cleanDigits = (expectedValue || '').replace(/\\D/g, '');
    const cleanExp = (expectedValue || '').toLowerCase().trim();

    // Selectors commonly used by county and municipal portals for autocomplete dropdowns
    const candidateSelectors = [
        '.ui-autocomplete li',
        '.ui-autocomplete .ui-menu-item',
        'ul[role=\"listbox\"] li',
        'ul[role=\"listbox\"] [role=\"option\"]',
        '[role=\"listbox\"] [role=\"option\"]',
        '[role=\"option\"]',
        '[class*=\"suggestion\"]:not(input)',
        '[class*=\"autocomplete\"]:not(input) li',
        '[class*=\"autocomplete-suggestion\"]',
        '.dropdown-menu .dropdown-item',
        '[class*=\"dropdown-item\"]',
        '.tt-suggestion',
        '[class*=\"typeahead\"] li',
        'mat-option',
        '.mat-mdc-option',
        '.k-list-item',
        '.k-item'
    ];

    const foundItems = [];

    for (const sel of candidateSelectors) {
        try {
            const els = document.querySelectorAll(sel);
            els.forEach((el, idx) => {
                const style = window.getComputedStyle(el);
                const isVis = el.offsetParent !== null && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                if (!isVis) return;

                const txt = norm(el.innerText || el.textContent);
                if (!txt || txt.length > 250) return;

                // Build a selector for this item
                let itemSel = '';
                if (el.id && !/^\\d/.test(el.id)) {
                    itemSel = '#' + CSS.escape(el.id);
                } else if (sel.includes('ui-autocomplete')) {
                    itemSel = `.ui-autocomplete li:nth-child(${idx + 1}), .ui-autocomplete .ui-menu-item:nth-child(${idx + 1})`;
                } else {
                    itemSel = `${sel}:has-text(\"${txt.replace(/\"/g, '\\\\\"')}\")`;
                }

                foundItems.push({
                    text: txt,
                    selector: itemSel,
                    baseSelector: sel,
                    index: idx
                });
            });
            if (foundItems.length > 0) {
                break;
            }
        } catch (e) {}
    }

    if (foundItems.length === 0) {
        return { has_autocomplete: false, items: [] };
    }

    // Score and find the best matching item
    let best = foundItems[0];
    for (const item of foundItems) {
        const itemDigits = item.text.replace(/\\D/g, '');
        const itemLower = item.text.toLowerCase();
        if (cleanDigits && (itemDigits.includes(cleanDigits) || cleanDigits.includes(itemDigits))) {
            best = item;
            break;
        }
        if (cleanExp && itemLower.includes(cleanExp)) {
            best = item;
            break;
        }
    }

    return {
        has_autocomplete: true,
        best_item: best,
        total_items: foundItems.length,
        items: foundItems.slice(0, 10)
    };
}
"""


async def detect_and_select_autocomplete(
    page: Page,
    expected_value: str,
    *,
    workflow_run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    wait_time_ms: int = 800,
) -> dict[str, Any]:
    """
    Detects if typing into an input brought up an autocomplete / suggestion menu.
    If detected, clicks the matching suggestion, waits for state binding, and logs activity.
    Returns metadata dict indicating whether autocomplete was detected and selected.
    """
    if not page or page.is_closed():
        return {"detected": False}

    # Brief pause to allow framework/jQuery autocomplete async lookup to populate DOM
    await page.wait_for_timeout(wait_time_ms)

    result_data = None
    target_frame = page.main_frame

    # Inspect main page and any iframe
    for f in [page.main_frame, *[fr for fr in page.frames if fr != page.main_frame]]:
        try:
            res = await f.evaluate(AUTOCOMPLETE_DETECTION_JS, expected_value)
            if res.get("has_autocomplete") and res.get("best_item"):
                result_data = res
                target_frame = f
                break
        except Exception:
            continue

    if not result_data or not result_data.get("has_autocomplete"):
        return {"detected": False}

    best_item = result_data["best_item"]
    sel = best_item.get("selector")
    txt = best_item.get("text") or expected_value
    base_sel = best_item.get("baseSelector") or ".ui-autocomplete li"

    logger.info("Detected autocomplete dropdown item: '%s' (selector: %s). Clicking suggestion...", txt, sel)

    clicked = False
    try:
        # 1. Try clicking via specific Playwright locator
        if sel:
            loc = target_frame.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=1200):
                await loc.click(force=True, timeout=2500)
                clicked = True
    except Exception as e:
        logger.debug("Autocomplete specific selector click failed: %s", e)

    if not clicked:
        try:
            # 2. Try clicking via text locator on base selector
            txt_loc = target_frame.locator(f"{base_sel}:visible").first
            if await txt_loc.count() > 0 and await txt_loc.is_visible(timeout=1000):
                await txt_loc.click(force=True, timeout=2500)
                clicked = True
        except Exception as e:
            logger.debug("Autocomplete base selector click failed: %s", e)

    if not clicked:
        try:
            # 3. Fallback: JS click on best item
            clicked = await target_frame.evaluate(
                """(txt) => {
                    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const els = document.querySelectorAll('.ui-autocomplete li, [role=\"option\"], .dropdown-item, .tt-suggestion, mat-option');
                    for (const el of els) {
                        if (norm(el.innerText || el.textContent) === txt) {
                            el.click();
                            return true;
                        }
                    }
                    if (els.length > 0) {
                        els[0].click();
                        return true;
                    }
                    return false;
                }""",
                txt,
            )
        except Exception as e:
            logger.debug("Autocomplete JS click fallback note: %s", e)

    # Universal keyboard commit fallback (ArrowDown + Enter) for Knockout / jQuery UI listboxes
    try:
        await page.keyboard.press("ArrowDown")
        await page.wait_for_timeout(150)
        await page.keyboard.press("Enter")
        clicked = True
    except Exception:
        pass

    # Log browser activity milestone
    if clicked:
        try:
            from app.browser.browser_manager import get_browser_manager
            bm = await get_browser_manager()
            if workflow_run_id:
                await bm.log_session_activity(
                    action="autocomplete_select",
                    description=f"Selected autocomplete suggestion: {txt}",
                    node_id=node_id,
                    step_index=4,
                    status="COMPLETED",
                    url=page.url,
                )
        except Exception:
            pass

        # Pacing wait for client-side state / Knockout / Angular to settle
        await page.wait_for_timeout(800)

    return {
        "detected": True,
        "clicked": clicked,
        "selected_text": txt,
        "selector": sel,
    }

