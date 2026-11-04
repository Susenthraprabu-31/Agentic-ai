"""AI-driven search results link analyzer and parcel clicker.

Dynamically inspects county portal search results pages, detects result tables or lists,
identifies the matching parcel number or property record link via AI, clicks it to navigate
to the property details page, and returns the discovered selector for persistence into recipes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from playwright.async_api import Frame, Page

from app.core.config import settings
from app.core.llm_client import chat_completions_create, llm_configured

logger = logging.getLogger(__name__)

RESULT_CANDIDATES_JS = r"""
(query) => {
    const out = [];
    const cleanDigits = (query || '').replace(/\\D/g, '');
    
    // Check if this page already looks like a property details page rather than a results list
    const bodyText = (document.body ? document.body.innerText : '').slice(0, 800).toLowerCase();
    const isAlreadyDetail = (
        (bodyText.includes('parcel details') || bodyText.includes('property details') || bodyText.includes('assessment details')) &&
        !bodyText.includes('search results') &&
        !bodyText.includes('basic search results') &&
        !bodyText.includes('showing 1 through') &&
        !bodyText.includes('records found') &&
        !bodyText.includes('total records')
    );
    if (isAlreadyDetail) {
        return { is_already_detail: true, candidates: [] };
    }

    function esc(s) {
        return (s || '').replace(/\\\\/g, '\\\\').replace(/"/g, '\\\\"').trim();
    }

    // Look for clickable elements inside result tables, cards, or lists
    // Supports both <a> links and clickable <td>/<tr> elements (Knockout/Angular/jQuery DataTables)
    const candidates = document.querySelectorAll(
        'table tbody tr td.link, table tbody tr td[class*="link"], ' +
        'table tbody tr td[data-bind*="goTo"], table tbody tr td[data-bind*="click"], ' +
        'table tbody tr td[onclick], table tbody tr td.pointer, table tbody tr.pointer td, ' +
        'table tr td a, .table a, [role="row"] a, [role="row"] [role="gridcell"], #flex1 tr a, ' +
        'a[href*="detail" i], a[href*="parcel" i], a[href*="folio" i], a[href*="record" i], ' +
        'table tbody tr td:first-child, table tbody tr td:nth-child(2)'
    );
    
    const seen = new Set();
    candidates.forEach((el, idx) => {
        try {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || rect.width === 0 || rect.height === 0) return;
            
            const tag = el.tagName.toLowerCase();
            const txt = (el.innerText || el.textContent || '').trim();
            const href = el.getAttribute('href') || '';
            const dataBind = el.getAttribute('data-bind') || '';
            const className = el.className || '';

            // Ignore header cells or non-data actions
            if (el.tagName === 'TH' || el.closest('thead') || el.closest('th')) return;
            if (/next|previous|first|last|page \\d|export|print|help|select all|download/i.test(txt)) return;
            if (tag === 'a' && href.startsWith('#') && !href.includes('detail') && !href.includes('parcel') && !href.includes('/')) return;
            if (tag === 'a' && href.startsWith('javascript:void(0)')) return;

            const tr = el.closest('tr') || el.closest('[role="row"]') || el.parentElement;
            const rowText = tr ? tr.innerText.replace(/\\s+/g, ' ').slice(0, 200) : '';

            let sel = '';
            if (el.id && !/^\\d/.test(el.id)) {
                sel = '#' + CSS.escape(el.id);
            } else if (href && !href.startsWith('javascript:')) {
                const cleanHref = href.split('?')[0].split('/').pop();
                if (cleanHref && cleanHref.length > 3) {
                    sel = `a[href*=\"${esc(cleanHref)}\"]`;
                }
            } else if (dataBind.includes('goToPin') || dataBind.includes('goTo')) {
                sel = `${tag}[data-bind*=\"goTo\"]:has-text(\"${esc(txt.slice(0, 30))}\")`;
            } else if (className.includes('link') && txt && txt.length <= 40) {
                sel = `${tag}.link:has-text(\"${esc(txt)}\")`;
            } else if (tag === 'a' && txt && txt.length <= 40) {
                sel = `a:has-text(\"${esc(txt)}\")`;
            } else if (tag === 'td' && txt && txt.length <= 40) {
                sel = `table tbody tr td:has-text(\"${esc(txt)}\")`;
            }

            if (!sel) {
                sel = 'table tbody tr:first-child td:first-child';
            }

            if (seen.has(sel) && !sel.includes('has-text')) return;
            seen.add(sel);

            out.push({
                index: idx,
                tag: tag,
                text: txt,
                href: href,
                data_bind: dataBind,
                is_cell: tag === 'td',
                row_context: rowText,
                selector: sel
            });
        } catch (e) {}
    });
    
    return { is_already_detail: false, candidates: out.slice(0, 20) };
}
"""

PROMPT_TEMPLATE = """You are an automated web browser agent.
A property search was just executed for query value: "{query_value}" (query_type: {query_type}).
The browser is currently on page: {url} (title: "{title}")

Here are candidate result links, table cells, and rows found on the page:
{candidates_json}

Determine if this page is a search results listing / table where the user needs to click the parcel number, link, or table cell/row to open the full property details page.
If YES:
1. Identify the best element to click (specifically the parcel number link, clickable table cell e.g. td.link, or property record detail link).
2. Provide the exact Playwright CSS selector to click it.
If NO (e.g. page is already showing the full property detail view or no matching results exist), return is_results_page: false.

Return ONLY valid JSON:
{{
  "is_results_page": true,
  "click_selector": "td.link:has-text('161425-0000')",
  "link_text": "161425-0000",
  "description": "Click parcel number 161425-0000 to view property details",
  "reasoning": "Brief explanation"
}}
"""


async def detect_and_click_result_link_ai(
    page: Page,
    query_value: str,
    *,
    query_type: str = "parcel",
    workflow_run_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Inspects page across all frames for search results links / parcel numbers.
    Uses AI to determine if a result link needs to be clicked, clicks it,
    and returns metadata suitable for executing and persisting into recipes.
    """
    curr_url = page.url or ""
    clean_digits = re.sub(r"\D", "", query_value)

    # Inspect all frames
    all_candidates: list[dict[str, Any]] = []
    target_frame: Frame = page.main_frame
    target_frame_idx = 0

    for idx, f in enumerate(page.frames):
        try:
            res = await f.evaluate(RESULT_CANDIDATES_JS, query_value)
            if res.get("is_already_detail"):
                logger.debug("Page or frame %s is already at property details view.", f.url)
                return {"is_results_page": False, "is_already_detail": True}
            cand_list = res.get("candidates", [])
            for c in cand_list:
                c["frame_index"] = idx
                c["frame_url"] = f.url
                all_candidates.append(c)
                if target_frame == page.main_frame and idx > 0 and cand_list:
                    target_frame = f
                    target_frame_idx = idx
        except Exception:
            continue

    if not all_candidates:
        logger.debug("No search result link candidates found on %s", curr_url)
        return {"is_results_page": False}

    logger.info("Found %d result link candidates on %s. Asking AI to identify parcel link...", len(all_candidates), curr_url)

    ai_data: dict[str, Any] = {"is_results_page": False}
    if llm_configured():
        try:
            page_title = await page.title() if not page.is_closed() else ""
            prompt = PROMPT_TEMPLATE.format(
                query_value=query_value,
                query_type=query_type,
                url=curr_url,
                title=page_title,
                candidates_json=json.dumps(all_candidates, indent=2),
            )
            resp, model_used = await chat_completions_create(
                create_kwargs={
                    "model": getattr(settings, "OPENAI_MODEL", "gpt-6-luna") or "gpt-6-luna",
                    "messages": [
                        {"role": "system", "content": "You are a web automation assistant. Respond strictly in valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            ai_data = json.loads(resp.choices[0].message.content or "{}")
            logger.info("AI Result Link Analysis (via %s): %s", model_used, ai_data)
        except Exception as e:
            logger.warning("AI result link analysis LLM call failed: %s. Using heuristic fallback.", e)
            ai_data = {"is_results_page": False}

    # Heuristic fallback if LLM returned false or failed
    if not ai_data.get("is_results_page"):
        for c in all_candidates:
            c_text = (c.get("text") or "").strip()
            c_href = (c.get("href") or "").lower()
            c_db = (c.get("data_bind") or "").lower()
            c_sel = (c.get("selector") or "").lower()
            c_digits = re.sub(r"\D", "", c_text)

            # Check if link text matches parcel digits or href indicates property detail or element has goTo/link
            matches_parcel = (clean_digits and clean_digits in c_digits) or (c_digits and c_digits in clean_digits)
            is_detail_href = any(k in c_href for k in ["propertydetails", "detail", "recorddetail", "parceldetail", "folioid"])
            is_clickable_cell = any(k in c_db for k in ["goto", "gotopin", "gotorecord", "detail"]) or ("link" in c_sel and matches_parcel) or (".pointer" in c_sel)

            if matches_parcel or is_detail_href or (is_clickable_cell and (matches_parcel or not clean_digits)):
                ai_data = {
                    "is_results_page": True,
                    "click_selector": c.get("selector"),
                    "link_text": c_text,
                    "description": f"Click parcel number {c_text or query_value} to view property details",
                    "reasoning": "Heuristic match on parcel number / detail link / table cell in results table",
                }
                break

    if not ai_data.get("is_results_page") or not ai_data.get("click_selector"):
        return {"is_results_page": False}

    click_selector = ai_data["click_selector"]
    link_text = ai_data.get("link_text") or query_value
    desc = ai_data.get("description") or f"Click parcel number {link_text} to view property details"

    logger.info("Clicking AI-identified parcel/result link: %s (%s)", click_selector, link_text)

    # Optional activity logging via BrowserManager
    try:
        from app.browser.browser_manager import get_browser_manager
        bm = await get_browser_manager()
        await bm.log_session_activity(
            action="view_record",
            description=desc,
            node_id=node_id,
            step_index=6,
            status="RUNNING",
            url=page.url,
        )
    except Exception:
        pass

    clicked = False
    for scope in [target_frame, page, *page.frames]:
        try:
            loc = scope.locator(click_selector).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2000):
                try:
                    await loc.click(force=True, timeout=4000)
                    clicked = True
                    break
                except Exception:
                    await loc.evaluate("el => el.click()")
                    clicked = True
                    break
        except Exception:
            continue

    if not clicked:
        # Fallback to direct text search with hyphen, space, and digit variants across frames
        text_variants = list(dict.fromkeys([
            link_text,
            link_text.replace("-", " "),
            link_text.replace("-", ""),
            clean_digits,
            query_value,
            query_value.replace("-", " "),
        ]))
        text_variants = [v for v in text_variants if v and len(v) >= 3]

        for scope in [target_frame, page, *page.frames]:
            for variant in text_variants:
                try:
                    fb_loc = scope.locator(
                        f"table tbody tr td.link:has-text('{variant}'), "
                        f"td[data-bind*='goTo']:has-text('{variant}'), "
                        f"table tbody tr td:has-text('{variant}'), "
                        f"table a:has-text('{variant}'), a:has-text('{variant}')"
                    ).first
                    if await fb_loc.count() > 0 and await fb_loc.is_visible(timeout=1200):
                        try:
                            await fb_loc.click(force=True, timeout=3000)
                        except Exception:
                            await fb_loc.evaluate("el => el.click()")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

    if not clicked:
        # Final fallback: click first result row cell if result table is present
        for scope in [target_frame, page, *page.frames]:
            try:
                first_row_cell = scope.locator(
                    "table tbody tr:first-child td.link, "
                    "table tbody tr:first-child td[data-bind*='goTo'], "
                    "table tbody tr td[data-bind*='goToPin'], "
                    "table tbody tr:first-child td a, "
                    "table tbody tr:first-child td:first-child"
                ).first
                if await first_row_cell.count() > 0 and await first_row_cell.is_visible(timeout=1000):
                    try:
                        await first_row_cell.click(force=True, timeout=3000)
                    except Exception:
                        await first_row_cell.evaluate("el => el.click()")
                    clicked = True
                    break
            except Exception:
                continue

    await page.wait_for_timeout(3500)

    try:
        from app.browser.browser_manager import get_browser_manager
        bm = await get_browser_manager()
        await bm.log_session_activity(
            action="view_record",
            description=f"Opened property details: {page.url}",
            node_id=node_id,
            step_index=6,
            status="COMPLETED" if clicked else "FAILED",
            url=page.url,
        )
    except Exception:
        pass

    return {
        "is_results_page": True,
        "click_selector": click_selector,
        "link_text": link_text,
        "description": desc,
        "reasoning": ai_data.get("reasoning", ""),
        "clicked": clicked,
        "navigated_url": page.url,
    }

