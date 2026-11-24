"""Prepare GIS/assessor pages for OCR and PDF capture; sanitize scraped map UI noise."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import Page

from app.core.logging import get_logger

logger = get_logger(__name__)

GIS_UI_NOISE = re.compile(
    r"show\s+scalebar|auto[\s-]*hide\s+print|zoom\s+in/out|gps\s+location|auto-track|"
    r"print\s+panel|menu\s+icons|informational\s+purposes",
    re.I,
)

DISCLAIMER_HINT = re.compile(r"informational\s+purposes|terms\s+and\s+conditions|i\s+agree", re.I)

CAPTURE_TAB_LABELS = (
    "Parcel Details",
    "Property Details",
    "Appraisal",
    "Summary",
)

DETAIL_PANEL_SELECTORS = (
    "iframe[src*='PageTypeID=4' i]",
    "iframe[src*='Parcel' i]",
    "iframe[id*='detail' i]",
    "iframe[name*='detail' i]",
    "#ParcelDetails",
    "#divParcelDetails",
    "[id*='ParcelDetail' i]",
    "div[id*='DetailPane' i]",
    "#ResultsGrid",
    "table:has-text('Owner')",
    "table:has-text('Parcel')",
)

PROPERTY_DATA_HINT = re.compile(
    r"owner|situs|site\s*location|parcel\s*id|folio|just\s*value|assessed|land\s*area|legal\s*description",
    re.I,
)

SEARCH_SELECTION_FORM = re.compile(
    r"using only one\s*\(?\s*1\s*\)?\s*search option|activate your search|"
    r"then click on search|search selection|hide filter options|"
    r"click on search to activate|along with the roll type",
    re.I,
)


def body_looks_like_search_selection_form(text: str) -> bool:
    """Multi-option county/tax search forms — not post-search property detail."""
    return bool(SEARCH_SELECTION_FORM.search(text or ""))

PORTAL_SEARCH_ERROR = re.compile(
    r"input\s*error|correct your search criteria|select\s*\[new search\]|no\s+records?\s+found|"
    r"please\s+correct|invalid\s+search",
    re.I,
)


async def page_has_portal_search_error(page: Page) -> bool:
    parts = [await _scope_body_text(page)]
    for frame in page.frames:
        parts.append(await _scope_body_text(frame))
    blob = " ".join(parts)
    return bool(PORTAL_SEARCH_ERROR.search(blob))


def pdf_bytes_looks_valid(data: bytes, query_value: str = "") -> bool:
    """Reject portal error screens saved as PDF."""
    if not data or len(data) < 2500:
        return False
    low_sample = data[:80000].lower()
    if b"input error" in low_sample or b"correct your search" in low_sample:
        return False
    try:
        import io

        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        text = " ".join((p.extract_text() or "") for p in reader.pages[:4])
        if PORTAL_SEARCH_ERROR.search(text):
            return False
        if panel_has_property_data(text):
            return True
        if query_value:
            qd = re.sub(r"\D", "", query_value)
            if qd and qd in re.sub(r"\D", "", text):
                return True
        return False
    except Exception:
        return b"Owner" in data and b"Parcel" in data

PRINT_ROUTE_LINK_SELECTORS = (
    "a[href*='/print' i]",
    "a:has-text('Download/Print Report')",
    "a:has-text('Print Report')",
)

# File/download links — never click /print routes (native print dialog).
PORTAL_FILE_DOWNLOAD_SELECTORS = (
    "a:has-text('Print (PDF)')",
    "a:has-text('Print PDF')",
    "a[href$='.pdf' i]",
    "a[href*='.pdf?' i]",
    "a[download]",
    "a[href*='Report' i]:not([href*='/print' i])",
)

PORTAL_DOWNLOAD_SELECTORS = PORTAL_FILE_DOWNLOAD_SELECTORS + PRINT_ROUTE_LINK_SELECTORS

_SUPPRESS_NATIVE_PRINT_INIT = """() => {
  const blockPrint = function() {};
  const lockPrint = () => {
    try { window.print = blockPrint; } catch (_) {}
    try {
      Object.defineProperty(window, 'print', {
        value: blockPrint,
        writable: true,
        configurable: true,
      });
    } catch (_) {}
  };
  lockPrint();
  try { document.addEventListener('DOMContentLoaded', lockPrint, { once: true }); } catch (_) {}
  try { window.addEventListener('load', lockPrint, { once: true }); } catch (_) {}
  try { setInterval(lockPrint, 250); } catch (_) {}
}"""


_PRINT_VIEW_PATH_RE = re.compile(
    r"(?:^|/)(?:print(?:/|$)|print(?:building|property|parcel|card|details|report))",
    re.I,
)


def is_print_route_url(url: str) -> bool:
    """True when URL is a printer-friendly view that normally calls window.print()."""
    u = (url or "").strip()
    if not u:
        return False
    low = u.lower()
    if "print=1" in low or "mode=print" in low:
        return True
    path = urlparse(u).path.lower()
    if path.rstrip("/").endswith("/print") or "/print/" in path:
        return True
    return bool(_PRINT_VIEW_PATH_RE.search(path))


def _href_looks_like_print_route(href: str) -> bool:
    h = (href or "").strip().lower()
    if not h or h.startswith("javascript:"):
        return False
    return is_print_route_url(h) or h.endswith("/print") or "/print/" in h


def detail_url_from_print_route(url: str) -> str | None:
    """Turn a printer-friendly URL into the normal property detail URL."""
    if not url or not is_print_route_url(url):
        return None
    parsed = urlparse(url)
    path_low = (parsed.path or "").lower()
    qs = parse_qs(parsed.query)
    parcel_keys = ("parcelid", "parcelId", "ParcelId", "pcn", "folio", "strap")
    parcel_id = None
    for key in parcel_keys:
        if key in qs and qs[key]:
            parcel_id = qs[key][0]
            break
    if not parcel_id:
        m = re.search(
            r"(?:parcelid|parcelId|pcn|folio|strap)=([^&#]+)",
            url,
            re.I,
        )
        if m:
            parcel_id = m.group(1)

    if parcel_id and re.search(r"print(building|property|parcel|card|details)", path_low, re.I):
        q = urlencode({"parcelId": parcel_id})
        return f"{parsed.scheme}://{parsed.netloc}/Property/Details?{q}"

    base = url.split("?")[0].split("#")[0]
    if base.lower().endswith("/print"):
        return base[: -len("/print")]
    stripped = re.sub(r"/print/?$", "", base, flags=re.I)
    return stripped or None


def body_likely_property_detail(text: str, query_value: str = "") -> bool:
    if panel_has_property_data(text):
        return True
    if not text or len(text.strip()) < 80:
        return False
    if is_map_ui_noise(text) and not PROPERTY_DATA_HINT.search(text):
        return False
    qd = re.sub(r"\D", "", query_value or "")
    combined = re.sub(r"\D", "", text)
    if qd and len(qd) >= 8 and qd in combined and PROPERTY_DATA_HINT.search(text):
        return True
    return False


async def install_print_route_guards(context: Any) -> None:
    """Block top-level navigations to /print on the workflow tab; capture those URLs via Playwright PDF."""
    if getattr(context, "_print_route_guard_installed", False):
        return
    context._print_route_guard_installed = True
    context._allow_print_route_navigation = False
    context._blocked_print_route_url = None

    async def _route_guard(route: Any) -> None:
        req = route.request
        if req.resource_type not in ("document", "frame"):
            await route.continue_()
            return
        url = req.url or ""
        if getattr(context, "_allow_print_route_navigation", False):
            await route.continue_()
            return
        if is_print_route_url(url):
            context._blocked_print_route_url = url
            logger.info(
                "Blocked print-route navigation — will capture with Playwright PDF: %s",
                url[:160],
            )
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", _route_guard)


async def discover_parcel_detail_url(page: Page, query_value: str) -> str | None:
    """Resolve a property detail HTTP URL for the query (never the /print variant)."""
    from urllib.parse import urljoin

    ctx = page.context
    blocked = getattr(ctx, "_blocked_print_route_url", None)
    if blocked:
        detail = detail_url_from_print_route(blocked)
        if detail:
            return detail

    q = (query_value or "").strip()
    qd = re.sub(r"\D", "", q)
    variants = parcel_search_text_variants(q) if q else []
    seen: set[str] = set()

    for scope in [page, *page.frames]:
        for sel in (
            "a[href*='parcel' i]",
            "a[href*='Parcel' i]",
            "table a[href]",
            "a[href]",
        ):
            try:
                links = scope.locator(sel)
                count = min(await links.count(), 40)
                for i in range(count):
                    link = links.nth(i)
                    if not await link.is_visible(timeout=200):
                        continue
                    href = (await link.get_attribute("href") or "").strip()
                    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                        continue
                    full = urljoin(page.url or "", href)
                    if full in seen or is_print_route_url(full):
                        if is_print_route_url(full):
                            full = detail_url_from_print_route(full) or ""
                        if not full or full in seen:
                            continue
                    seen.add(full)
                    text = (await link.inner_text(timeout=500) or "").strip()
                    blob = f"{text} {href} {full}".upper()
                    blob_digits = re.sub(r"\D", "", blob)
                    token_hit = any(v.upper() in blob for v in variants if v)
                    digit_hit = bool(qd and len(qd) >= 8 and qd in blob_digits)
                    if token_hit or digit_hit:
                        if "/parcel" in full.lower() or "strap" in blob.lower() or digit_hit:
                            return full
            except Exception:
                continue
    return None


async def ensure_property_detail_workspace(page: Page, query_value: str = "") -> bool:
    """
    Land on an interactive property detail view (not search chrome or blocked /print).
    """
    await escape_print_preview_route(page)

    if await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=2500):
        return True

    detail_url = await discover_parcel_detail_url(page, query_value)
    if not detail_url:
        blocked = getattr(page.context, "_blocked_print_route_url", None)
        if blocked:
            detail_url = detail_url_from_print_route(blocked)

    if detail_url and not is_print_route_url(detail_url):
        try:
            logger.info("Opening property detail workspace: %s", detail_url[:160])
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=28000)
            await page.wait_for_timeout(900)
            page.context._blocked_print_route_url = None
        except Exception as nav_err:
            logger.debug("Property detail navigation: %s", nav_err)

    await dismiss_blocking_disclaimer(page)
    if is_standalone_parcel_detail_url(page.url or ""):
        return await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=12000)
    if not is_gis_portal_url(page.url or ""):
        await activate_parcel_details_tab(page, skip_print_routes=True)
    await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=10000)
    return await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=2000)


async def discover_print_route_url(page: Page) -> str | None:
    """Find a printer-friendly URL from visible links without clicking."""
    from urllib.parse import urljoin

    if is_standalone_parcel_detail_url(page.url or ""):
        return None

    blocked = getattr(page.context, "_blocked_print_route_url", None)
    if blocked and is_print_route_url(blocked):
        return blocked

    base = page.url or ""
    for scope in [page, *page.frames]:
        for sel in PRINT_ROUTE_LINK_SELECTORS:
            try:
                loc = scope.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=350):
                    continue
                href = (await loc.get_attribute("href") or "").strip()
                if _href_looks_like_print_route(href):
                    return urljoin(base, href)
            except Exception:
                continue
    return None


async def suppress_native_print_dialog(page: Page) -> None:
    """Block window.print() on the active tab (avoids Chrome Save-as-PDF modal)."""
    try:
        await page.evaluate(_SUPPRESS_NATIVE_PRINT_INIT)
    except Exception:
        pass


async def recover_from_print_route_page(page: Page, query_value: str = "") -> bool:
    """Return to interactive parcel detail when stuck on a print-layout URL."""
    if not is_print_route_url(page.url or ""):
        return True

    logger.warning("Recovering from print-layout URL: %s", (page.url or "")[:160])
    await suppress_native_print_dialog(page)
    for _ in range(2):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            pass

    detail = detail_url_from_print_route(page.url or "")
    if detail:
        try:
            await page.goto(detail, wait_until="domcontentloaded", timeout=28000)
            await page.wait_for_timeout(700)
            await suppress_native_print_dialog(page)
            if not is_print_route_url(page.url or ""):
                if query_value:
                    await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=8000)
                return True
        except Exception as nav_exc:
            logger.debug("Print-route recovery goto: %s", nav_exc)

    if await escape_print_preview_route(page):
        return True
    return not is_print_route_url(page.url or "")


async def escape_print_preview_route(page: Page) -> bool:
    """Leave a stuck /print URL without invoking the native print dialog."""
    if not is_print_route_url(page.url or ""):
        return False
    logger.warning("Leaving print-preview route: %s", (page.url or "")[:160])
    await suppress_native_print_dialog(page)
    for _ in range(2):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
        except Exception:
            pass
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(400)
        return not is_print_route_url(page.url or "")
    except Exception as exc:
        logger.debug("go_back from print route: %s", exc)
    return False


async def capture_print_route_as_pdf(
    page: Page,
    print_url: str,
    dest_pdf: Any,
    *,
    query_value: str = "",
) -> bool:
    """Open a print-layout URL in a side tab and save with Playwright PDF (no native print UI)."""
    from pathlib import Path
    from urllib.parse import urljoin

    pdf_path = Path(dest_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    target = urljoin(page.url or print_url, print_url)
    if not target.startswith("http"):
        return False

    ctx = page.context
    ctx._allow_print_route_navigation = True
    print_page = await ctx.new_page()
    try:
        await print_page.add_init_script(_SUPPRESS_NATIVE_PRINT_INIT)
        await print_page.goto(target, wait_until="domcontentloaded", timeout=22000)
        await print_page.wait_for_timeout(400)
        if query_value:
            await wait_for_property_detail_ready(print_page, query_value=query_value, timeout_ms=5000)
        await print_page.emulate_media(media="print")
        await print_page.pdf(
            path=str(pdf_path),
            format="Letter",
            print_background=True,
            prefer_css_page_size=True,
            margin=PDF_RENDER_MARGIN,
        )
        if pdf_path.exists() and pdf_path.stat().st_size > 1500:
            raw = pdf_path.read_bytes()
            if pdf_bytes_looks_valid(raw, query_value):
                logger.info("Saved print-route assessor PDF (%d bytes)", len(raw))
                return True
            pdf_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Print-route PDF capture failed (%s): %s", target[:100], exc)
    finally:
        ctx._allow_print_route_navigation = False
        try:
            await print_page.close()
        except Exception:
            pass
    return False


async def capture_assessor_pdf_best_effort(
    page: Page,
    dest_pdf: Any,
    *,
    query_value: str = "",
) -> bool:
    """
    Save assessor card via Playwright page.pdf() on GIS embeds only.
    Standalone county portals (pbcpao, etc.) use screenshot capture — never print-layout URLs.
    """
    await recover_from_print_route_page(page, query_value)
    await ensure_property_detail_workspace(page, query_value)

    if is_standalone_parcel_detail_url(page.url or ""):
        return False

    if is_print_route_url(page.url or ""):
        await recover_from_print_route_page(page, query_value)
        return False

    if is_gis_portal_url(page.url or ""):
        if await render_assessor_detail_pdf_playwright(page, dest_pdf, query_value=query_value):
            return True

    return await try_portal_assessor_file_download(page, dest_pdf, query_value=query_value)

PDF_RENDER_MARGIN = {"top": "0.35in", "right": "0.35in", "bottom": "0.35in", "left": "0.35in"}


def panel_has_property_data(text: str) -> bool:
    if not text or len(text.strip()) < 60:
        return False
    if is_map_ui_noise(text) and not PROPERTY_DATA_HINT.search(text):
        return False
    return bool(PROPERTY_DATA_HINT.search(text))


async def _scope_body_text(scope: Page | Any) -> str:
    try:
        return (await scope.locator("body").inner_text(timeout=2500) or "")[:4000]
    except Exception:
        return ""


async def wait_for_property_detail_ready(page: Page, query_value: str = "", timeout_ms: int = 22000) -> bool:
    """Wait until an iframe or panel shows real parcel fields (not an empty tab shell)."""
    import time

    if await page_is_search_results_listing(page):
        return False

    digits = re.sub(r"\D", "", query_value or "")
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        for frame in page.frames:
            text = await _scope_body_text(frame)
            if panel_has_property_data(text):
                if not digits or digits in re.sub(r"\D", "", text):
                    return True
                if PROPERTY_DATA_HINT.search(text):
                    return True
        for sel in DETAIL_PANEL_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=400):
                    continue
                inner = (await loc.inner_text(timeout=1500) or "")[:3000]
                if panel_has_property_data(inner):
                    return True
            except Exception:
                continue
        await page.wait_for_timeout(600)
    return False


RESULT_ROW_NOISE = re.compile(
    r"certified\s+values|property\s+appraiser\s+20\d\d|download\s*/\s*print\s+report|"
    r"record\s+search|search\s+results|jump\s+to:\s*page",
    re.I,
)

SEARCH_RESULTS_LISTING = re.compile(
    r"property\s+search\s+results|basic\s+search\s+results|click\s+rows\s+to\s+view|"
    r"showing\s+\d+\s+(through|to)\s+\d+|total\s+records",
    re.I,
)

ASSESSOR_RESULTS_ROW_CLICK_JS = r"""(payload) => {
    const query = (payload && payload.query) ? String(payload.query) : String(payload || '');
    const queryType = ((payload && payload.query_type) || 'address').toLowerCase();
    const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const raw = String(query || '').trim();
    const parts = raw.split(/[\s,]+/).filter(Boolean);
    const tokens = [];
    for (const p of parts) {
        const t = p.replace(/\./g, '').toLowerCase();
        if (t.length < 2) continue;
        if (/^(st|dr|rd|ave|blvd|ln|ct|way|ter|cir|pl)$/i.test(t)) continue;
        tokens.push(t);
    }
    if (!tokens.length && raw) tokens.push(norm(raw));
    const isActionLabel = (t) => /^(qv|map|print|excel|csv|y|n)$/i.test(String(t || '').trim());
    const isActionControl = (el) => {
        if (!el) return true;
        const tag = (el.tagName || '').toUpperCase();
        const val = (el.value || el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        if (isActionLabel(val)) return true;
        if (tag === 'INPUT' || tag === 'BUTTON') {
            if (isActionLabel(val) || (el.type || '').toLowerCase() === 'button') return true;
        }
        const cls = String(el.className || '').toLowerCase();
        if (/\bqv\b|\bmap\b|quickview|quick-view/.test(cls)) return true;
        return false;
    };
    const headerCols = (table) => {
        const idx = { owner: -1, address: -1, parcel: -1, all: [] };
        const headerCells = table.querySelectorAll('thead th, thead td, tr th');
        const rows = headerCells.length ? headerCells : table.querySelectorAll('tbody tr:first-child th, tbody tr:first-child td');
        let i = 0;
        rows.forEach((cell) => {
            const h = norm(cell.innerText || cell.textContent || '');
            if (!h || h.length > 80) return;
            idx.all.push(h);
            if (idx.owner < 0 && /owner/.test(h)) idx.owner = i;
            if (idx.address < 0 && /(site\\s*)?address|location|situs/.test(h)) idx.address = i;
            if (idx.parcel < 0 && /(parcel|folio|pcn|control\\s*number|strap|pin)/.test(h)) idx.parcel = i;
            i += 1;
        });
        if (idx.address < 0) idx.address = idx.owner >= 0 ? idx.owner + 1 : 2;
        if (idx.owner < 0) idx.owner = 1;
        return idx;
    };
    const pickClickableInCell = (td) => {
        if (!td || isActionControl(td)) return null;
        const candidates = [td, ...td.querySelectorAll('a[href], [onclick], span, div')];
        for (const el of candidates) {
            if (isActionControl(el)) continue;
            const txt = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!txt || txt.length < 4 || isActionLabel(txt)) continue;
            const tag = (el.tagName || '').toUpperCase();
            let style = { cursor: 'auto', textDecoration: '' };
            try { style = window.getComputedStyle(el); } catch (_) {}
            const href = el.getAttribute && el.getAttribute('href');
            const oc = el.getAttribute && el.getAttribute('onclick');
            if (tag === 'A' && href && href !== '#') {
                const hl = href.toLowerCase();
                if (!hl.startsWith('javascript:void')) return { el, txt };
            }
            if (oc) return { el, txt };
            if (tag === 'A' || style.cursor === 'pointer' || /underline/.test(style.textDecoration || '')) {
                return { el, txt };
            }
            if (tag === 'SPAN' && txt.length >= 6 && /[A-Z0-9]/.test(txt)) return { el, txt };
        }
        return null;
    };
    const tryClick = (el, txt) => {
        try {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
            el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
            el.click();
            return { clicked: true, link_text: txt.slice(0, 100), method: 'cell-link' };
        } catch (_) {
            return null;
        }
    };
    for (const table of document.querySelectorAll('table')) {
        const cols = headerCols(table);
        for (const tr of table.querySelectorAll('tbody tr')) {
            if (tr.querySelector('th')) continue;
            const tds = tr.querySelectorAll('td');
            if (tds.length < 3) continue;
            const rowText = norm(tr.innerText || '');
            if (!rowText || rowText.length < 10) continue;
            let match = true;
            for (const tok of tokens) {
                if (!rowText.includes(tok)) { match = false; break; }
            }
            if (!match) continue;
            const prefCols = queryType === 'parcel'
                ? [cols.parcel, cols.address, cols.owner]
                : queryType === 'owner'
                    ? [cols.owner, cols.address, cols.parcel]
                    : [cols.address, cols.owner, cols.parcel];
            const seenIdx = new Set();
            for (const ci of prefCols) {
                if (ci < 0 || ci >= tds.length || seenIdx.has(ci)) continue;
                seenIdx.add(ci);
                const hit = pickClickableInCell(tds[ci]);
                if (hit) {
                    const res = tryClick(hit.el, hit.txt);
                    if (res) return res;
                }
                const cellTxt = (tds[ci].innerText || '').replace(/\s+/g, ' ').trim();
                if (cellTxt.length >= 6 && !isActionLabel(cellTxt)) {
                    const res = tryClick(tds[ci], cellTxt);
                    if (res) return res;
                }
            }
            for (let ci = 0; ci < tds.length; ci++) {
                if (seenIdx.has(ci)) continue;
                const cellTxt = norm(tds[ci].innerText || '');
                if (isActionLabel(cellTxt) || /^\\s*$/i.test(cellTxt)) continue;
                const hit = pickClickableInCell(tds[ci]);
                if (hit) {
                    const res = tryClick(hit.el, hit.txt);
                    if (res) return res;
                }
            }
        }
    }
    return { clicked: false };
}"""

DISMISS_QV_CLICK_DETAILS_JS = r"""() => {
    const body = (document.body && document.body.innerText) ? document.body.innerText.toLowerCase() : '';
    const modalish = body.includes('print property summary') ||
        (body.includes('market value') && body.includes('owners') && body.includes('close'));
    if (!modalish) return { dismissed: false };
    const controls = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
    for (const el of controls) {
        const t = (el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
        if (/^details$/i.test(t)) {
            try {
                el.click();
                return { dismissed: true, action: 'details' };
            } catch (_) {}
        }
    }
    return { dismissed: false };
}"""


async def page_is_search_results_listing(page: Page) -> bool:
    """True when the page is a multi-row search results grid, not a single-parcel detail view."""
    url = (page.url or "").lower()
    if any(k in url for k in ("searchresults", "mastersearch", "pagetypeid=3")):
        return True
    try:
        body = (await page.locator("body").inner_text(timeout=3000) or "")[:6000]
    except Exception:
        return False
    low = body.lower()
    if "property search results" in low:
        return True
    if SEARCH_RESULTS_LISTING.search(body) and re.search(r"\bqv\b", low, re.I):
        return True
    if SEARCH_RESULTS_LISTING.search(body) and re.search(
        r"owner\s+name|site\s+address|parcel\s+control", low, re.I
    ):
        return True
    return False


async def click_assessor_search_results_row(
    page: Page,
    query_value: str,
    *,
    query_type: str = "address",
) -> dict[str, Any]:
    """Open full property details from an assessor results table (owner/address link, not QV/MAP)."""
    if not (query_value or "").strip():
        return {"clicked": False}
    payload = {"query": query_value.strip(), "query_type": (query_type or "address").lower()}
    for scope in [page, *page.frames]:
        try:
            res = await scope.evaluate(ASSESSOR_RESULTS_ROW_CLICK_JS, payload)
            if res and res.get("clicked"):
                try:
                    await page.wait_for_function(
                        """() => {
                            const u = (location.href || '').toLowerCase();
                            return u.includes('property/details') || u.includes('parcelid=')
                                || u.includes('/parcel/');
                        }""",
                        timeout=15000,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(2000)
                if await page_is_search_results_listing(page):
                    try:
                        qv = await scope.evaluate(DISMISS_QV_CLICK_DETAILS_JS)
                        if qv and qv.get("action") == "details":
                            await page.wait_for_timeout(2500)
                    except Exception:
                        pass
                return {
                    "clicked": True,
                    "link_text": res.get("link_text") or query_value,
                    "description": f"Opened property record from results row ({res.get('link_text') or query_value})",
                }
        except Exception:
            continue
    return {"clicked": False}


def is_whole_results_row_selector(selector: str) -> bool:
    """True when the LLM picked a full `<tr>` click (often hits QV/MAP instead of owner/address)."""
    s = (selector or "").lower().strip()
    if not s:
        return False
    if "qv" in s or "map" in s:
        return True
    if "tbody tr" in s and " a" not in s and ":has-text" not in s and " td" not in s:
        return True
    return False


async def recover_from_quick_view_modal(page: Page) -> bool:
    """If a QV popup is open, click its Details control to reach the full property page."""
    for scope in [page, *page.frames]:
        try:
            qv = await scope.evaluate(DISMISS_QV_CLICK_DETAILS_JS)
            if qv and qv.get("action") == "details":
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False


def parcel_search_text_variants(query_value: str) -> list[str]:
    """Hyphen/space/digit variants for GIS grid parcel cells (no county hardcoding)."""
    q = re.sub(r"\s+", " ", (query_value or "").strip())
    if not q:
        return []
    variants = [q, q.replace("-", " "), q.replace("-", "")]
    m = re.match(r"^(\d{2}-\d{2}-\d{2})-(.+)$", q, re.I)
    if m:
        variants.append(f"{m.group(1)} {m.group(2)}")
    digits = re.sub(r"\D", "", q)
    if digits:
        variants.append(digits)
    return list(dict.fromkeys(v for v in variants if v and len(v) >= 5))


PARCEL_RESULTS_ROW_CLICK_JS = """(queryDigits) => {
    const norm = (s) => String(s || '').replace(/\\D/g, '');
    const qd = norm(queryDigits);
    if (!qd || qd.length < 6) return false;
    for (const tr of document.querySelectorAll('table tbody tr')) {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 2) continue;
        const headerish = /^(parcel id|owner|address|#)$/i.test((tds[0].innerText || '').trim());
        if (headerish && tr.parentElement && tr.parentElement.querySelectorAll('tr').length > 2) continue;
        const rd = norm(tr.innerText || '');
        if (!rd.includes(qd)) continue;
        const link = tr.querySelector('td a[href], td a');
        const target = link || tds[0] || tr;
        try {
            target.scrollIntoView({ block: 'center', inline: 'nearest' });
            if (link) {
                link.click();
            } else {
                tr.click();
                tr.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
            }
            return true;
        } catch (_) {}
    }
    return false;
}"""


async def click_parcel_in_results_table_by_digits(page: Page, query_value: str) -> bool:
    """Click the results row whose parcel digits match the query (Lafayette / GrizzlyLogic-style grids)."""
    digits = re.sub(r"\D", "", (query_value or "").strip())
    if len(digits) < 6:
        return False
    for scope in [page, *page.frames]:
        try:
            ok = await scope.evaluate(PARCEL_RESULTS_ROW_CLICK_JS, digits)
            if ok:
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False


async def activate_search_results_tab(page: Page) -> None:
    for scope in [page, *page.frames]:
        try:
            tab = scope.locator(
                "a:has-text('Search Results'), button:has-text('Search Results'), "
                "[role='tab']:has-text('Search Results')"
            ).first
            if await tab.count() > 0 and await tab.is_visible(timeout=800):
                await tab.click(force=True, timeout=2500)
                await page.wait_for_timeout(900)
                return
        except Exception:
            continue


async def open_gis_search_result_row(
    page: Page,
    query_value: str,
    *,
    query_type: str = "parcel",
    workflow_run_id: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """
    Open a parcel from the GIS Search Results grid (DeSoto / floridapa).
    Uses deterministic parcel link click, then AI if the portal layout blocks automation.
    """
    key = (query_value or "").strip()
    if not key or not is_gis_portal_url(page.url or ""):
        return {"clicked": False, "reason": "not_gis_or_empty_query"}

    await activate_search_results_tab(page)

    parcel_digits = re.sub(r"\D", "", key)
    clicked = False
    description = ""

    if await click_parcel_in_results_table_by_digits(page, key):
        clicked = True
        description = f"Opened parcel {key} from results table (digit match)"
        await activate_parcel_details_tab(page)

    parcel_selectors: list[str] = []
    for variant in parcel_search_text_variants(key):
        parcel_selectors.extend(
            [
                f'a:has-text("{variant}")',
                f'td a:has-text("{variant}")',
                f'td.link:has-text("{variant}")',
                f'tr:has-text("{variant}") a',
                f'tr:has-text("{variant}")',
            ]
        )
    parcel_selectors = list(dict.fromkeys(parcel_selectors))
    parcel_link_re = re.compile(re.escape(key[:18]), re.I) if len(key) >= 10 else None
    if parcel_digits and parcel_digits != key:
        parcel_selectors.extend(
            [
                f'a:has-text("{parcel_digits}")',
                f'tr:has-text("{parcel_digits}") a',
            ]
        )

    for scope in [page, *page.frames]:
        for sel in parcel_selectors:
            try:
                loc = scope.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=1200):
                    continue
                text = (await loc.inner_text(timeout=1500) or "").strip()
                if RESULT_ROW_NOISE.search(text) and key not in text:
                    continue
                text_digits = re.sub(r"\D", "", text)
                if key not in text and (not parcel_digits or parcel_digits not in text_digits):
                    continue
                await loc.click(force=True, timeout=3500)
                clicked = True
                description = f"Opened parcel {key} from search results"
                await page.wait_for_timeout(2000)
                break
            except Exception:
                continue
        if clicked:
            break

    if not clicked and key:
        jq_sel = (
            f"table.ui-jqgrid-btable tr.jqgrow:has-text('{key}'), "
            f"#ResultsGrid tr.jqgrow:has-text('{key}'), "
            f"table[id*='Grid' i] tr.jqgrow:has-text('{key}')"
        )
        for scope in [page, *page.frames]:
            try:
                row = scope.locator(jq_sel).first
                if await row.count() == 0 or not await row.is_visible(timeout=1500):
                    continue
                cell_link = row.locator("td a").first
                if await cell_link.count() > 0 and await cell_link.is_visible(timeout=800):
                    await cell_link.click(force=True, timeout=3500)
                else:
                    await row.dblclick(force=True, timeout=3500)
                clicked = True
                description = f"Opened parcel {key} from jqGrid search results"
                await page.wait_for_timeout(2500)
                break
            except Exception:
                continue

    if not clicked and parcel_link_re:
        for scope in [page, *page.frames]:
            try:
                loc = scope.locator("a").filter(has_text=parcel_link_re).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=1200):
                    continue
                text = (await loc.inner_text(timeout=1500) or "").strip()
                if RESULT_ROW_NOISE.search(text) and key not in text:
                    continue
                await loc.click(force=True, timeout=3500)
                clicked = True
                description = f"Opened parcel {key} from search results (partial link)"
                await page.wait_for_timeout(2000)
                break
            except Exception:
                continue

    if not clicked:
        try:
            from app.browser.result_selector_ai import detect_and_click_result_link_ai

            ai_res = await detect_and_click_result_link_ai(
                page,
                query_value=key,
                query_type=query_type,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            if ai_res.get("clicked"):
                clicked = True
                description = ai_res.get("description") or f"AI opened parcel {key}"
                await page.wait_for_timeout(2000)
        except Exception as exc:
            logger.debug("GIS result AI click note: %s", exc)

    if clicked:
        await activate_parcel_details_tab(page)
        await wait_for_property_detail_ready(page, query_value=key, timeout_ms=10000)

    if not await wait_for_property_detail_ready(page, query_value=key, timeout_ms=2000):
        ensured = await ensure_gis_parcel_detail(page, key)
        if ensured:
            clicked = True
            description = description or f"Opened parcel {key} via ensure_gis_parcel_detail"
            await activate_parcel_details_tab(page)

    return {
        "clicked": clicked,
        "description": description,
        "detail_ready": await wait_for_property_detail_ready(page, query_value=key, timeout_ms=2500)
        if clicked
        else False,
    }


async def _interaction_navigates_to_print(link: Any) -> bool:
    try:
        href = (await link.get_attribute("href") or "").strip()
        if _href_looks_like_print_route(href):
            return True
        onclick = (await link.get_attribute("onclick") or "").lower()
        if "print" in onclick and "/parcel" in onclick:
            return True
        text = (await link.inner_text(timeout=600) or "").strip().lower()
        if re.search(r"\bprint\b", text):
            if "download" in text and "pdf" in text:
                return False
            return True
    except Exception:
        pass
    return False


async def activate_parcel_details_tab(page: Page, *, skip_print_routes: bool = True) -> None:
    for label in CAPTURE_TAB_LABELS:
        for scope in [page, *page.frames]:
            try:
                tab = scope.locator(
                    f"a:has-text('{label}'), button:has-text('{label}'), "
                    f"[role='tab']:has-text('{label}'), li:has-text('{label}') a"
                ).first
                if await tab.count() > 0 and await tab.is_visible(timeout=800):
                    if skip_print_routes and await _interaction_navigates_to_print(tab):
                        logger.debug("Skipping capture tab %s — targets print route", label)
                        continue
                    await tab.click(force=True, timeout=2500)
                    await page.wait_for_timeout(1600)
                    logger.info("Activated capture tab: %s", label)
                    if label in ("Parcel Details", "Property Details"):
                        return
            except Exception:
                continue


def is_gis_portal_url(url: str) -> bool:
    low = (url or "").lower()
    return "/gis" in low or "pagetypeid" in low or "keyvalue=" in low


def is_standalone_parcel_detail_url(url: str) -> bool:
    """True for direct property detail HTTP pages (not GIS embeds, search grids, or /print routes)."""
    if not url or is_print_route_url(url) or is_gis_portal_url(url):
        return False
    low = (url or "").lower()
    if re.search(r"/parcel/[^/?#]+", low):
        return True
    if "/property/details" in low or "propertydetail" in low:
        return True
    if any(k in low for k in ("parcelid=", "parctrl=", "folioid=", "accountid=")):
        return True
    if "pagetypeid=4" in low and "keyvalue=" in low:
        return True
    return False


EXPAND_DETAIL_SECTIONS_JS = r"""async () => {
    const skip = (t) => /^(close|print|map|search|qv|home|nearby)$/i.test(String(t || '').trim());
    const hint = /owner|sales|exemption|property|appraisal|assessed|taxable|taxes|information|value|legal|mailing/i;
    const clickables = document.querySelectorAll(
        'button[aria-expanded="false"], [role="button"][aria-expanded="false"], ' +
        '.accordion-toggle, .panel-heading a, a[data-toggle="collapse"], ' +
        '[data-bs-toggle="collapse"], .ui-accordion-header'
    );
    for (const el of clickables) {
        const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
        if (skip(t)) continue;
        if (hint.test(t) || el.getAttribute('aria-expanded') === 'false') {
            try { el.click(); } catch (_) {}
        }
    }
    document.querySelectorAll('tr, div, button, a').forEach((el) => {
        const raw = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (raw.length > 72 || !hint.test(raw)) return;
        if (skip(raw)) return;
        if (el.getAttribute('aria-expanded') === 'false' || /\+\s*$/.test(raw)) {
            try { el.click(); } catch (_) {}
        }
    });
    document.querySelectorAll('a, button, span, i, div').forEach((el) => {
        const t = (el.innerText || el.title || '').trim();
        if (t === '+' || t === '▶' || /^expand$/i.test(t) || /show\s+more/i.test(t)) {
            const row = el.closest('tr, div, li');
            if (row && hint.test((row.innerText || ''))) {
                try { row.click(); } catch (_) {}
            } else {
                try { el.click(); } catch (_) {}
            }
        }
    });
}"""

SCROLL_PAGE_FOR_LAZY_LOAD_JS = r"""async () => {
    await new Promise((resolve) => {
        let totalHeight = 0;
        const distance = 450;
        const timer = setInterval(() => {
            const scrollHeight = document.body.scrollHeight;
            window.scrollBy(0, distance);
            totalHeight += distance;
            if (totalHeight >= scrollHeight || totalHeight > 16000) {
                clearInterval(timer);
                window.scrollTo(0, 0);
                resolve();
            }
        }, 70);
    });
}"""


async def expand_collapsible_detail_sections(
    page: Page,
    *,
    query_value: str = "",
) -> None:
    try:
        from app.browser.detail_sections_ai import expand_property_detail_sections_ai

        await expand_property_detail_sections_ai(page, query_value=query_value)
    except Exception as ai_expand_err:
        logger.debug("AI detail section expansion note: %s", ai_expand_err)

    for scope in [page, *page.frames]:
        try:
            await scope.evaluate(EXPAND_DETAIL_SECTIONS_JS)
        except Exception:
            continue
    await page.wait_for_timeout(500)


async def scroll_property_detail_page(page: Page) -> None:
    for scope in [page, *page.frames]:
        try:
            await scope.evaluate(SCROLL_PAGE_FOR_LAZY_LOAD_JS)
            await page.wait_for_timeout(350)
            return
        except Exception:
            continue


_PARCEL_UI_NOISE = re.compile(
    r"grid|scalebar|layer|toolbar|search\s+results|parcel\s+details\s+tab|map\b",
    re.I,
)


def is_plausible_parcel_id(value: str, query_value: str = "") -> bool:
    """Reject map/grid labels scraped as parcel IDs."""
    if not value or not str(value).strip():
        return False
    s = str(value).strip()
    if _PARCEL_UI_NOISE.search(s):
        return False
    if len(s) > 48 or len(s.split()) > 6:
        return False
    digits = re.sub(r"\D", "", s)
    if not digits or len(digits) < 5:
        return False
    if query_value:
        qd = re.sub(r"\D", "", query_value)
        if qd and qd not in digits and digits not in qd:
            if len(qd) >= 8:
                return False
    return True


async def disclaimer_still_visible(page: Page) -> bool:
    try:
        body_text = (await page.locator("body").inner_text(timeout=4000))[:2500]
        if DISCLAIMER_HINT.search(body_text):
            return True
    except Exception:
        pass
    for scope in [page, *page.frames]:
        try:
            if await scope.locator("text=/informational purposes/i").count() > 0:
                agree = scope.locator(
                    'button:has-text("I agree"), button:has-text("Agree"), input[value*="agree" i], #button1'
                ).first
                if await agree.count() > 0 and await agree.is_visible(timeout=400):
                    return True
        except Exception:
            continue
    return False


async def dismiss_blocking_disclaimer(
    page: Page,
    *,
    workflow_run_id: str | None = None,
    node_id: str | None = None,
    attempts: int = 3,
) -> bool:
    """Dismiss disclaimer overlays; returns True when no blocking disclaimer remains."""
    from app.browser.modal_detector import detect_and_dismiss_modal_ai

    for _ in range(attempts):
        if not await disclaimer_still_visible(page):
            return True
        try:
            modal = await detect_and_dismiss_modal_ai(page, workflow_run_id=workflow_run_id, node_id=node_id)
            if modal.get("has_disclaimer") and modal.get("dismissed"):
                await page.wait_for_timeout(900)
        except Exception as exc:
            logger.debug("Modal dismiss attempt note: %s", exc)

        for scope in [page, *page.frames]:
            try:
                for btn_sel in (
                    'button:has-text("I agree, please continue")',
                    'button:has-text("I agree")',
                    'button:has-text("Agree")',
                    'input[value*="agree" i]',
                    'a:has-text("I agree")',
                    "#button1",
                ):
                    btn = scope.locator(btn_sel).first
                    if await btn.count() > 0 and await btn.is_visible(timeout=500):
                        await btn.click(force=True, timeout=2500)
                        await page.wait_for_timeout(1100)
                        break
            except Exception:
                continue

        if not await disclaimer_still_visible(page):
            return True

    return not await disclaimer_still_visible(page)


async def ensure_gis_parcel_detail(page: Page, query_value: str = "") -> bool:
    """On floridapa GIS hubs, open the parcel detail view before capture."""
    url = page.url or ""
    if not is_gis_portal_url(url) or not query_value:
        return False

    low = url.lower()
    key = (query_value or "").strip()
    digits = re.sub(r"\D", "", key)

    if await wait_for_property_detail_ready(page, query_value=key, timeout_ms=3000):
        return True

    opened = "keyvalue=" in low and digits and digits in re.sub(r"\D", "", low)
    if opened:
        await activate_parcel_details_tab(page)
        await wait_for_property_detail_ready(page, query_value=key, timeout_ms=16000)
        return True

    try:
        from app.extraction.portal_configs import normalize_florida_pa_parcel

        norm_key = normalize_florida_pa_parcel(key)
    except Exception:
        norm_key = key

    for scope in [page, *page.frames]:
        for sel in (
            f'a:has-text("{key}")',
            f'td:has-text("{key}")',
            f'tr:has-text("{norm_key}")',
            f'tr:has-text("{key}")',
            "#ResultsGrid tr.jqgrow",
            "table.ui-jqgrid-btable tr.jqgrow",
        ):
            try:
                loc = scope.locator(sel).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=1200):
                    continue
                text = (await loc.inner_text(timeout=1500) or "").strip()
                if _PARCEL_UI_NOISE.search(text) and digits not in re.sub(r"\D", "", text):
                    continue
                await loc.click(force=True, timeout=3000)
                await page.wait_for_timeout(2500)
                if "keyvalue=" in (page.url or "").lower() or await scope.locator(
                    "#ParcelDetails, [id*='ParcelDetail' i], #divParcelDetails"
                ).count():
                    logger.info("Opened GIS parcel row/detail via %s", sel)
                    return True
            except Exception:
                continue
    return False


def is_map_ui_noise(text: str) -> bool:
    if not text or not str(text).strip():
        return False
    return bool(GIS_UI_NOISE.search(str(text)))


def looks_like_physical_address(text: str) -> bool:
    if not text:
        return False
    s = str(text).strip()
    if is_map_ui_noise(s):
        return False
    if len(s) > 220:
        return False
    return bool(re.search(r"\d{1,6}\s+[\w]", s)) or bool(
        re.search(r"\b(rd|dr|st|ave|blvd|ln|ct|hwy|road)\b", s, re.I)
    )


def sanitize_currency_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return None
        return value
    s = re.sub(r"\s+", " ", str(value)).strip().replace("$", "")
    if not s:
        return None
    digits_only = re.sub(r"\D", "", s)
    if len(digits_only) > 10:
        first_currency = re.search(r"(\d{1,3},\d{3})(?:,\d{3})*", s)
        if first_currency:
            s = first_currency.group(1)
        else:
            s = digits_only[:9]
    try:
        return float(s.replace(",", ""))
    except ValueError:
        m = re.search(r"([\d,]+(?:\.\d{2})?)", s)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return value


def sanitize_extracted_record(record: dict[str, Any], *, query_value: str = "", query_type: str = "") -> dict[str, Any]:
    """Drop GIS toolbar text and fix corrupted currency fields."""
    if not record:
        return record
    out = dict(record)

    for addr_key in ("property_address", "mailing_address", "situs_address"):
        val = out.get(addr_key)
        if val and (is_map_ui_noise(str(val)) or not looks_like_physical_address(str(val))):
            if addr_key == "property_address" and looks_like_physical_address(str(out.get("mailing_address", ""))):
                pass
            elif is_map_ui_noise(str(val)):
                out[addr_key] = None

    for money_key in ("taxable_value", "assessed_value", "just_value"):
        if money_key in out and out[money_key] is not None:
            cleaned = sanitize_currency_value(out[money_key])
            out[money_key] = cleaned

    pn = out.get("parcel_number")
    if pn and not is_plausible_parcel_id(str(pn), query_value):
        out["parcel_number"] = None
    if not out.get("parcel_number") and query_type == "parcel" and query_value:
        out["parcel_number"] = query_value.strip()

    return out


_ASSESSOR_IDENTITY_KEYS = (
    "parcel_number",
    "owner_name",
    "property_address",
    "legal_description",
    "assessed_value",
    "just_value",
)


def _record_display_value(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    return bool(s) and s not in ("-", "—", "null", "None", "N/A", "n/a")


def record_has_assessor_identity(record: dict[str, Any] | None) -> bool:
    if not record or not isinstance(record, dict):
        return False
    return any(_record_display_value(record.get(k)) for k in _ASSESSOR_IDENTITY_KEYS)


def _digits_match(a: str, b: str) -> bool:
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    if not da or not db:
        return False
    return da == db or da in db or db in da


def finalize_assessor_extraction(
    extracted: dict[str, Any],
    *,
    query_value: str = "",
    query_type: str = "parcel",
) -> dict[str, Any]:
    """
    Collapse bogus search-grid table rows into one display record.
    Top-level OCR/DOM fields win over empty per-row mappings.
    """
    if not extracted or not isinstance(extracted, dict):
        return extracted or {}

    out = dict(extracted)
    meta_keys = {"_records", "_documents", "_pagination", "_source_url", "ocr_markdown", "ocr_key_values"}
    canonical = sanitize_extracted_record(
        {k: v for k, v in out.items() if k not in meta_keys and not str(k).startswith("_")},
        query_value=query_value,
        query_type=query_type,
    )

    raw_records = out.get("_records")
    if not isinstance(raw_records, list) or not raw_records:
        if record_has_assessor_identity(canonical):
            out["_records"] = [canonical]
        return out

    meaningful = [r for r in raw_records if isinstance(r, dict) and record_has_assessor_identity(r)]
    qv = (query_value or "").strip()

    if qv and query_type == "parcel":
        matched = [
            r
            for r in meaningful
            if _digits_match(str(r.get("parcel_number") or ""), qv)
            or _digits_match(qv, str(r.get("parcel_number") or ""))
        ]
        if matched:
            meaningful = matched

    mostly_empty_grid = len(meaningful) < max(1, len(raw_records) // 2)
    if mostly_empty_grid and record_has_assessor_identity(canonical):
        meaningful = []

    if not meaningful and record_has_assessor_identity(canonical):
        out["_records"] = [canonical]
        for k, v in canonical.items():
            if v is not None and (k not in out or not out.get(k)):
                out[k] = v
        return out

    if not meaningful:
        out.pop("_records", None)
        for k, v in canonical.items():
            if v is not None:
                out[k] = v
        return out

    if len(meaningful) == 1 or record_has_assessor_identity(canonical):
        base = dict(meaningful[0]) if meaningful else {}
        merged = dict(base)
        for k, v in canonical.items():
            if v is not None and (_record_display_value(v) or not _record_display_value(merged.get(k))):
                merged[k] = v
        merged = sanitize_extracted_record(merged, query_value=query_value, query_type=query_type)
        out["_records"] = [merged]
        for k, v in merged.items():
            if v is not None:
                out[k] = v
        return out

    out["_records"] = [sanitize_extracted_record(r, query_value=query_value, query_type=query_type) for r in meaningful]
    if record_has_assessor_identity(canonical):
        for k, v in canonical.items():
            if v is not None and not _record_display_value(out.get(k)):
                out[k] = v
    return out


async def prepare_property_detail_view(
    page: Page,
    *,
    workflow_run_id: str | None = None,
    node_id: str | None = None,
    query_value: str = "",
) -> None:
    """Dismiss blocking modals and open parcel detail content before screenshot/PDF."""
    await recover_from_print_route_page(page, query_value)
    await suppress_native_print_dialog(page)
    await dismiss_blocking_disclaimer(page, workflow_run_id=workflow_run_id, node_id=node_id)
    if is_gis_portal_url(page.url or ""):
        await ensure_gis_parcel_detail(page, query_value)
        await activate_parcel_details_tab(page, skip_print_routes=True)
    elif query_value:
        await ensure_property_detail_workspace(page, query_value)
    else:
        await escape_print_preview_route(page)
        await activate_parcel_details_tab(page, skip_print_routes=True)
    await dismiss_blocking_disclaimer(page, workflow_run_id=workflow_run_id, node_id=node_id)
    await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=6000)
    await expand_collapsible_detail_sections(page, query_value=query_value)

    for sel in DETAIL_PANEL_SELECTORS:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=3500)
            break
        except Exception:
            continue

    await page.wait_for_timeout(800)


async def _best_detail_frame_url(page: Page, query_value: str = "") -> str | None:
    """Resolve a direct HTTP URL for the parcel detail document (often the detail iframe)."""
    from urllib.parse import quote, urlparse

    best_url: str | None = None
    best_score = 0
    digits = re.sub(r"\D", "", query_value or "")

    for frame in page.frames:
        fu = (frame.url or "").strip()
        if not fu.startswith("http"):
            continue
        text = await _scope_body_text(frame)
        score = len(text) if panel_has_property_data(text) else 0
        if digits and digits in re.sub(r"\D", "", fu):
            score += 500
        if "pagetypeid=4" in fu.lower() or "parcel" in fu.lower():
            score += 200
        if score > best_score:
            best_score = score
            best_url = fu

    if best_url:
        return best_url

    page_url = page.url or ""
    low = page_url.lower()
    if query_value and is_gis_portal_url(page_url):
        try:
            from app.extraction.portal_configs import normalize_florida_pa_parcel

            norm_key = normalize_florida_pa_parcel(query_value)
        except Exception:
            norm_key = query_value.strip()
        parsed = urlparse(page_url)
        if parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/gis/?PageTypeID=4&KeyValue={quote(norm_key, safe='-')}"
    return None


async def render_assessor_detail_pdf_playwright(
    page: Page,
    dest_pdf: Any,
    *,
    query_value: str = "",
) -> bool:
    """
    Save assessor property card as PDF via Playwright on the **live** parcel detail view.
    Never cold-navigate to KeyValue URLs (DeSoto returns Input Error without search session).
    """
    from pathlib import Path

    pdf_path = Path(dest_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Rendering GIS assessor PDF on live session for parcel %s",
        query_value or "unknown",
    )

    await escape_print_preview_route(page)
    await ensure_gis_parcel_detail(page, query_value)
    await activate_parcel_details_tab(page)
    await expand_collapsible_detail_sections(page, query_value=query_value)
    if await page_has_portal_search_error(page):
        logger.warning("Portal search error on page — skipping Playwright PDF render")
        return False
    if not await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=12000):
        logger.warning("Parcel detail not ready — skipping Playwright PDF render")
        return False

    try:
        await page.emulate_media(media="print")
        await page.pdf(
            path=str(pdf_path),
            format="Letter",
            print_background=True,
            prefer_css_page_size=True,
            margin=PDF_RENDER_MARGIN,
        )
        if pdf_path.exists():
            raw = pdf_path.read_bytes()
            if pdf_bytes_looks_valid(raw, query_value):
                logger.info("Rendered valid assessor PDF (%d bytes)", len(raw))
                return True
            logger.warning("Rejecting invalid assessor PDF (portal error or missing parcel data)")
            pdf_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Playwright assessor PDF render failed: %s", exc)
    return False


GIS_OFFICIAL_DOC_LABELS = (
    ("Property Card", "Assessor Property Card"),
    ("TRIM (pdf)", "TRIM Notice"),
)


async def try_gis_official_document_downloads(
    page: Page,
    dest_dir: Any,
    clean_id: str,
    *,
    query_value: str = "",
) -> list[dict[str, Any]]:
    """Download DeSoto / floridapa GIS toolbar PDFs (Property Card, TRIM)."""
    from pathlib import Path

    if not is_gis_portal_url(page.url or ""):
        return []

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    await ensure_gis_parcel_detail(page, query_value)
    await activate_parcel_details_tab(page)
    if await page_has_portal_search_error(page):
        logger.warning("Cannot download GIS documents: portal search error visible")
        return []
    await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=8000)

    saved: list[dict[str, Any]] = []
    link_patterns = (
        ("Property Card", ("Property Card",)),
        ("TRIM (pdf)", ("TRIM (pdf)", "TRIM")),
    )
    for label, patterns in link_patterns:
        doc_type = GIS_OFFICIAL_DOC_LABELS[0][1] if "Property" in label else GIS_OFFICIAL_DOC_LABELS[1][1]
        for scope in [page, *page.frames]:
            try:
                link = None
                for pat in patterns:
                    loc = scope.locator(f"a:has-text('{pat}')").first
                    if await loc.count() > 0 and await loc.is_visible(timeout=700):
                        link = loc
                        break
                if link is None and key:
                    loc = scope.get_by_role("link", name=re.compile(re.escape(key[:12]), re.I)).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=700):
                        link = loc
                if link is None:
                    loc = scope.locator("a").filter(has_text=re.compile(r"property\s+card", re.I)).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=700):
                        link = loc
                if link is None:
                    continue
                slug = re.sub(r"[^\w.-]+", "_", label.lower())
                out_path = dest / f"{slug}_{clean_id}.pdf"
                logger.info("GIS official document download: %s", label)
                popup = None
                try:
                    async with page.expect_download(timeout=18000) as dl_info:
                        await link.click(force=True, timeout=3500)
                    download = await dl_info.value
                    fname = download.suggested_filename or out_path.name
                    if fname.lower().endswith(".pdf"):
                        out_path = dest / fname
                    await download.save_as(str(out_path))
                except Exception:
                    try:
                        async with page.context.expect_page(timeout=12000) as pop_info:
                            await link.click(force=True, timeout=3500)
                        popup = await pop_info.value
                        await popup.wait_for_load_state("domcontentloaded", timeout=15000)
                        await popup.wait_for_timeout(800)
                        await popup.emulate_media(media="print")
                        await popup.pdf(
                            path=str(out_path),
                            format="Letter",
                            print_background=True,
                            margin=PDF_RENDER_MARGIN,
                        )
                        await popup.close()
                        popup = None
                    except Exception as pop_err:
                        logger.debug("GIS doc popup note (%s): %s", label, pop_err)
                        if popup:
                            try:
                                await popup.close()
                            except Exception:
                                pass
                        continue

                if out_path.exists() and out_path.stat().st_size > 1500:
                    raw = out_path.read_bytes()
                    if not pdf_bytes_looks_valid(raw, query_value):
                        logger.warning("Rejecting GIS %s — invalid or error PDF", label)
                        out_path.unlink(missing_ok=True)
                        continue
                    saved.append({
                        "document_type": doc_type,
                        "document_title": f"Assessor Property Card - {clean_id}.pdf"
                        if doc_type == "Assessor Property Card"
                        else out_path.name,
                        "file_path": str(out_path),
                        "source_url": page.url,
                        "document_url": page.url,
                    })
                    logger.info("Saved GIS %s (%d bytes)", label, out_path.stat().st_size)
                    break
            except Exception:
                continue
    return saved


async def try_portal_assessor_file_download(
    page: Page,
    dest_pdf: Any,
    *,
    query_value: str = "",
) -> bool:
    """Click portal links that trigger a real PDF download (never toolbar Print / window.print)."""
    from pathlib import Path

    if is_gis_portal_url(page.url or ""):
        return False

    pdf_path = Path(dest_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    for scope in [page, *page.frames]:
        for sel in PORTAL_FILE_DOWNLOAD_SELECTORS:
            try:
                link = scope.locator(sel).first
                if await link.count() == 0 or not await link.is_visible(timeout=600):
                    continue
                href = (await link.get_attribute("href") or "").strip()
                if href.lower().startswith("javascript:"):
                    continue
                if _href_looks_like_print_route(href):
                    logger.debug("Skipping print-layout link (screenshot capture used instead): %s", href[:120])
                    continue
                logger.info("Trying assessor download link: %s", (href or sel)[:120])
                try:
                    async with page.expect_download(timeout=12000) as dl_info:
                        await link.click(force=True, timeout=3000)
                    download = await dl_info.value
                    await download.save_as(str(pdf_path))
                    if pdf_path.exists() and pdf_path.stat().st_size > 1500:
                        logger.info("Saved assessor document via download (%d bytes)", pdf_path.stat().st_size)
                        return True
                except Exception:
                    popup = None
                    try:
                        if not href or href == "#":
                            continue
                        if _href_looks_like_print_route(href):
                            continue
                        async with page.context.expect_page(timeout=10000) as pop_info:
                            await link.click(force=True, timeout=3000)
                        popup = await pop_info.value
                        await popup.add_init_script(_SUPPRESS_NATIVE_PRINT_INIT)
                        await popup.wait_for_load_state("domcontentloaded", timeout=15000)
                        await popup.wait_for_timeout(600)
                        if query_value:
                            await wait_for_property_detail_ready(popup, query_value=query_value, timeout_ms=12000)
                        await popup.emulate_media(media="print")
                        await popup.pdf(
                            path=str(pdf_path),
                            format="Letter",
                            print_background=True,
                            margin=PDF_RENDER_MARGIN,
                        )
                        await popup.close()
                        if pdf_path.exists() and pdf_path.stat().st_size > 1500:
                            return True
                    except Exception:
                        if popup:
                            try:
                                await popup.close()
                            except Exception:
                                pass
            except Exception:
                continue
    return False


async def capture_property_detail_png_bytes(page: Page, *, query_value: str = "") -> bytes | None:
    """Screenshot the frame or panel that actually contains parcel owner/values."""
    if is_standalone_parcel_detail_url(page.url or ""):
        return None
    await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=12000)

    best: bytes | None = None
    best_len = 0

    for frame in page.frames:
        text = await _scope_body_text(frame)
        if not panel_has_property_data(text):
            continue
        try:
            png = await frame.locator("body").screenshot(type="png")
            if png and len(png) > best_len:
                best = png
                best_len = len(png)
        except Exception:
            continue

    for sel in DETAIL_PANEL_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0 or not await loc.is_visible(timeout=900):
                continue
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            inner = (await loc.inner_text(timeout=2000) or "")[:2000]
            if tag == "iframe":
                frame = loc.content_frame()
                if frame:
                    inner = await _scope_body_text(frame)
                    if panel_has_property_data(inner):
                        png = await frame.locator("body").screenshot(type="png")
                        if png and len(png) > best_len:
                            best = png
                            best_len = len(png)
                continue
            if not panel_has_property_data(inner):
                continue
            box = await loc.bounding_box()
            if not box or box.get("height", 0) < 120:
                continue
            png = await loc.screenshot(type="png")
            if png and len(png) > best_len:
                best = png
                best_len = len(png)
        except Exception:
            continue

    if best and best_len > 4000:
        return best
    return None


async def capture_detail_screenshot_bytes(page: Page, *, query_value: str = "") -> bytes | None:
    """Prefer a detail panel crop; fall back to full-page when property detail is visible."""
    await recover_from_print_route_page(page, query_value)
    await suppress_native_print_dialog(page)
    if await disclaimer_still_visible(page):
        logger.warning("Skipping screenshot: disclaimer overlay still visible")
        return None

    if is_gis_portal_url(page.url or "") and query_value:
        await ensure_gis_parcel_detail(page, query_value)
        await activate_parcel_details_tab(page, skip_print_routes=True)
        await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=15000)
    elif query_value:
        await ensure_property_detail_workspace(page, query_value)

    if is_standalone_parcel_detail_url(page.url or ""):
        await wait_for_property_detail_ready(page, query_value=query_value, timeout_ms=12000)
        await expand_collapsible_detail_sections(page, query_value=query_value)
        await scroll_property_detail_page(page)
        try:
            png = await page.screenshot(full_page=True, type="png")
            if png and len(png) > 4000:
                logger.info(
                    "Captured direct property detail full-page screenshot (%d bytes)",
                    len(png),
                )
                return png
        except Exception as exc:
            logger.debug("Direct property detail full-page screenshot: %s", exc)

    png = await capture_property_detail_png_bytes(page, query_value=query_value)
    if png:
        logger.info("Captured property detail screenshot (%d bytes)", len(png))
        return png

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text(timeout=3000))[:8000]
        if DISCLAIMER_HINT.search(body_text[:2000]):
            logger.warning("Skipping full-page screenshot: disclaimer still visible on page")
            return None
    except Exception:
        pass

    if not body_likely_property_detail(body_text, query_value):
        detail_url = await discover_parcel_detail_url(page, query_value)
        if detail_url and not is_print_route_url(detail_url):
            shot_page = await page.context.new_page()
            try:
                await shot_page.add_init_script(_SUPPRESS_NATIVE_PRINT_INIT)
                await shot_page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
                await shot_page.wait_for_timeout(800)
                inner = (await shot_page.locator("body").inner_text(timeout=4000))[:8000]
                if body_likely_property_detail(inner, query_value):
                    png = await shot_page.screenshot(full_page=True, type="png")
                    if png and len(png) > 4000:
                        logger.info("Captured full-page detail screenshot via detail URL (%d bytes)", len(png))
                        return png
            except Exception as side_err:
                logger.debug("Side-tab detail screenshot: %s", side_err)
            finally:
                try:
                    await shot_page.close()
                except Exception:
                    pass
        if not body_likely_property_detail(body_text, query_value):
            logger.warning("Skipping full-page screenshot: no property detail text on page")
            return None

    if is_print_route_url(page.url or ""):
        logger.warning("Still on print-layout URL; refusing screenshot of print preview")
        return None

    try:
        await expand_collapsible_detail_sections(page, query_value=query_value)
        await scroll_property_detail_page(page)
        png = await page.screenshot(full_page=True, type="png")
        if png and len(png) > 4000:
            logger.info("Captured full-page property screenshot (%d bytes)", len(png))
            return png
    except Exception as exc:
        logger.debug("Full-page screenshot fallback failed: %s", exc)
    return None
