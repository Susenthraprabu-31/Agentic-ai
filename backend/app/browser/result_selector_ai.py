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
    const taxResultsListing = bodyText.includes('records found') ||
        bodyText.includes('showing records') ||
        bodyText.includes('view or pay a tax bill') ||
        bodyText.includes('click the property number below') ||
        /showing\\s+\\d+\\s*-\\s*\\d+\\s+of\\s+\\d+\\s+records/i.test(bodyText);
    const pathLow = (window.location.pathname || '').toLowerCase();
    const assessorResultsGrid = bodyText.includes('property search results') ||
        pathLow.includes('searchresults') || pathLow.includes('mastersearch') ||
        (bodyText.includes('search results') && (bodyText.includes('homesteaded') || /\\bqv\\b/.test(bodyText)));
    const isAlreadyDetail = !taxResultsListing && !assessorResultsGrid && (
        (bodyText.includes('parcel details') || bodyText.includes('property details') || bodyText.includes('assessment details')) ||
        (bodyText.includes('account history') && bodyText.includes('annual bill')) ||
        (bodyText.includes('real estate account') && bodyText.includes('account summary'))
    ) && (
        !bodyText.includes('search results') &&
        !bodyText.includes('basic search results') &&
        !bodyText.includes('showing 1 through') &&
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
        'table tbody tr td:first-child, table tbody tr td:nth-child(2), ' +
        'a[href^="#"], main a, #content a, .content a'
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
            if (/next|previous|first|last|page \\d|export|print|help|select all|download|filter|excel|csv/i.test(txt)) return;
            if (/^(qv|map|quick view)$/i.test(txt.trim())) return;
            if (/^(parcel id|address|owner name|sales|real estate|tangible)$/i.test(txt)) return;
            const taxYearLink = /^R\\s*-\\s*[\\d\\-]+\\s*-\\s*20\\d{2}$/i.test(txt);
            if (tag === 'a' && href.match(/^#(parcel|address|owner|sales|realestate)/i)) return;
            if (tag === 'a' && href.startsWith('#') && !href.includes('detail') && !href.includes('parcel') && !href.includes('/') && !taxYearLink) return;
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
            } else if (tag === 'a' && txt && (txt.length <= 40 || taxYearLink)) {
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
    
    // Entire table rows (Tyler / CommonSearch: "Click rows to view property details")
    const resultsHint = bodyText.includes('click rows to view') ||
        bodyText.includes('records found') || bodyText.includes('showing 1 through') ||
        bodyText.includes('showing 1 to');
    const bodyRows = document.querySelectorAll('table tbody tr');
    bodyRows.forEach((tr, rowIdx) => {
        try {
            if (tr.querySelector('th') || tr.closest('thead')) return;
            const txt = (tr.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!txt || txt.length < 12) return;
            const tds = tr.querySelectorAll('td');
            if (tds.length < 2) return;
            const onclick = tr.getAttribute('onclick') || '';
            let cursor = 'auto';
            try { cursor = window.getComputedStyle(tr).cursor || 'auto'; } catch (e) {}
            const clickable = onclick || cursor === 'pointer' || resultsHint;
            if (!clickable) return;
            if (/\\bqv\\b/.test(txt) && /\\bmap\\b/.test(txt)) return;
            out.push({
                index: rowIdx,
                tag: 'tr',
                text: txt.slice(0, 220),
                href: '',
                data_bind: '',
                is_cell: false,
                is_row: true,
                row_context: txt.slice(0, 220),
                selector: `table tbody tr:nth-of-type(${rowIdx + 1})`,
            });
        } catch (_) {}
    });

    // Tax collector search results (floridatax, county-taxes, etc.): primary blue account/owner link
    if (taxResultsListing) {
        document.querySelectorAll('a[href]').forEach((a) => {
            try {
                if (a.closest('nav, header, footer, .navbar, .menu, [role="navigation"]')) return;
                const rect = a.getBoundingClientRect();
                const style = window.getComputedStyle(a);
                if (style.display === 'none' || style.visibility === 'hidden' || rect.width === 0) return;
                const txt = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                if (txt.length < 6 || txt.length > 160) return;
                if (/^(previous|next|search|home|help|menu|cart|property tax|business|misc|español)$/i.test(txt)) return;
                const href = (a.getAttribute('href') || '').toLowerCase();
                const taxRowLink = /^R\\s*-\\s*[\\d\\-]+\\s*-\\s*20\\d{2}$/i.test(txt);
                if (!href || (href === '#' && !taxRowLink) || href.startsWith('javascript:void')) return;
                const block = a.closest('div, section, article, li, p, td') || a.parentElement;
                const context = (block ? block.innerText : txt).replace(/\\s+/g, ' ').slice(0, 420);
                const accountOwner = /^[\\w\\-]+\\s*[-–]\\s*.+/i.test(txt);
                const hasId = /[\\dA-Z]{3,}/.test(txt);
                if (!accountOwner && !hasId) return;
                let sel = '';
                if (txt) {
                    sel = `a:has-text("${esc(txt.slice(0, 80))}")`;
                } else if (href) {
                    sel = `a[href*="${esc(href.split('?')[0].slice(-24))}"]`;
                }
                if (!sel || seen.has(sel)) return;
                seen.add(sel);
                out.push({
                    index: out.length,
                    tag: 'a',
                    text: txt,
                    href: a.getAttribute('href') || '',
                    data_bind: '',
                    is_cell: false,
                    is_tax_result_link: true,
                    row_context: context,
                    selector: sel,
                });
            } catch (_) {}
        });
    }

    return { is_already_detail: false, candidates: out.slice(0, 40) };
}
"""

RESULT_TABLE_ROWS_JS = r"""
() => {
    const bodyText = (document.body ? document.body.innerText : '').toLowerCase();
    const resultsHint = bodyText.includes('click rows to view') ||
        bodyText.includes('records found') || bodyText.includes('showing 1 through') ||
        bodyText.includes('showing 1 to');
    const rows = [];
    const trs = Array.from(document.querySelectorAll('table tbody tr'));
    trs.forEach((tr, index) => {
        if (tr.querySelector('th') || tr.closest('thead')) return;
        const text = (tr.innerText || '').replace(/\s+/g, ' ').trim();
        if (!text || text.length < 12) return;
        const tds = tr.querySelectorAll('td');
        if (tds.length < 2) return;
        const onclick = tr.getAttribute('onclick') || '';
        let cursor = 'auto';
        try { cursor = window.getComputedStyle(tr).cursor || 'auto'; } catch (e) {}
        const tdClickable = Array.from(tds).some((td) => {
            const oc = td.getAttribute('onclick') || '';
            const cl = (td.className || '').toLowerCase();
            return Boolean(oc) || cl.includes('link') || cl.includes('pointer');
        });
        const clickable = onclick || cursor === 'pointer' || tdClickable || resultsHint;
        if (!clickable) return;
        rows.push({ index, text: text.slice(0, 320), clickable, has_onclick: Boolean(onclick) });
    });
    return { resultsHint, rows: rows.slice(0, 30) };
}
"""

PROMPT_TEMPLATE = """You are an automated web browser agent.
A property search was just executed for query value: "{query_value}" (query_type: {query_type}).
The browser is currently on page: {url} (title: "{title}")

Here are candidate result links, table cells, and rows found on the page:
{candidates_json}

Determine if this page is a search results listing / table where the user needs to click the parcel number, link, table cell, or entire table row (tr) to open the full property details / appraisal page.
Many county portals (CommonSearch) use clickable <tr> rows with hint text "Click rows to view property details".
For address searches, match the row whose Site Address / Owner columns contain the searched street number and street name.
If YES:
1. Identify the best element to click (parcel link, td.link, owner/site address text link, or matching tbody tr row).
2. Provide the exact Playwright CSS selector to click it — prefer `a:has-text("...")` or `td` in the owner/address column, **never** bare `table tbody tr:nth-of-type(N)` when QV/MAP columns exist (row clicks open Quick View, not full details).
3. **Search results table — open full property details (critical):** Many county appraiser result grids add action columns with small controls such as **QV** (Quick View), **MAP**, or similar icons/buttons at the end of each row. **Never** set `click_selector` to those action-column controls — they open a quick-view modal, map, or popup, not the full property details page. Instead, click a **data-column** link in the same row: the **owner name** (Owner Name column), **site / location address** (Site Address, Location, Situs), or the **parcel / folio / control number** link when that column is the primary navigation to the detail page. Prefer owner name or site address when both are links and match the query; use parcel/control number when searching by parcel. Choose `click_selector` from candidate text/href in those columns only, not from candidates whose `text` is a short action label like QV or MAP.
On DeSoto / floridapa GIS: use Search Results tab — click the blue **Parcel ID** link matching the query exactly (not Certified Values rows, not Download/Print Report).
On floridapa / Schneider GIS map portals, after opening a parcel land on Parcel Details with owner/situs visible (often in an iframe). Do not use toolbar Print — the runner saves PDF to storage with Playwright.
On tax collector search results (e.g. floridatax, county-taxes, Phenix, VisualGov): pages say "records found" with a prominent blue link such as account/folio ID plus owner name (often "ID - OWNER"). Click that link to open the tax account — not Search, Previous/Next, or nav menu. Use row_context (mailing/property address) to match address or parcel queries when the link text is an account ID.
When the page shows "TO VIEW OR PAY A TAX BILL CLICK THE PROPERTY NUMBER BELOW" or "Total N records found" with multiple year links like `R - <property-number> - 2026`, each row is a separate tax bill year. Classify as is_results_page: true. If only ONE link must be chosen (legacy path), pick the newest year. When multiple matching links are listed, return is_results_page: true and describe the listing — downstream automation will open **every** matching result link in turn, capture each bill, and return to the list (do not click only one year when the table lists all years).
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


_DIRECTION_TOKENS = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "st", "dr", "rd", "ave", "blvd", "ln", "ct", "way"}


def _query_match_tokens(query_value: str, query_type: str) -> list[str]:
    q = re.sub(r"\s+", " ", (query_value or "").strip()).lower()
    tokens: list[str] = []
    if query_type == "address":
        for part in re.split(r"[\s,]+", q):
            p = part.strip().rstrip(".")
            if not p:
                continue
            if p in _DIRECTION_TOKENS:
                continue
            if p.isdigit() or len(p) >= 3:
                tokens.append(p)
    elif query_type == "owner":
        for part in re.split(r"[\s,]+", q):
            if len(part) >= 2:
                tokens.append(part)
    else:
        digits = re.sub(r"\D", "", q)
        if digits:
            tokens.append(digits)
        if q and len(q) >= 3:
            tokens.append(q)
    return list(dict.fromkeys(t for t in tokens if t))


def _score_row_against_query(row_text: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    blob = (row_text or "").lower()
    blob_digits = re.sub(r"\D", "", row_text or "")
    hits = 0
    for t in tokens:
        if t in blob:
            hits += 1
        else:
            td = re.sub(r"\D", "", t)
            if td and len(td) >= 6 and td in blob_digits:
                hits += 1
    return hits / len(tokens)


def _sanitize_playwright_selector(selector: str) -> str:
    """Collapse newlines and odd whitespace inside :has-text(...) selectors from LLM output."""
    if not selector:
        return selector
    out = re.sub(r"[\r\n]+", " ", selector)
    out = re.sub(r"\s+", " ", out).strip()

    def _clean_has_text(m: re.Match) -> str:
        inner = re.sub(r"\s+", " ", m.group(1)).strip()
        inner = inner.replace('"', '\\"')
        return f':has-text("{inner}")'

    out = re.sub(r':has-text\("((?:[^"\\]|\\.)*)"\)', _clean_has_text, out)
    return out


def ai_indicates_already_on_detail(ai_data: dict[str, Any]) -> bool:
    if not ai_data or ai_data.get("is_results_page"):
        return False
    if (ai_data.get("click_selector") or "").strip():
        return False
    blob = " ".join(
        str(ai_data.get(k) or "") for k in ("description", "reasoning", "link_text")
    ).lower()
    return any(
        p in blob
        for p in (
            "already the",
            "already displaying",
            "already on",
            "full property report",
            "property report",
            "not a search-results",
            "no search-results",
            "not a search results",
            "not a results listing",
        )
    )


def ai_indicates_no_matching_result(ai_data: dict[str, Any]) -> bool:
    """True when the LLM explicitly rejected all result rows for this query."""
    if not ai_data or ai_data.get("is_results_page") or (ai_data.get("click_selector") or "").strip():
        return False
    if ai_indicates_already_on_detail(ai_data):
        return False
    blob = " ".join(
        str(ai_data.get(k) or "")
        for k in ("description", "reasoning", "link_text")
    ).lower()
    if not blob.strip():
        return False
    return any(
        p in blob
        for p in (
            "no matching",
            "not found",
            "does not match",
            "did not match",
            "no result",
            "no matching property",
        )
    )


async def beacon_bulk_results_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if "pagetypeid=3" in url:
        return True
    try:
        snippet = await page.evaluate(
            "() => (document.body ? document.body.innerText : '').slice(0, 2500).toLowerCase()"
        )
        if "5000 results" in snippet or "5,000 results" in snippet:
            return True
        if "maximum number of records" in snippet:
            return True
    except Exception:
        pass
    return False


async def click_matching_result_table_row(
    page: Page,
    query_value: str,
    *,
    query_type: str = "parcel",
) -> dict[str, Any]:
    """Click the best-matching data row in a standard HTML results table (CommonSearch-style)."""
    try:
        await page.wait_for_selector("table tbody tr td", timeout=12000)
    except Exception:
        pass

    tokens = _query_match_tokens(query_value, query_type)
    bulk = await beacon_bulk_results_page(page)
    min_score = 0.5 if bulk and query_type == "address" else 0.34 if tokens else 0.0

    for scope in [page, *page.frames]:
        try:
            data = await scope.evaluate(RESULT_TABLE_ROWS_JS)
        except Exception:
            continue
        rows = data.get("rows") or []
        if not rows:
            continue

        best: dict[str, Any] | None = None
        best_score = -1.0
        for row in rows:
            score = _score_row_against_query(row.get("text", ""), tokens)
            if score > best_score:
                best_score = score
                best = row

        if best is None:
            continue
        if best_score < min_score:
            continue

        idx = int(best.get("index", 0))
        row_loc = scope.locator("table tbody tr").nth(idx)
        if await row_loc.count() == 0:
            continue
        try:
            await row_loc.scroll_into_view_if_needed(timeout=2500)
        except Exception:
            pass
        try:
            await row_loc.click(force=True, timeout=4000)
        except Exception:
            await row_loc.evaluate(
                """(tr) => {
                    if (typeof tr.onclick === 'function') tr.onclick();
                    tr.click();
                    const a = tr.querySelector('a[href]');
                    if (a) a.click();
                }"""
            )
        await page.wait_for_timeout(3500)
        return {
            "is_results_page": True,
            "clicked": True,
            "click_selector": f"table tbody tr:nth-child({idx + 1})",
            "link_text": (best.get("text") or "")[:80],
            "description": f"Opened property appraisal from results row ({(best.get('text') or '')[:60]})",
            "reasoning": f"Matched result row score={best_score:.2f} for tokens={tokens[:5]}",
        }

    return {"is_results_page": False, "clicked": False}


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

    from app.browser.property_capture_prep import (
        click_assessor_search_results_row,
        is_whole_results_row_selector,
        page_is_search_results_listing,
        recover_from_quick_view_modal,
    )

    if await page_is_search_results_listing(page):
        row_open = await click_assessor_search_results_row(
            page, query_value, query_type=query_type
        )
        if row_open.get("clicked"):
            logger.info(
                "Opened assessor search result via owner/address cell (not QV): %s",
                row_open.get("link_text"),
            )
            return {
                "is_results_page": True,
                "clicked": True,
                "click_selector": "(assessor results row link)",
                "link_text": row_open.get("link_text") or query_value,
                "description": row_open.get("description")
                or f"Opened property record for {query_value}",
                "reasoning": "Deterministic results-table click on owner/address column",
            }

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
                    "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
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

    if ai_indicates_already_on_detail(ai_data):
        logger.info(
            "LLM reports property detail page already open for %s on %s.",
            query_value,
            curr_url,
        )
        return {
            "is_results_page": False,
            "is_already_detail": True,
            "clicked": False,
            "reasoning": ai_data.get("reasoning") or ai_data.get("description"),
        }

    if ai_indicates_no_matching_result(ai_data):
        logger.info(
            "LLM reports no matching property for query %s on %s; skipping result clicks.",
            query_value,
            curr_url,
        )
        return {
            "is_results_page": True,
            "clicked": False,
            "no_matching_results": True,
            "reasoning": ai_data.get("reasoning") or ai_data.get("description"),
        }

    # Heuristic fallback if LLM returned false or failed
    if not ai_data.get("is_results_page") and not ai_indicates_already_on_detail(ai_data):
        tokens = _query_match_tokens(query_value, query_type)
        best_row: dict[str, Any] | None = None
        best_row_score = -1.0
        for c in all_candidates:
            c_text = (c.get("text") or "").strip()
            if re.match(r"^(qv|map|quick view)$", c_text, re.I):
                continue
            c_href = (c.get("href") or "").lower()
            c_db = (c.get("data_bind") or "").lower()
            c_sel = (c.get("selector") or "").lower()
            c_digits = re.sub(r"\D", "", c_text)

            if c.get("is_row"):
                rs = _score_row_against_query(c.get("row_context") or c_text, tokens)
                if rs > best_row_score:
                    best_row_score = rs
                    best_row = c
                continue

            row_blob = (c.get("row_context") or c_text or "").lower()
            matches_parcel = (clean_digits and clean_digits in c_digits) or (c_digits and c_digits in clean_digits)
            matches_address = _score_row_against_query(c_text, tokens) >= 0.5
            matches_address_ctx = _score_row_against_query(row_blob, tokens) >= 0.5
            is_tax_link = bool(c.get("is_tax_result_link"))
            is_detail_href = any(k in c_href for k in ["propertydetails", "detail", "recorddetail", "parceldetail", "folioid", "account"])
            is_clickable_cell = any(k in c_db for k in ["goto", "gotopin", "gotorecord", "detail"]) or ("link" in c_sel and matches_parcel) or (".pointer" in c_sel)

            if (
                matches_parcel
                or matches_address
                or matches_address_ctx
                or is_detail_href
                or (is_tax_link and (matches_address_ctx or matches_parcel or len(tokens) <= 2))
                or (is_clickable_cell and (matches_parcel or not clean_digits))
            ):
                ai_data = {
                    "is_results_page": True,
                    "click_selector": c.get("selector"),
                    "link_text": c_text,
                    "description": f"Click result {c_text or query_value} to view property details",
                    "reasoning": "Heuristic match on parcel/address/detail link in results table",
                }
                break

        min_row = 0.5 if await beacon_bulk_results_page(page) and query_type == "address" else 0.34
        if best_row and best_row_score >= min_row and not ai_data.get("is_results_page"):
            ai_data = {
                "is_results_page": True,
                "click_selector": best_row.get("selector"),
                "link_text": (best_row.get("text") or "")[:80],
                "description": "Click matching results table row to open property appraisal",
                "reasoning": f"Heuristic row match score={best_row_score:.2f}",
            }

    from app.browser.property_capture_prep import click_parcel_in_results_table_by_digits

    if await click_parcel_in_results_table_by_digits(page, query_value):
        await page.wait_for_timeout(2000)
        try:
            from app.browser.property_capture_prep import activate_parcel_details_tab

            await activate_parcel_details_tab(page)
        except Exception:
            pass
        return {
            "is_results_page": True,
            "clicked": True,
            "click_selector": "(digit-matched results row)",
            "link_text": query_value,
            "description": f"Opened parcel {query_value} from search results row",
            "reasoning": "Matched parcel digits in results table",
        }

    if not ai_data.get("is_results_page") or not ai_data.get("click_selector"):
        return {"is_results_page": False, "clicked": False}

    click_selector = _sanitize_playwright_selector(ai_data["click_selector"])
    link_text = ai_data.get("link_text") or query_value
    desc = ai_data.get("description") or f"Click parcel number {link_text} to view property details"

    if is_whole_results_row_selector(click_selector) or re.search(r"\bqv\b", link_text or "", re.I):
        logger.info(
            "AI chose whole-row or QV-adjacent target (%s); using owner/address column click instead.",
            click_selector,
        )
        row_open = await click_assessor_search_results_row(
            page, query_value, query_type=query_type
        )
        if row_open.get("clicked"):
            return {
                "is_results_page": True,
                "clicked": True,
                "click_selector": "(assessor results row link)",
                "link_text": row_open.get("link_text") or query_value,
                "description": row_open.get("description") or desc,
                "reasoning": "Replaced whole-row/QV AI selector with owner/address column link",
            }

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
                target = loc
                try:
                    inner_a = loc.locator("a").first
                    if await inner_a.count() > 0 and await inner_a.is_visible(timeout=400):
                        target = inner_a
                except Exception:
                    pass
                try:
                    await target.click(force=True, timeout=4000)
                    clicked = True
                    break
                except Exception:
                    try:
                        await target.evaluate(
                            """(el) => {
                                const a = el.querySelector && el.querySelector('a');
                                if (a) a.click();
                                else el.click();
                            }"""
                        )
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
        from app.browser.property_capture_prep import parcel_search_text_variants

        text_variants = list(
            dict.fromkeys(
                parcel_search_text_variants(query_value)
                + parcel_search_text_variants(link_text)
                + [clean_digits, link_text]
            )
        )
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
        row_fb = await click_matching_result_table_row(page, query_value, query_type=query_type)
        if row_fb.get("clicked"):
            return row_fb

    if clicked and await page_is_search_results_listing(page):
        await recover_from_quick_view_modal(page)
        row_open = await click_assessor_search_results_row(
            page, query_value, query_type=query_type
        )
        if row_open.get("clicked"):
            clicked = True
        elif await recover_from_quick_view_modal(page):
            clicked = True

    await page.wait_for_timeout(3500 if clicked else 0)

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

