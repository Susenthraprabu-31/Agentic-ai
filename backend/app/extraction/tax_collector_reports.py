"""
Official Tax Collector Report and Bill PDF retrieval, full-page screenshot capture,
and dynamic tax account data extraction.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Frame, Page
from app.extraction.assessor_reports import register_downloaded_documents

logger = logging.getLogger(__name__)


def _clean_storage_key(identifier: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", (identifier or "record").strip())
    return cleaned or "tax_record"


def get_tax_storage_dir(identifier: str) -> Path:
    key = _clean_storage_key(identifier)
    digits = re.sub(r"\D", "", key) or key
    dest_dir = Path("tmp") / "downloads" / "tax" / digits
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir


def is_tax_collector_portal(url: str, title: str = "", html_sample: str = "") -> bool:
    """Check if the given URL, title, or HTML belongs to a tax collector portal."""
    combined = f"{url} {title} {html_sample}".lower()
    indicators = [
        "county-taxes.net",
        "county-taxes.com",
        "taxsys",
        "floridatax",
        "taxcollector",
        "tax-collector",
        "property-tax",
        "taxbill",
        "govhub",
        "mdctaxcollector",
    ]
    return any(ind in combined for ind in indicators)


TAX_UNIFIED_SEARCH_INPUT_JS = r"""
() => {
    const isVisible = (el) => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'
            && r.width > 24 && r.height > 8;
    };
    const scoreInput = (el) => {
        const ph = (el.getAttribute('placeholder') || '').toLowerCase();
        const al = (el.getAttribute('aria-label') || '').toLowerCase();
        const nm = (el.getAttribute('name') || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        const blob = [ph, al, nm, id].join(' ');
        let sc = 0;
        if (/name.*address|address.*account|account.*folio|folio.*number/i.test(blob)) sc += 220;
        if (/\bfolio\b/.test(blob)) sc += 120;
        if (/\baccount\b/.test(blob)) sc += 90;
        if (el.type === 'search' || /\bsearch\b/.test(al)) sc += 85;
        if (/\bsearch\b/.test(ph)) sc += 70;
        if (/\baddress\b/.test(blob)) sc += 40;
        if (/\bproperty\s*number\b/.test(blob)) sc += 200;
        if (/\bparcel\b/.test(blob) || /\bfolio\b/.test(blob)) sc += 160;
        if (id === 'propertynumber' || nm === 'propertynumber') sc += 240;
        return sc;
    };
    const buildSel = (el) => {
        if (el.id && !/^\d/.test(el.id)) return '#' + CSS.escape(el.id);
        const ph = el.getAttribute('placeholder');
        if (ph) return 'input[placeholder="' + ph.replace(/"/g, '\\"') + '"]';
        const nm = el.getAttribute('name');
        if (nm) return 'input[name="' + nm.replace(/"/g, '\\"') + '"]';
        const al = el.getAttribute('aria-label');
        if (al) return 'input[aria-label="' + al.replace(/"/g, '\\"') + '"]';
        return '';
    };
    const picks = [];
    document.querySelectorAll(
        "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='file'])"
    ).forEach((el) => {
        if (!isVisible(el)) return;
        const sc = scoreInput(el);
        if (sc < 45) return;
        const sel = buildSel(el);
        if (!sel) return;
        picks.push({ selector: sel, score: sc });
    });
    picks.sort((a, b) => b.score - a.score);
    return picks[0] || null;
}
"""

TAX_UNIFIED_SUBMIT_JS = r"""
() => {
    const isVisible = (el) => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
    };
    const scoreBtn = (el) => {
        const t = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
        const id = (el.id || '').toLowerCase();
        let sc = 0;
        if (t === 'search' || id === 'search') sc += 150;
        else if (t.includes('search')) sc += 90;
        else if (t === 'go' || t === 'find') sc += 60;
        if (el.type === 'submit') sc += 80;
        if ((el.getAttribute('role') || '').toLowerCase() === 'button' && t === 'search') sc += 95;
        return sc;
    };
    const buildSel = (el) => {
        if (el.id && !/^\d/.test(el.id)) return '#' + CSS.escape(el.id);
        const val = el.getAttribute('value');
        if (val && el.tagName.toLowerCase() === 'input')
            return 'input[type="' + (el.type || 'submit') + '"][value="' + val.replace(/"/g, '\\"') + '"]';
        const txt = (el.innerText || '').trim().slice(0, 40);
        if (txt) return el.tagName.toLowerCase() + ':has-text("' + txt.replace(/"/g, '\\"') + '")';
        return '';
    };
    const picks = [];
    document.querySelectorAll(
        'button, input[type="submit"], input[type="button"], [role="button"], div.button, #search'
    ).forEach((el) => {
        if (!isVisible(el)) return;
        const sc = scoreBtn(el);
        if (sc < 50) return;
        const sel = buildSel(el);
        if (!sel) return;
        picks.push({ selector: sel, score: sc });
    });
    picks.sort((a, b) => b.score - a.score);
    return picks[0] || null;
}
"""

TAX_PORTAL_SUBMIT_INVOKE_JS = r"""() => {
    const vis = (el) => {
        try {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
        } catch (_) { return false; }
    };
    const tryClick = (sel) => {
        const el = document.querySelector(sel);
        if (el && vis(el)) { try { el.click(); return true; } catch (_) {} }
        return false;
    };
    if (tryClick('#search')) return true;
    const btn = document.querySelector('[role="button"]#search, div#search.button, #search[role="button"]');
    if (btn && vis(btn)) { try { btn.click(); return true; } catch (_) {} }
    if (typeof submitForm === 'function') {
        try { submitForm(0); return true; } catch (_) {}
    }
    const inputs = document.querySelectorAll('input[type="submit"], input[type="button"]');
    for (const el of inputs) {
        const v = ((el.value || '') + '').trim().toLowerCase();
        if (v === 'search' && vis(el)) { try { el.click(); return true; } catch (_) {} }
    }
    return false;
}"""

TAX_RETURN_TO_SEARCH_JS = r"""() => {
    const vis = (el) => {
        try {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
        } catch (_) { return false; }
    };
    const labels = /new search|search again|clear search|property search|search selection/i;
    const links = document.querySelectorAll('a, button, [role="button"], input[type="button"]');
    for (const el of links) {
        const t = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim();
        if (!t || !labels.test(t)) continue;
        if (!vis(el)) continue;
        try { el.click(); return t.slice(0, 80); } catch (_) {}
    }
    return '';
}"""


def _tax_collector_detail_url(url: str) -> bool:
    """True when URL path/fragment looks like a tax account detail view, not the search form."""
    url_l = (url or "").lower().split("?")[0]
    search_markers = ("/property/search", "searchselect", "search.aspx", "basicsearch", "search/basic")
    if any(m in url_l for m in search_markers):
        return False
    detail_markers = (
        "taxbill",
        "delinquent",
        "accountsummary",
        "/account/",
        "/receipt",
        "paymenthistory",
        "billhistory",
        "noticedetails",
        "propertydetails",
        "pagetypeid=4",
    )
    return any(m in url_l for m in detail_markers)


async def _collect_page_text(page: Page) -> str:
    combined_parts: list[str] = []
    for frame in page.frames:
        try:
            chunk = await frame.locator("body").inner_text(timeout=2500)
            if chunk:
                combined_parts.append(chunk)
        except Exception:
            continue
    try:
        combined_parts.append(await page.locator("body").inner_text(timeout=1500) or "")
    except Exception:
        pass
    return "\n".join(combined_parts)


async def tax_portal_body_matches_query(page: Page, query_value: str) -> bool:
    """Return True when visible portal text contains the active search query (parcel/account)."""
    q = (query_value or "").strip()
    if not q:
        return True
    body = (await _collect_page_text(page)).lower()
    if len(body) < 40:
        return False
    digits = re.sub(r"\D", "", q)
    body_digits = re.sub(r"\D", "", body)
    if digits and len(digits) >= 6 and digits in body_digits:
        return True
    compact_q = re.sub(r"[\s\-]+", "", q.upper())
    compact_body = re.sub(r"[\s\-]+", "", body.upper())
    if compact_q and compact_q in compact_body:
        return True
    try:
        from app.browser.result_selector_ai import _query_match_tokens

        tokens = _query_match_tokens(q, "parcel")
        if tokens and sum(1 for t in tokens if t.upper() in body.upper()) >= min(1, len(tokens)):
            return True
    except Exception:
        pass
    return False


async def is_tax_collector_detail_view(page: Page) -> bool:
    """Detect tax collector account/detail UI (no interactable search field)."""
    url_l = (page.url or "").lower()
    if _tax_collector_detail_url(url_l):
        return True
    if ("searchselect" in url_l or re.search(r"/property/search", url_l)) and await _tax_search_input_visible(
        page, None, 450
    ):
        return False
    try:
        markers = (
            "real estate account",
            "account summary",
            "amount due",
            "annual bill",
            "print (pdf)",
        )
        body = (await _collect_page_text(page)).lower()
        if len(body) >= 80 and any(m in body for m in markers):
            return not await _tax_search_input_visible(page, None, 350)
    except Exception:
        pass
    return False


async def navigate_tax_collector_to_search(
    page: Page,
    portal_search_url: str | None = None,
) -> bool:
    """Return to the property search workspace from a detail/history view (in-page links, then optional URL)."""
    navigated = False
    for frame in page.frames:
        try:
            clicked = await frame.evaluate(TAX_RETURN_TO_SEARCH_JS)
            if clicked:
                await page.wait_for_timeout(900)
                navigated = True
                break
        except Exception:
            continue

    search_link_selectors = (
        "a[href*='SearchSelect' i]",
        "a[href*='searchselect' i]",
        "a[href*='ClearData' i]",
        "a[href*='/property/search' i]",
        "a[href*='property-tax' i]",
        "a:has-text('Property Search')",
        "a:has-text('New Search')",
        "a:has-text('Search Again')",
        "button:has-text('Property Search')",
        "a:has-text('Property Tax')",
        "button:has-text('Property Tax')",
        "a:has-text('Pay Property Tax')",
    )
    if not await _tax_search_input_visible(page, None, 500):
        for frame in page.frames:
            for sel in search_link_selectors:
                try:
                    link = frame.locator(sel).first
                    if await link.count() == 0 or not await link.is_visible(timeout=500):
                        continue
                    await link.click(timeout=4000)
                    await page.wait_for_timeout(1200)
                    navigated = True
                    break
                except Exception:
                    continue
            if navigated and await _tax_search_input_visible(page, None, 500):
                break

    if portal_search_url and not await _tax_search_input_visible(page, None, 700):
        try:
            await page.goto(portal_search_url, wait_until="domcontentloaded", timeout=28000)
            await page.wait_for_timeout(900)
            navigated = True
        except Exception as nav_err:
            logger.debug("Tax portal search URL navigation: %s", nav_err)

    if not await _tax_search_input_visible(page, None, 600):
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(600)
            navigated = True
        except Exception:
            pass

    return await _tax_search_input_visible(page, None, 1200)


async def ensure_tax_collector_search_page(
    page: Page,
    query_value: str = "",
    portal_search_url: str | None = None,
) -> bool:
    """
    Open the property tax search view when on auth errors, detail pages, or a stale/wrong record.
  """
    if not is_tax_collector_portal(page.url) and not any(
        is_tax_collector_portal(f.url or "") for f in page.frames
    ):
        return False

    url_l = (page.url or "").lower()
    auth_noise = "error=login" in url_l or (
        "callback" in url_l and "property-tax" not in url_l and "searchselect" not in url_l
    )

    search_ready = await _tax_search_input_visible(page, None, 500)
    on_detail = await is_tax_collector_detail_view(page)
    wrong_record = bool(
        query_value.strip()
        and (on_detail or not search_ready)
        and not await tax_portal_body_matches_query(page, query_value)
    )

    if search_ready and not on_detail and not auth_noise and not wrong_record:
        return False

    if search_ready and query_value.strip() and not wrong_record and not on_detail:
        return False

    if auth_noise or on_detail or wrong_record or not search_ready:
        return await navigate_tax_collector_to_search(page, portal_search_url)

    nav_selectors = (
        "a[href*='property-tax']",
        "a:has-text('Property Tax')",
        "button:has-text('Property Tax')",
        "a:has-text('Pay Property Tax')",
    )
    for frame in page.frames:
        for sel in nav_selectors:
            try:
                link = frame.locator(sel).first
                if await link.count() == 0 or not await link.is_visible(timeout=600):
                    continue
                await link.click(timeout=4000)
                await page.wait_for_timeout(1500)
                return True
            except Exception:
                continue
    return False


async def run_tax_collector_unified_search(page: Page, query_value: str) -> dict[str, Any]:
    """
    Fill and submit the unified tax portal search box (name / address / account / folio).
    """
    from app.browser.modal_detector import detect_and_dismiss_modal_ai, dismiss_blocking_overlays_heuristic

    result: dict[str, Any] = {
        "filled": False,
        "submitted": False,
        "input_selector": None,
        "submit_selector": None,
        "fills": [],
    }
    q = (query_value or "").strip()
    if not q:
        return result

    await dismiss_blocking_overlays_heuristic(page)
    try:
        await detect_and_dismiss_modal_ai(page)
    except Exception:
        pass
    await ensure_tax_collector_search_page(page, query_value=q)
    await dismiss_blocking_overlays_heuristic(page)

    input_pick: dict[str, Any] | None = None
    submit_pick: dict[str, Any] | None = None
    target_frame = page.main_frame

    for frame in page.frames:
        try:
            pick = await frame.evaluate(TAX_UNIFIED_SEARCH_INPUT_JS)
        except Exception:
            pick = None
        if pick and (not input_pick or pick.get("score", 0) > input_pick.get("score", 0)):
            input_pick = pick
            target_frame = frame

    if not input_pick:
        for frame in page.frames:
            for probe in (
                "input[placeholder*='Name' i]",
                "input[placeholder*='Address' i]",
                "input[placeholder*='Account' i]",
                "input[placeholder*='Folio' i]",
                "input[type='search']",
            ):
                try:
                    loc = frame.locator(probe).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=400):
                        input_pick = {"selector": probe, "score": 100}
                        target_frame = frame
                        break
                except Exception:
                    continue
            if input_pick:
                break

    if not input_pick:
        return result

    in_sel = input_pick.get("selector") or ""
    try:
        inp = target_frame.locator(in_sel).first
        await inp.scroll_into_view_if_needed(timeout=3000)
        await inp.click(timeout=3000)
        await inp.fill(q, timeout=5000)
        await page.wait_for_timeout(300)
        seen = (await inp.input_value(timeout=2000) or "").strip()
        if not seen and q:
            await inp.press_sequentially(q, delay=30)
            await page.wait_for_timeout(200)
    except Exception as exc:
        logger.info("Tax unified search fill failed (%s): %s", in_sel, exc)
        return result

    result["filled"] = True
    result["input_selector"] = in_sel
    result["fills"] = [{"selector": in_sel, "value": q, "field_role": "unified_tax_search"}]

    try:
        submit_pick = await target_frame.evaluate(TAX_UNIFIED_SUBMIT_JS)
    except Exception:
        submit_pick = None

    submit_selectors = []
    if submit_pick and submit_pick.get("selector"):
        submit_selectors.append(submit_pick["selector"])
    submit_selectors.extend(
        [
            "#search",
            "[role='button']#search",
            "div#search.button",
            "button:has-text('Search')",
            "input[type='submit'][value*='Search' i]",
            "input[type='button'][value*='Search' i]",
            "button[aria-label*='search' i]",
        ]
    )

    for sub_sel in submit_selectors:
        try:
            btn = target_frame.locator(sub_sel).first
            if await btn.count() == 0 or not await btn.is_visible(timeout=500):
                continue
            await btn.click(timeout=4000)
            result["submitted"] = True
            result["submit_selector"] = sub_sel
            await page.wait_for_timeout(2000)
            return result
        except Exception:
            continue

    try:
        await target_frame.locator(in_sel).first.press("Enter")
        result["submitted"] = True
        await page.wait_for_timeout(2000)
    except Exception:
        pass

    if not result.get("submitted"):
        for frame in page.frames:
            try:
                if await frame.evaluate(TAX_PORTAL_SUBMIT_INVOKE_JS):
                    result["submitted"] = True
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                continue
    return result


async def _tax_search_input_visible(
    page: Page, input_selector: str | None = None, timeout_ms: int = 800
) -> bool:
    probes = [input_selector] if input_selector else []
    probes.extend(
        [
            "#propertynumber",
            "input[name*='property' i]",
            "input[placeholder*='Property Number' i]",
            "input[placeholder*='Folio' i]",
        ]
    )
    for frame in page.frames:
        for sel in probes:
            if not sel:
                continue
            try:
                loc = frame.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=timeout_ms):
                    return True
            except Exception:
                continue
    return False


async def refresh_tax_search_results_for_query(
    page: Page,
    query_value: str,
    *,
    input_selector: str | None = None,
    submit_selector: str | None = None,
    portal_search_url: str | None = None,
) -> bool:
    """
    Re-run the portal property search so the results list only contains rows for query_value.
    Used between multi-year bill captures when go_back lands on an unfiltered bulk list.
    """
    from app.browser.modal_detector import detect_and_dismiss_modal_ai, dismiss_blocking_overlays_heuristic

    q = (query_value or "").strip()
    if not q:
        return False

    await dismiss_blocking_overlays_heuristic(page)
    try:
        await detect_and_dismiss_modal_ai(page)
    except Exception:
        pass

    if portal_search_url and not await _tax_search_input_visible(page, input_selector, 400):
        try:
            await page.goto(portal_search_url, wait_until="domcontentloaded", timeout=28000)
            await page.wait_for_timeout(900)
        except Exception as nav_err:
            logger.debug("Portal search URL navigation: %s", nav_err)

    if not await _tax_search_input_visible(page, input_selector, 1200):
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=18000)
            await page.wait_for_timeout(700)
        except Exception:
            pass
        for frame in page.frames:
            try:
                await frame.evaluate(TAX_RETURN_TO_SEARCH_JS)
            except Exception:
                continue
        await page.wait_for_timeout(600)

    search = await run_tax_collector_unified_search(
        page,
        q,
    )
    if input_selector and search.get("input_selector") != input_selector:
        for frame in page.frames:
            try:
                inp = frame.locator(input_selector).first
                if await inp.count() == 0 or not await inp.is_visible(timeout=1200):
                    continue
                await inp.scroll_into_view_if_needed(timeout=3000)
                await inp.click(timeout=2000)
                await inp.fill(q, timeout=5000)
                search["filled"] = True
                search["input_selector"] = input_selector
                break
            except Exception:
                continue

    if search.get("filled") and submit_selector and not search.get("submitted"):
        for frame in page.frames:
            try:
                btn = frame.locator(submit_selector).first
                if await btn.count() == 0 or not await btn.is_visible(timeout=800):
                    continue
                await btn.click(timeout=5000)
                search["submitted"] = True
                await page.wait_for_timeout(1800)
                break
            except Exception:
                continue

    if search.get("filled") and not search.get("submitted"):
        for frame in page.frames:
            try:
                if await frame.evaluate(TAX_PORTAL_SUBMIT_INVOKE_JS):
                    search["submitted"] = True
                    await page.wait_for_timeout(1800)
                    break
            except Exception:
                continue

    for _ in range(14):
        discovery = await discover_tax_search_result_links(page, q)
        if _filter_tax_search_result_links(discovery.get("links") or [], q):
            logger.info("Tax search refreshed; matching bill-year links visible for %s", q)
            return True
        await page.wait_for_timeout(650)

    logger.warning("Tax search refresh did not restore filtered results for %s", q)
    return False


def record_has_tax_collector_identity(data: dict[str, Any] | None) -> bool:
    if not data or not isinstance(data, dict):
        return False
    owner = str(data.get("owner_name") or "").strip().upper()
    if not owner or owner in {"", "OWNER OF RECORD", "OWNER", "N/A", "UNKNOWN"}:
        return False
    acct = str(data.get("account_number") or data.get("parcel_number") or "").strip()
    addr = str(
        data.get("property_address") or data.get("situs") or data.get("situs_address") or ""
    ).strip()
    return bool(acct or (addr and any(c.isdigit() for c in addr)))


async def page_shows_tax_account_summary(page: Page, query_value: str = "") -> bool:
    """Detect tax collector account summary across main page and child frames."""
    body = (await _collect_page_text(page)).lower()
    if len(body) < 80:
        return False
    markers = (
        "real estate account",
        "account summary",
        "amount due",
        "paid in full",
        "nothing due",
        "print (pdf)",
        "annual bill",
        "situs",
        "enroll in paperless",
        "your account is",
    )
    if not any(m in body for m in markers):
        return False
    q = (query_value or "").strip()
    if not q:
        return True
    return await tax_portal_body_matches_query(page, q)


async def find_tax_frame(page: Page) -> Frame:
    """Find the frame hosting the tax account summary and bill history."""
    for f in page.frames:
        f_url = (f.url or "").lower()
        if any(k in f_url for k in ["taxsys", "county-taxes", "govhub", "floridatax"]):
            return f
        try:
            body_txt = await f.locator("body").inner_text()
            if any(k in body_txt.lower() for k in ["real estate account", "account summary", "amount due", "notice of ad valorem"]):
                return f
        except Exception:
            continue
    return page.main_frame


async def wait_for_tax_details_loaded(page: Page, timeout_seconds: int = 15) -> Frame:
    """
    Wait dynamically for the tax portal, child frames, and bill data to fully load.
    Prevents closing the browser prematurely before bills and history render.
    """
    target_frame = page.main_frame

    # 1. Wait for navigation / child frame to appear
    for _ in range(timeout_seconds):
        await page.wait_for_timeout(1000)
        for f in page.frames:
            f_url = (f.url or "").lower()
            if any(k in f_url for k in ["taxsys", "county-taxes", "govhub", "floridatax"]):
                target_frame = f
                break
        if target_frame != page.main_frame:
            break

    # 2. Wait for loading indicators / spinners inside target frame to hide
    try:
        await target_frame.wait_for_selector(
            "text=Loading, .loading-spinner, .spinner, .loading",
            state="hidden",
            timeout=8000,
        )
    except Exception:
        pass

    # 3. Wait for the bills and account content to populate in the DOM
    for _ in range(10):
        try:
            body = await target_frame.locator("body").inner_text()
            if len(body) > 400 or any(k in body.lower() for k in ["print (pdf)", "amount due", "annual bill", "receipt #", "paid $"]):
                break
        except Exception:
            pass
        await page.wait_for_timeout(1000)

    return target_frame


async def _aggregate_visible_body_text(page: Page) -> str:
    chunks: list[str] = []
    for frame in page.frames:
        try:
            chunk = await frame.locator("body").inner_text(timeout=3000)
            if chunk and chunk.strip():
                chunks.append(chunk.strip())
        except Exception:
            continue
    if not chunks:
        try:
            chunks.append(await page.locator("body").inner_text(timeout=2000) or "")
        except Exception:
            pass
    return "\n".join(chunks)


async def ensure_tax_account_detail_loaded(page: Page, query_value: str = "") -> bool:
    """Navigate from search/listing views to a tax account summary when possible."""
    from app.browser.modal_detector import dismiss_blocking_overlays_heuristic

    await dismiss_blocking_overlays_heuristic(page)
    if await page_shows_tax_account_summary(page, query_value):
        return True

    body_l = (await _aggregate_visible_body_text(page)).lower()
    if "records found" in body_l or "showing records" in body_l:
        try:
            from app.browser.result_selector_ai import detect_and_click_result_link_ai

            q = (query_value or "").strip()
            qt = "address" if re.search(r"[A-Za-z]", q) and re.search(r"\d", q) else "parcel"
            if " " in q and re.search(r"\b(st|rd|dr|ave|way|blvd|ln|ct)\b", q, re.I):
                qt = "address"
            res = await detect_and_click_result_link_ai(page, q, query_type=qt)
            if res.get("clicked"):
                await page.wait_for_timeout(2000)
                if await page_shows_tax_account_summary(page, query_value):
                    return True
        except Exception as tax_res_err:
            logger.debug("Tax search result AI click: %s", tax_res_err)

    q = (query_value or "").strip()
    q_compact = re.sub(r"\s+", " ", q)
    digits = re.sub(r"\D", "", q)
    probes: list[str] = []
    if q:
        probes.extend(
            [
                f'a[href*="{q}"]',
                f'a:has-text("{q}")',
                f'button:has-text("{q}")',
            ]
        )
    if q_compact and q_compact != q:
        probes.append(f'a:has-text("{q_compact}")')
    if digits:
        probes.append(f'a[href*="{digits}"]')
    probes.extend(
        [
            '[role="option"]',
            '[data-test*="search-result" i]',
            '[data-test*="account" i]',
            ".dropdown-menu a",
            ".typeahead-dropdown a",
        ]
    )

    for frame in page.frames:
        for sel in probes:
            try:
                loc = frame.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=500):
                    continue
                label = (await loc.inner_text(timeout=800) or "").strip()
                if digits and label and digits not in re.sub(r"\D", "", label) and q.upper() not in label.upper():
                    if "account" not in label.lower() and "annual" not in label.lower():
                        continue
                await loc.click(timeout=4000)
                await page.wait_for_timeout(1800)
                if await page_shows_tax_account_summary(page, query_value):
                    return True
            except Exception:
                continue

    digits = re.sub(r"\D", "", q)
    for frame in page.frames:
        for sel in (
            '[role="listbox"] [role="option"]',
            '.typeahead-dropdown li',
            '.dropdown-menu a',
            'ul[role="listbox"] li',
        ):
            try:
                options = frame.locator(sel)
                count = await options.count()
                for i in range(min(count, 8)):
                    opt = options.nth(i)
                    if not await opt.is_visible(timeout=300):
                        continue
                    txt = (await opt.inner_text(timeout=500) or "").strip()
                    if not txt:
                        continue
                    if digits and digits in re.sub(r"\D", "", txt):
                        if "account" in txt.lower() or q.upper() in txt.upper() or digits in txt:
                            await opt.click(timeout=3000)
                            await page.wait_for_timeout(2000)
                            if await page_shows_tax_account_summary(page, query_value):
                                return True
            except Exception:
                continue

    if "search_query" in (page.url or "").lower():
        try:
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1800)
        except Exception:
            pass

    await wait_for_tax_details_loaded(page, timeout_seconds=10)
    return await page_shows_tax_account_summary(page, query_value)


async def extract_tax_collector_record(page: Page, query_value: str = "") -> dict[str, Any]:
    """
    Extract structured tax account details, owner, situs, status, amounts, and account history.
    """
    await ensure_tax_account_detail_loaded(page, query_value)
    clean_target = query_value.strip()
    body_text = await _aggregate_visible_body_text(page)

    record: dict[str, Any] = {
        "parcel_number": clean_target,
        "account_number": "",
        "owner_name": "",
        "property_address": "",
        "situs_address": "",
        "tax_year": 2026,
        "tax_status": "Unknown",
        "status": "Unknown",
        "amount_due": 0.0,
        "total_amount_due": 0.0,
        "amount_paid": 0.0,
        "due_date": "",
        "current_bill_year": "",
        "current_bill_amount": 0.0,
        "is_delinquent": False,
        "account_history": [],
    }

    if not body_text:
        return record

    # 1. Account / Parcel Number
    m_acct = re.search(
        r"(?:Real\s+Estate\s+)?Account\s*#?\s*([0-9][0-9\-_.]+)",
        body_text,
        re.I,
    )
    if m_acct:
        record["account_number"] = m_acct.group(1).strip()
        if not record["parcel_number"]:
            record["parcel_number"] = record["account_number"]

    # 2. Owner
    m_owner = re.search(r"Owner:?\s*\n+([^\n]+)", body_text, re.I)
    if not m_owner:
        m_owner = re.search(r"Owner:?\s+([^\n]+)", body_text, re.I)
    if m_owner:
        record["owner_name"] = m_owner.group(1).strip()

    # 3. Situs / Address
    m_situs = re.search(r"Situs:?\s*\n+([^\n]+(?:\n+[^\n]+)?)", body_text, re.I)
    if m_situs:
        raw_addr = m_situs.group(1).replace("\n", ", ").strip()
        cleaned_addr = re.sub(r"\s*,\s*", ", ", raw_addr)
        record["property_address"] = cleaned_addr
        record["situs_address"] = cleaned_addr

    # 4. Total Amount Due
    m_total = re.search(r"Total\s*Amount\s*Due:?\s*\n*\$?\s*([\d,]+(?:\.\d{2})?)", body_text, re.I)
    if m_total:
        val = float(m_total.group(1).replace(",", ""))
        record["total_amount_due"] = val
        record["amount_due"] = val
        if val > 0:
            record["tax_status"] = "Unpaid"
            record["status"] = "Unpaid"
        else:
            record["tax_status"] = "PAID"
            record["status"] = "PAID"

    # 5. Account History parsing
    history = []
    history_patterns = (
        r"(\d{4}\s+Annual\s+Bill)\s*\n+\s*\$?([\d,]+(?:\.\d{2})?)\s+(Paid(?:\s+\$[\d,]+(?:\.\d{2})?)?|Unpaid|Delinquent)([^\n]*)",
        r"(\d{4})\s+Annual\s+Bill[^\n]*\n[^\n]*\$?([\d,]+(?:\.\d{2})?)[^\n]*(Paid|Unpaid|Delinquent)[^\n]*",
    )
    seen_years: set[str] = set()
    for pattern in history_patterns:
        for hm in re.finditer(pattern, body_text, re.I):
            bill_title = hm.group(1).strip()
            if bill_title.isdigit() and len(bill_title) == 4:
                bill_title = f"{bill_title} Annual Bill"
            due_amt = hm.group(2).strip()
            status_str = hm.group(3).strip()
            extra = hm.group(4).strip() if hm.lastindex and hm.lastindex >= 4 else ""

            year = bill_title.split()[0]
            if year in seen_years:
                continue
            seen_years.add(year)

            m_date = re.search(r"(\d{2}/\d{2}/\d{4})", extra)
            p_date = m_date.group(1) if m_date else ""
            m_rec = re.search(r"Receipt\s*#?([A-Za-z0-9\-_]+)", extra)
            p_receipt = m_rec.group(1) if m_rec else ""

            history.append({
                "bill": bill_title,
                "year": year,
                "amount_due": f"${due_amt}",
                "status": status_str,
                "payment_date": p_date,
                "receipt_number": p_receipt,
            })

    if not history:
        for ym in re.finditer(r"\b(19|20)\d{2}\s+Annual\s+Bill\b", body_text, re.I):
            year = ym.group(0).split()[0]
            if year not in seen_years:
                seen_years.add(year)
                history.append({"bill": f"{year} Annual Bill", "year": year, "status": "", "amount_due": ""})

    record["account_history"] = history
    record["_source_url"] = page.url or ""
    if history:
        top_bill = history[0]
        record["current_bill_year"] = top_bill.get("year", "2026")
        record["tax_year"] = int(top_bill.get("year", "2026")) if top_bill.get("year", "").isdigit() else 2026
        top_status = top_bill.get("status", "")
        if "Unpaid" in top_status:
            record["tax_status"] = "Unpaid"
            record["status"] = "Unpaid"
            # If prior year bills are also unpaid, it is delinquent
            if len(history) > 1 and any("Unpaid" in h.get("status", "") for h in history[1:]):
                record["is_delinquent"] = True
        elif "Paid" in top_status:
            record["tax_status"] = "PAID"
            record["status"] = "PAID"
            # Parse amount paid
            m_paid = re.search(r"\$([\d,]+(?:\.\d{2})?)", top_status)
            if m_paid:
                record["amount_paid"] = float(m_paid.group(1).replace(",", ""))

    return record


def _unique_tax_years_sorted_desc(years: list[str]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for y in years:
        ys = str(y or "").strip()
        if not ys.isdigit() or len(ys) != 4:
            continue
        if ys in seen:
            continue
        seen.add(ys)
        clean.append(ys)
    return sorted(clean, key=int, reverse=True)


def years_from_account_history(account_history: list[dict[str, Any]] | None) -> list[str]:
    if not account_history:
        return []
    return _unique_tax_years_sorted_desc(
        [str(h.get("year") or "") for h in account_history if h.get("year")]
    )


TAX_ANNUAL_BILL_LINKS_JS = r"""
() => {
    const seen = new Set();
    const links = [];
    const isVisible = (el) => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
    };
    document.querySelectorAll('a').forEach((a) => {
        if (!isVisible(a)) return;
        const text = (a.innerText || '').replace(/\s+/g, ' ').trim();
        const m = text.match(/^(\d{4})\s+Annual\s+Bill$/i);
        if (!m) return;
        const year = m[1];
        if (seen.has(year)) return;
        seen.add(year);
        const href = a.getAttribute('href') || '';
        let selector = '';
        if (href) {
            selector = 'a[href="' + href.replace(/"/g, '\\"') + '"]';
        } else {
            selector = 'a:has-text("' + text.replace(/"/g, '\\"') + '")';
        }
        links.push({ year, label: text, selector, href });
    });
    return links.sort((a, b) => Number(b.year) - Number(a.year));
}
"""

TAX_HISTORY_SECTION_JS = r"""
() => {
    const markers = ['account history', 'annual bill', 'bill history', 'tax bills', 'payment history'];
    const isVisible = (el) => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.height > 120 && r.width > 200;
    };
    let best = null;
    let bestScore = 0;
    document.querySelectorAll('section, main, article, table, ul, div').forEach((el) => {
        if (!isVisible(el)) return;
        const text = (el.innerText || '').slice(0, 4000).toLowerCase();
        if (!/\b(19|20)\d{2}\b/.test(text)) return;
        let sc = 0;
        markers.forEach((m) => { if (text.includes(m)) sc += 30; });
        const years = text.match(/\b(19|20)\d{2}\b/g) || [];
        sc += Math.min(years.length, 12) * 5;
        if (sc > bestScore) {
            bestScore = sc;
            best = el;
        }
    });
    if (!best) return null;
    if (best.id && !/^\d/.test(best.id)) return { selector: '#' + CSS.escape(best.id), score: bestScore };
    const dt = best.getAttribute('data-test');
    if (dt) return { selector: '[data-test="' + dt.replace(/"/g, '\\"') + '"]', score: bestScore };
    return { selector: 'body', score: bestScore, fallback_full_page: true };
}
"""


def _plausible_tax_bill_year(year: str) -> bool:
    if not str(year or "").isdigit() or len(str(year)) != 4:
        return False
    y = int(year)
    current = __import__("datetime").date.today().year
    return 1990 <= y <= current + 1


def tax_search_result_link_matches_query(link_text: str, query_value: str) -> bool:
    """True when a VisualGov-style bill row belongs to the searched property (not bulk county hits)."""
    if not link_text or not query_value:
        return False
    q = str(query_value).strip()
    t = str(link_text).strip()
    if not q or not t:
        return False
    if q.lower() in t.lower():
        return True
    q_compact = re.sub(r"[\s\-]+", "", q, flags=re.I).lower()
    t_compact = re.sub(r"[\s\-]+", "", t, flags=re.I).lower()
    if len(q_compact) >= 10 and q_compact in t_compact:
        return True
    q_digits = re.sub(r"\D", "", q)
    t_digits = re.sub(r"\D", "", t)
    t_core = re.sub(r"20\d{2}\s*$", "", t_digits)
    if len(q_digits) >= 10 and q_digits in t_core:
        return True
    return False


TAX_SEARCH_RESULT_LINKS_JS = r"""
(query) => {
  function esc(s) {
    return (s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }
  function isVisible(el) {
    try {
      const s = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 4 && r.height > 4;
    } catch (_) { return false; }
  }
  function queryMatchesLink(q, t) {
    const qq = String(q || '').trim();
    const tt = String(t || '').trim();
    if (!qq || !tt) return false;
    if (tt.toLowerCase().includes(qq.toLowerCase())) return true;
    const qCompact = qq.replace(/[\s\-]+/g, '').toLowerCase();
    const tCompact = tt.replace(/[\s\-]+/g, '').toLowerCase();
    if (qCompact.length >= 10 && tCompact.includes(qCompact)) return true;
    const qd = qq.replace(/\D/g, '');
    let td = tt.replace(/\D/g, '');
    td = td.replace(/20\d{2}$/, '');
    if (qd.length >= 10 && td.includes(qd)) return true;
    return false;
  }
  const q = String(query || '').trim();
  const body = (document.body ? document.body.innerText : '').toLowerCase();
  const listing =
    body.includes('records found') ||
    body.includes('view or pay a tax bill') ||
    body.includes('click the property number below') ||
    body.includes('total') && body.includes('property number');
  const links = [];
  const scopes = document.querySelectorAll('table tbody tr a, table a, main a, #content a, a[href]');
  scopes.forEach((a) => {
    try {
      if (!isVisible(a)) return;
      if (a.closest('nav, header, footer, .navbar, [role="navigation"]')) return;
      const t = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length < 8 || t.length > 200) return;
      if (/^(search|previous|next|home|menu|cart|privacy)$/i.test(t)) return;
      const ym = t.match(/(20\d{2})\s*$/);
      const year = ym ? ym[1] : '';
      if (!queryMatchesLink(q, t)) return;
      if (!year && t.replace(/\D/g, '').length < 8) return;
      links.push({
        text: t,
        year: year,
        href: a.getAttribute('href') || '',
        selector: `a:has-text("${esc(t)}")`,
      });
    } catch (_) {}
  });
  const seen = new Set();
  const uniq = [];
  for (const l of links) {
    if (seen.has(l.text)) continue;
    seen.add(l.text);
    uniq.push(l);
  }
  uniq.sort((a, b) => (parseInt(b.year, 10) || 0) - (parseInt(a.year, 10) || 0));
  return { listing: listing || uniq.length > 1, links: uniq };
}
"""


def _filter_tax_search_result_links(links: list[dict[str, Any]], query_value: str) -> list[dict[str, Any]]:
    return [
        l
        for l in links or []
        if tax_search_result_link_matches_query(str(l.get("text") or ""), query_value)
    ]


async def discover_tax_search_result_links(page: Page, query_value: str) -> dict[str, Any]:
    """Clickable tax search rows for the queried property only (VisualGov multi-year links)."""
    merged: dict[str, Any] = {"listing": False, "links": []}
    best_match_count = 0
    for frame in page.frames:
        try:
            res = await frame.evaluate(TAX_SEARCH_RESULT_LINKS_JS, query_value)
        except Exception:
            continue
        if not res:
            continue
        links = _filter_tax_search_result_links(res.get("links") or [], query_value)
        match_count = len(links)
        if match_count > best_match_count:
            best_match_count = match_count
            merged = {**res, "links": links}
    if best_match_count == 0:
        return {"listing": False, "links": []}
    return merged


def _pick_tax_link_for_year(
    links: list[dict[str, Any]], *, year: str, link_text: str, parcel_id: str
) -> dict[str, Any] | None:
    year = (year or "").strip()
    link_text = (link_text or "").strip()
    if link_text:
        for l in links:
            if (l.get("text") or "").strip() == link_text:
                return l
    if year and year != "unknown":
        for l in links:
            if str(l.get("year") or "").strip() == year:
                if tax_search_result_link_matches_query(str(l.get("text") or ""), parcel_id):
                    return l
    return None


async def capture_all_tax_search_result_records(
    page: Page,
    parcel_id: str,
    *,
    results_list_url: str | None = None,
    portal_search_url: str | None = None,
    search_input_selector: str | None = None,
    search_submit_selector: str | None = None,
    workflow_run_id: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Open each tax search result link (one per bill year), capture screenshot/PDF/extract,
    then re-run the portal search before the next year (avoids stale bulk result lists).
    """
    discovery = await discover_tax_search_result_links(page, parcel_id)
    links = _filter_tax_search_result_links(discovery.get("links") or [], parcel_id)
    if len(links) < 2 and not (discovery.get("listing") and len(links) == 1):
        if not links:
            logger.warning(
                "No tax search result links match query %s; refusing bulk/unrelated rows.",
                parcel_id,
            )
        return [], {}

    logger.info(
        "Tax search multi-year capture: %d link(s) matching %s",
        len(links),
        parcel_id,
    )

    portal_url = (portal_search_url or results_list_url or page.url or "").strip()
    targets = [
        {
            "year": str(l.get("year") or "").strip() or "unknown",
            "text": (l.get("text") or "").strip(),
        }
        for l in links
    ]

    docs: list[dict[str, Any]] = []
    records_by_year: dict[str, Any] = {}
    captured_years: list[str] = []

    for idx, target in enumerate(targets):
        year = target["year"]
        if idx > 0:
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=18000)
                await page.wait_for_timeout(600)
            except Exception:
                pass
            refreshed = await refresh_tax_search_results_for_query(
                page,
                parcel_id,
                input_selector=search_input_selector,
                submit_selector=search_submit_selector,
                portal_search_url=portal_url,
            )
            if not refreshed:
                logger.warning("Skipping tax year %s; could not refresh search for %s", year, parcel_id)
                continue

        discovery_now = await discover_tax_search_result_links(page, parcel_id)
        live_links = _filter_tax_search_result_links(discovery_now.get("links") or [], parcel_id)
        link = _pick_tax_link_for_year(
            live_links, year=year, link_text=target["text"], parcel_id=parcel_id
        )
        if not link:
            logger.warning("No live link for tax year %s (%s)", year, target["text"][:80])
            continue

        sel = (link.get("selector") or "").strip()
        if not sel:
            continue
        opened = False
        for frame in page.frames:
            try:
                loc = frame.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=2500):
                    continue
                await loc.click(timeout=6000)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            logger.warning("Could not open tax search result link: %s", link.get("text"))
            continue

        try:
            await page.wait_for_timeout(2500)
            await wait_for_tax_details_loaded(page, timeout_seconds=14)
            rec = await extract_tax_collector_record(page, query_value=parcel_id)
            if rec:
                records_by_year[year] = rec
            clean_key = _clean_storage_key(f"{parcel_id}_{year}")
            shot = await capture_tax_collector_screenshot(page, clean_key, workflow_run_id=workflow_run_id)
            if shot:
                shot["capture_kind"] = "tax_search_result_year"
                shot["tax_year"] = year
                docs.append(shot)
            bill = await download_official_tax_bill(page, clean_key, workflow_run_id=workflow_run_id)
            if bill:
                bill["tax_year"] = year
                docs.append(bill)
            captured_years.append(year)
            logger.info("Captured tax search result for year %s (%s)", year, link.get("text", "")[:80])
        except Exception as cap_err:
            logger.warning("Tax search result capture for %s failed: %s", year, cap_err)

    meta = {
        "tax_search_results_years": captured_years,
        "tax_search_results_by_year": records_by_year,
        "tax_search_results_count": len(captured_years),
    }
    return docs, meta


async def discover_tax_bill_year_targets(page: Page) -> list[dict[str, Any]]:
    """Locate Annual Bill links in the account history table across frames."""
    merged: dict[str, dict[str, Any]] = {}
    for frame in page.frames:
        try:
            rows = await frame.evaluate(TAX_ANNUAL_BILL_LINKS_JS)
        except Exception:
            continue
        for row in rows or []:
            year = str(row.get("year") or "").strip()
            if not _plausible_tax_bill_year(year):
                continue
            merged[year] = {**row, "frame_url": frame.url}
    return [merged[y] for y in sorted(merged.keys(), key=int, reverse=True)]


async def navigate_to_tax_account_summary(page: Page) -> bool:
    """Return to the account summary / history view from bill detail or search."""
    body_l = (await _aggregate_visible_body_text(page)).lower()
    if "account history" in body_l and "annual bill" in body_l:
        if "bill details" not in body_l or body_l.count("annual bill") > 1:
            return True

    for frame in page.frames:
        for sel in (
            'a:has-text("Account Summary")',
            'a[href*="account-summary" i]',
            'nav a:has-text("Summary")',
            'ol.breadcrumb a:has-text("Account Summary")',
            '.breadcrumb a:has-text("Account Summary")',
        ):
            try:
                loc = frame.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=600):
                    continue
                await loc.click(timeout=4000)
                await page.wait_for_timeout(1500)
                return True
            except Exception:
                continue
    return "account history" in (await _aggregate_visible_body_text(page)).lower()


async def scroll_account_history_into_view(page: Page) -> None:
    for frame in page.frames:
        for sel in (
            'text=Account History',
            'text=Annual Bill',
            'table:has(a:has-text("Annual Bill"))',
            '[data-test*="history" i]',
        ):
            try:
                loc = frame.locator(sel).first
                if await loc.count() > 0:
                    await loc.scroll_into_view_if_needed(timeout=5000)
                    await page.wait_for_timeout(400)
                    return
            except Exception:
                continue


async def _resolve_frame_for_target(page: Page, target: dict[str, Any]) -> Frame:
    frame_url = (target.get("frame_url") or "").strip()
    if frame_url:
        for f in page.frames:
            if f.url == frame_url:
                return f
    return await find_tax_frame(page)


async def capture_tax_collector_screenshot(
    page: Page,
    parcel_id: str,
    workflow_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Capture full-page high-resolution screenshot of the tax portal account view."""
    try:
        clean_id = _clean_storage_key(parcel_id)
        dest_dir = get_tax_storage_dir(clean_id)
        screenshot_path = dest_dir / f"tax_collector_account_{clean_id}.png"

        await page.screenshot(path=str(screenshot_path), full_page=True)
        if screenshot_path.exists() and screenshot_path.stat().st_size > 5000:
            logger.info("Captured Tax Collector Screenshot: %s (%d bytes)", screenshot_path, screenshot_path.stat().st_size)
            return {
                "document_type": "Tax Collector Report",
                "document_title": f"Tax Collector Property Account - {clean_id}.png",
                "file_path": str(screenshot_path),
                "source_url": page.url,
                "document_url": page.url,
            }
    except Exception as exc:
        logger.warning("Could not capture tax collector screenshot: %s", exc)
    return None


async def capture_tax_bill_history_index_screenshot(
    page: Page,
    parcel_id: str,
    *,
    years_indexed: list[str] | None = None,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """
    Screenshot the bill-history index (all visible years) and return an index manifest.
    """
    clean_id = _clean_storage_key(parcel_id)
    dest_dir = get_tax_storage_dir(clean_id)
    manifest: list[dict[str, Any]] = []
    index_doc: dict[str, Any] | None = None

    try:
        await navigate_to_tax_account_summary(page)
        await scroll_account_history_into_view(page)
        await page.wait_for_timeout(500)

        index_path = dest_dir / f"tax_bill_history_index_{clean_id}.png"
        await page.screenshot(path=str(index_path), full_page=True)

        if index_path.exists() and index_path.stat().st_size > 3000:
            index_doc = {
                "document_type": "Tax Bill History Index",
                "document_title": f"Tax Bill History Index - {clean_id}.png",
                "file_path": str(index_path),
                "source_url": page.url,
                "document_url": page.url,
                "capture_kind": "history_index",
            }
            manifest.append(
                {
                    "index": 0,
                    "kind": "history_index",
                    "year": None,
                    "document_title": index_doc["document_title"],
                    "file_path": index_doc["file_path"],
                    "years_listed": years_indexed or [],
                }
            )
    except Exception as exc:
        logger.warning("Could not capture tax bill history index screenshot: %s", exc)

    return index_doc, manifest


async def _wait_for_annual_bill_detail(page: Page, year: str, *, timeout_s: float = 12.0) -> bool:
    import time as _time

    deadline = _time.time() + timeout_s
    needle = f"{year} annual bill"
    while _time.time() < deadline:
        body = (await _aggregate_visible_body_text(page)).lower()
        if needle in body and (
            "ad valorem" in body
            or "bill details" in body
            or "amount due" in body
            or "print (pdf)" in body
        ):
            return True
        await page.wait_for_timeout(450)
    return False


async def _screenshot_tax_year_view(
    page: Page,
    frame: Frame,
    parcel_id: str,
    year: str,
    *,
    click_selector: str | None = None,
    content_hash_seen: set[str] | None = None,
) -> Optional[dict[str, Any]]:
    clean_id = _clean_storage_key(parcel_id)
    dest_dir = get_tax_storage_dir(clean_id)
    shot_path = dest_dir / f"tax_bill_year_{year}_{clean_id}.png"

    try:
        await navigate_to_tax_account_summary(page)
        bill_label = f"{year} Annual Bill"
        selectors = []
        if click_selector:
            selectors.append(click_selector)
        selectors.extend(
            [
                f'a:has-text("{bill_label}")',
                f'a >> text=/{year}\\s+Annual\\s+Bill/i',
            ]
        )

        clicked = False
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=800):
                    continue
                await loc.scroll_into_view_if_needed(timeout=4000)
                await loc.click(timeout=4000)
                clicked = True
                break
            except Exception:
                for fr in page.frames:
                    try:
                        loc = fr.locator(sel).first
                        if await loc.count() == 0 or not await loc.is_visible(timeout=600):
                            continue
                        await loc.scroll_into_view_if_needed(timeout=3000)
                        await loc.click(timeout=4000)
                        clicked = True
                        frame = fr
                        break
                    except Exception:
                        continue
            if clicked:
                break

        if not clicked:
            logger.info("No Annual Bill link for year %s on account summary", year)
            return None

        if not await _wait_for_annual_bill_detail(page, year):
            logger.warning("Bill detail view did not load for year %s", year)
            return None

        await page.wait_for_timeout(600)
        await page.screenshot(path=str(shot_path), full_page=True)

        if shot_path.exists() and shot_path.stat().st_size > 2500:
            import hashlib

            digest = hashlib.md5(shot_path.read_bytes()).hexdigest()
            if content_hash_seen is not None:
                if digest in content_hash_seen:
                    logger.info("Skipping duplicate tax year screenshot for %s", year)
                    shot_path.unlink(missing_ok=True)
                    await navigate_to_tax_account_summary(page)
                    return None
                content_hash_seen.add(digest)

            doc = {
                "document_type": "Tax Bill Year Screenshot",
                "document_title": f"Tax Bill Year {year} - {clean_id}.png",
                "file_path": str(shot_path),
                "source_url": page.url,
                "document_url": page.url,
                "capture_kind": "bill_year",
                "tax_year": year,
                "content_md5": digest,
            }
            await navigate_to_tax_account_summary(page)
            return doc
    except Exception as exc:
        logger.warning("Could not capture tax year %s screenshot: %s", year, exc)
        try:
            await navigate_to_tax_account_summary(page)
        except Exception:
            pass
    return None


async def capture_tax_year_screenshots(
    page: Page,
    parcel_id: str,
    *,
    account_history: list[dict[str, Any]] | None = None,
    recent_year_count: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Capture per-year screenshots for discovered bill years, prioritizing the most
    recent `recent_year_count` tax years. Returns (documents, index_entries, recent_years).
    """
    await navigate_to_tax_account_summary(page)
    discovered = await discover_tax_bill_year_targets(page)
    dom_years = [str(d.get("year")) for d in discovered if d.get("year") and _plausible_tax_bill_year(str(d.get("year")))]
    history_years = [
        y for y in years_from_account_history(account_history) if _plausible_tax_bill_year(y)
    ]
    all_years = _unique_tax_years_sorted_desc(dom_years + history_years)
    year_targets = {str(d.get("year")): d for d in discovered}

    recent_targets = all_years[: max(1, recent_year_count)]
    docs: list[dict[str, Any]] = []
    index_entries: list[dict[str, Any]] = []
    captured_years: set[str] = set()
    content_hashes: set[str] = set()
    next_index = 1

    index_path_hint = get_tax_storage_dir(_clean_storage_key(parcel_id)) / f"tax_bill_history_index_{_clean_storage_key(parcel_id)}.png"
    if index_path_hint.exists():
        import hashlib

        content_hashes.add(hashlib.md5(index_path_hint.read_bytes()).hexdigest())

    for year in recent_targets:
        target = year_targets.get(year)
        frame = await _resolve_frame_for_target(page, target or {}) if target else await find_tax_frame(page)
        shot = await _screenshot_tax_year_view(
            page,
            frame,
            parcel_id,
            year,
            click_selector=(target or {}).get("selector"),
            content_hash_seen=content_hashes,
        )
        if shot:
            docs.append(shot)
            index_entries.append(
                {
                    "index": next_index,
                    "kind": "bill_year",
                    "year": year,
                    "document_title": shot["document_title"],
                    "file_path": shot["file_path"],
                    "source_label": (target or {}).get("label"),
                    "priority": "recent",
                }
            )
            captured_years.add(year)
            next_index += 1

    recent_captured = [y for y in recent_targets if y in captured_years]
    return docs, index_entries, recent_captured


async def capture_tax_collector_document_suite(
    page: Page,
    parcel_id: str,
    *,
    account_history: list[dict[str, Any]] | None = None,
    recent_year_count: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Full tax capture: account summary, bill history index, all indexed years,
    and explicit recent-year screenshots.
    """
    docs: list[dict[str, Any]] = []

    await ensure_tax_account_detail_loaded(page, parcel_id)
    await navigate_to_tax_account_summary(page)
    if not account_history:
        quick = await extract_tax_collector_record(page, query_value=parcel_id)
        account_history = quick.get("account_history") or account_history

    year_targets = await discover_tax_bill_year_targets(page)
    dom_years = [
        str(d.get("year"))
        for d in year_targets
        if d.get("year") and _plausible_tax_bill_year(str(d.get("year")))
    ]
    history_years = [
        y for y in years_from_account_history(account_history) if _plausible_tax_bill_year(y)
    ]
    indexed_years = _unique_tax_years_sorted_desc(dom_years + history_years)

    index_doc, index_manifest = await capture_tax_bill_history_index_screenshot(
        page, parcel_id, years_indexed=indexed_years
    )
    if index_doc:
        docs.append(index_doc)

    await navigate_to_tax_account_summary(page)
    account_shot = await capture_tax_collector_screenshot(page, parcel_id)
    if account_shot:
        account_shot["capture_kind"] = "account_summary"
        docs.append(account_shot)

    year_docs, year_index, recent_years = await capture_tax_year_screenshots(
        page,
        parcel_id,
        account_history=account_history,
        recent_year_count=recent_year_count,
    )
    docs.extend(year_docs)

    full_index = list(index_manifest) + year_index
    for i, row in enumerate(full_index):
        row["index"] = i

    suite_meta = {
        "tax_screenshot_index": full_index,
        "tax_years_indexed": indexed_years,
        "tax_recent_year_screenshots": recent_years,
        "tax_screenshot_count": len(docs),
    }
    return docs, suite_meta


async def download_official_tax_bill(
    page: Page,
    parcel_id: str,
    workflow_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Download official property tax bill PDF from the Print (PDF) link."""
    try:
        clean_id = _clean_storage_key(parcel_id)
        dest_dir = get_tax_storage_dir(clean_id)
        target_frame = await find_tax_frame(page)

        print_btn = target_frame.locator("a:has-text('Print (PDF)'), a[href*='/print'], a.print-link-with-icon").first
        if await print_btn.count() == 0:
            print_btn = page.locator("a:has-text('Print (PDF)'), a[href*='/print'], a.print-link-with-icon").first

        if await print_btn.count() > 0:
            logger.info("Found official Tax Bill Print (PDF) button, downloading...")
            try:
                async with page.expect_download(timeout=15000) as download_info:
                    await print_btn.evaluate("el => el.click()")
                download = await download_info.value
                filename = download.suggested_filename or f"tax_bill_{clean_id}.pdf"
                pdf_path = dest_dir / filename
                await download.save_as(str(pdf_path))

                if pdf_path.exists() and pdf_path.stat().st_size > 5000:
                    logger.info("Downloaded official Tax Bill PDF: %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
                    return {
                        "document_type": "Property Tax Bill",
                        "document_title": filename,
                        "file_path": str(pdf_path),
                        "source_url": page.url,
                        "document_url": f"{page.url}#/bill/print",
                    }
            except Exception as dl_err:
                logger.warning("expect_download error for tax bill: %s", dl_err)
                try:
                    href = (await print_btn.get_attribute("href") or "").strip()
                    from app.browser.property_capture_prep import (
                        _href_looks_like_print_route,
                        capture_print_route_as_pdf,
                    )

                    if _href_looks_like_print_route(href):
                        pdf_path = dest_dir / f"tax_bill_{clean_id}.pdf"
                        if await capture_print_route_as_pdf(
                            page, href, pdf_path, query_value=parcel_id
                        ):
                            return {
                                "document_type": "Property Tax Bill",
                                "document_title": pdf_path.name,
                                "file_path": str(pdf_path),
                                "source_url": page.url,
                                "document_url": href,
                            }
                except Exception as print_pdf_err:
                    logger.debug("Tax bill print-route PDF fallback: %s", print_pdf_err)
    except Exception as exc:
        logger.warning("Could not download official tax bill PDF: %s", exc)
    return None


async def capture_and_register_tax_documents(
    page: Page,
    parcel_id: str,
    doc_service: Any,
    workflow_run_id: Optional[uuid.UUID] = None,
    source_configuration_id: Optional[uuid.UUID] = None,
    account_history: list[dict[str, Any]] | None = None,
    recent_year_count: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Capture tax screenshots (index + recent years) and bill PDF; register artifacts."""
    from app.core.config import settings

    recent_n = int(
        getattr(settings, "TAX_RECENT_YEAR_SCREENSHOT_COUNT", recent_year_count) or recent_year_count
    )

    docs_to_register, suite_meta = await capture_tax_collector_document_suite(
        page,
        parcel_id,
        account_history=account_history,
        recent_year_count=recent_n,
    )

    bill_doc = await download_official_tax_bill(page, parcel_id)
    if bill_doc:
        docs_to_register.append(bill_doc)

    if not docs_to_register:
        return [], suite_meta

    registered_meta = await register_downloaded_documents(
        doc_service=doc_service,
        downloaded_docs=docs_to_register,
        workflow_run_id=workflow_run_id,
        source_configuration_id=source_configuration_id,
    )
    return registered_meta, suite_meta

