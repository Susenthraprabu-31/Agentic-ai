"""AI-driven discovery and extraction of tax bill detail panel tabs (multi-vendor, dynamic, not county-hardcoded)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from playwright.async_api import Frame, Page

from app.core.config import settings
from app.core.llm_client import chat_completions_create, llm_configured

logger = logging.getLogger(__name__)

TAX_DETAIL_TAB_CANDIDATES_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    const bodyText = (document.body ? document.body.innerText : '').slice(0, 16000).toLowerCase();
    const path = (location.pathname || '').toLowerCase();

    // Dynamically identify if the page is a property tax bill / account detail view
    const isDetailUrl = /propertydetail|accountdetail|taxbill|billdetail|taxdetail|parceldetail|accountview|details/i.test(path);
    const hasTaxMarkers =
        bodyText.includes('tax bill detail') ||
        bodyText.includes('property tax account') ||
        bodyText.includes('real estate account') ||
        bodyText.includes('tax account') ||
        bodyText.includes('ad valorem') ||
        bodyText.includes('non-ad valorem') ||
        bodyText.includes('amount due') ||
        bodyText.includes('payment history') ||
        bodyText.includes('unpaid bills') ||
        bodyText.includes('assessments') ||
        bodyText.includes('tax district') ||
        bodyText.includes('millage') ||
        bodyText.includes('tax bill');

    const onBillView = isDetailUrl || hasTaxMarkers;
    if (!onBillView) {
        return { is_tax_bill_detail: false, candidates: [], has_tabs: false };
    }

    const esc = (s) => (s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').trim();

    const isVisible = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 4 && r.height > 4;
    };

    // Navigation and Action Noise Filters
    const navNoise =
        /^(home|search|register|log\s*in|login|logout|menu|cart|help|faq|contact|property\s+tax|business\s+tax|tourist\s+dev|misc|previous|next|shopping\s*cart)$/i;

    const actionNoise =
        /^(pay\s*now|pay\s*all(\s*bills)?|print\s*bill|print\s*receipt|print|download|property\s*appraiser|add\s*to\s*cart|view\s*cart|back\s*to\s*results)$/i;

    // Strategy 1: Explicit ASP.NET Ajax Tabs (.ajax__tab_tab, [id^="__tab_"], etc.)
    document.querySelectorAll('.ajax__tab_tab, a[id^="__tab_"], [id*="PropertyContainer"][id*="tab"]').forEach((el) => {
        if (!isVisible(el)) return;
        const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (text.length < 2 || text.length > 50 || navNoise.test(text) || actionNoise.test(text)) return;
        if (/^\d{4}$/.test(text)) return;

        let selector = '';
        if (el.id) {
            selector = '#' + CSS.escape(el.id);
        } else {
            selector = `a.ajax__tab_tab:has-text("${esc(text)}")`;
        }
        if (!seen.has(selector)) {
            seen.add(selector);
            const isActive = el.closest('.ajax__tab_active') !== null || el.classList.contains('ajax__tab_active');
            out.push({
                index: out.length,
                label: text,
                selector,
                is_active: isActive,
                type: 'aspnet_ajax',
            });
        }
    });

    // Strategy 2: ARIA and standard Framework Tabs ([role="tab"], .nav-tabs, [data-toggle="tab"], [data-bs-toggle="tab"])
    document.querySelectorAll('[role="tab"], .nav-tabs a, .nav-tabs button, .nav-pills a, .nav-pills button, [data-toggle="tab"], [data-bs-toggle="tab"]').forEach((el) => {
        if (!isVisible(el)) return;
        if (el.closest('header, footer, nav.navbar, .navbar, [role="navigation"]')) return;
        const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (text.length < 2 || text.length > 50 || navNoise.test(text) || actionNoise.test(text)) return;
        if (/^\d{4}$/.test(text)) return;

        let selector = '';
        if (el.id && !/^\d/.test(el.id)) {
            selector = '#' + CSS.escape(el.id);
        } else if (el.getAttribute('role') === 'tab') {
            selector = `[role="tab"]:has-text("${esc(text)}")`;
        } else {
            selector = `${el.tagName.toLowerCase()}:has-text("${esc(text)}")`;
        }
        if (!seen.has(selector)) {
            seen.add(selector);
            const isActive = el.getAttribute('aria-selected') === 'true' || el.classList.contains('active');
            out.push({
                index: out.length,
                label: text,
                selector,
                is_active: isActive,
                type: 'framework_tab',
            });
        }
    });

    // Strategy 3: Dynamic In-Page Tab Bars (containers with 2 to 12 visible sibling buttons/links)
    function scoreContainer(container) {
        if (!container) return 0;
        const links = container.querySelectorAll('a, button');
        let sc = 0;
        links.forEach((el) => {
            if (!isVisible(el)) return;
            const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
            if (t.length < 3 || t.length > 45) return;
            if (navNoise.test(t) || actionNoise.test(t)) return;
            if (/^\d{4}$/.test(t)) return; // sidebar year
            sc += 1;
        });
        return sc;
    }

    if (out.length < 2) {
        const containers = [];
        document.querySelectorAll('div, ul, nav, p, section').forEach((c) => {
            if (!isVisible(c)) return;
            if (c.closest('header, footer, nav.navbar, .navbar')) return;
            const sc = scoreContainer(c);
            if (sc >= 2 && sc <= 12) {
                containers.push({ el: c, score: sc });
            }
        });

        containers.sort((a, b) => b.score - a.score);

        if (containers.length > 0) {
            const best = containers[0].el;
            best.querySelectorAll('a, button').forEach((el) => {
                if (!isVisible(el)) return;
                const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
                if (text.length < 3 || text.length > 45 || navNoise.test(text) || actionNoise.test(text)) return;
                if (/^\d{4}$/.test(text)) return;

                let selector = '';
                if (el.id && !/^\d/.test(el.id)) {
                    selector = '#' + CSS.escape(el.id);
                } else {
                    const href = el.getAttribute('href') || '';
                    if (href && href.startsWith('#') && href.length > 1) {
                        selector = `a[href="${esc(href)}"]`;
                    } else {
                        selector = `${el.tagName.toLowerCase()}:has-text("${esc(text)}")`;
                    }
                }
                if (!seen.has(selector)) {
                    seen.add(selector);
                    out.push({
                        index: out.length,
                        label: text,
                        selector,
                        is_active: false,
                        type: 'container_tab',
                    });
                }
            });
        }
    }

    const dedup = [];
    const labels = new Set();
    out.forEach((c) => {
        const key = (c.label || '').toLowerCase();
        if (labels.has(key)) return;
        labels.add(key);
        dedup.push(c);
    });

    return {
        is_tax_bill_detail: true,
        candidates: dedup.slice(0, 15),
        has_tabs: dedup.length > 0,
    };
}
"""

TAB_PANEL_EXTRACTION_JS = r"""
() => {
    // 1. Identify the active panel or tab content container
    let panel = document.querySelector(
        '.ajax__tab_panel:not([style*="display: none"]), [role="tabpanel"]:not([hidden]):not([aria-hidden="true"]), .tab-pane.active'
    );
    if (!panel) {
        panel = document.querySelector('#MainContent_PropertyContainer_body, .ajax__tab_body, .tab-content, main, article, #content') || document.body;
    }

    // 2. Extract structured HTML tables
    const tables = [];
    panel.querySelectorAll('table').forEach((tbl, tIdx) => {
        const s = window.getComputedStyle(tbl);
        if (s.display === 'none' || s.visibility === 'hidden') return;

        let title = '';
        const caption = tbl.querySelector('caption');
        if (caption) title = caption.innerText.trim();
        if (!title) {
            let prev = tbl.previousElementSibling;
            while (prev && !title) {
                if (/^h[1-6]$/i.test(prev.tagName) || prev.classList.contains('table-title') || prev.classList.contains('header')) {
                    title = prev.innerText.trim();
                }
                prev = prev.previousElementSibling;
            }
        }
        if (!title && tbl.parentElement && tbl.parentElement !== panel) {
            const h = tbl.parentElement.querySelector('h1, h2, h3, h4, h5, h6, strong, b');
            if (h && h !== tbl) title = h.innerText.trim();
        }

        const headers = [];
        const ths = tbl.querySelectorAll('thead th, thead td, tr:first-child th');
        if (ths.length > 0) {
            ths.forEach((th) => headers.push(th.innerText.replace(/\s+/g, ' ').trim()));
        } else {
            const firstRow = tbl.querySelector('tr');
            if (firstRow) {
                firstRow.querySelectorAll('th, td').forEach((td) => headers.push(td.innerText.replace(/\s+/g, ' ').trim()));
            }
        }

        const rows = [];
        const trs = tbl.querySelectorAll('tbody tr, tr');
        trs.forEach((tr, rIdx) => {
            if (ths.length === 0 && rIdx === 0) return;
            const cells = [];
            tr.querySelectorAll('td, th').forEach((cell) => {
                cells.push(cell.innerText.replace(/\s+/g, ' ').trim());
            });
            if (cells.length > 0 && cells.some(c => c.length > 0)) {
                rows.push(cells);
            }
        });

        if (rows.length > 0 || headers.length > 0) {
            tables.push({
                index: tIdx,
                title: title || `Table ${tIdx + 1}`,
                headers,
                rows,
            });
        }
    });

    const text = (panel.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 10000);
    return {
        text,
        tables,
    };
}
"""

TAX_DETAIL_TAB_PROMPT = """You analyze a tax collector property tax bill / account detail page. Layouts differ by county and vendor (Phenix/floridatax, county-taxes.net, GovHub, TaxSys, and others). Do not assume a county name or a fixed tab list.

Your job is complete data capture. When the page has in-page content tabs or section links, **tabs_to_visit must include EVERY distinct content tab**, including the tab that is currently active/visible. Clicking each one reveals a different panel (Taxes, Unpaid Bills, Assessments, Legal Description, Payment History, Transactions, Exemptions, and similar).

INCLUDE every candidate that switches the bill detail panel.
ALSO add any content-tab label you can read in the page excerpt that is missing from the candidate list (using Playwright selector a:has-text("Exact Label")).

EXCLUDE only: site header menus (Home, Search, Register, Log In), Pay Now / Pay All Bills / Print Bill / Receipt / Shopping Cart, the year-and-due sidebar (a column of years such as 2026, 2025, …), and external links.

Do not return a subset. If five content tabs are present, return all five entries in the order they appear.
If the page has NO tabs (single-page or accordion layout), set tabs_to_visit=[].

Return ONLY valid JSON:
{{
  "is_tax_bill_detail": true,
  "tabs_to_visit": [
    {{"label": "Taxes", "click_selector": "a:has-text(\\"Taxes\\")", "reason": "ad valorem and non-ad valorem breakdown"}},
    {{"label": "All Unpaid Bills", "click_selector": "a:has-text(\\"All Unpaid Bills\\")", "reason": "unpaid folio rows"}},
    {{"label": "Assessments", "click_selector": "a:has-text(\\"Assessments\\")", "reason": "assessed, exemptions, taxable values"}},
    {{"label": "Legal Description", "click_selector": "a:has-text(\\"Legal Description\\")", "reason": "full legal description text"}},
    {{"label": "Payment History", "click_selector": "a:has-text(\\"Payment History\\")", "reason": "prior payments and receipts"}}
  ],
  "active_tab_label": "Taxes",
  "skip_reason": null
}}

Query account/parcel context: {query_value}
URL: {url}
Title: {title}

Tab candidates:
{candidates_json}

Visible page excerpt:
{page_excerpt}
"""

TAX_ACCOUNT_EXTRACTION_PROMPT = """You are an expert real estate title search analyst and property tax data extractor.
Analyze the complete multi-tab property tax bill details, breakdown tables, assessment values, unpaid amounts, and payment history below.

Extract every available detail from ALL tabs and return ONLY a valid JSON object matching this schema:
{{
  "account_number": "Property tax account number or folio (e.g. R02230-055)",
  "parcel_number": "Parcel ID or Folio number (e.g. R02230-055)",
  "bill_number": "Bill number if present (e.g. 3198)",
  "owner_name": "Full owner name(s) of record (e.g. PULIDO LUIS ALFONSO BAUTISTA)",
  "mailing_address": "Full mailing address (e.g. 148 LACOUR LN, PERRY FL 32348)",
  "property_address": "Site / property address if present, otherwise null",
  "legal_description": "Full legal description text from the Legal Description tab or summary",
  "tax_district": "Tax district code (e.g. CO)",
  "property_type": "Property type description (e.g. Real Estate)",
  "discount_period": "Discount period if present (e.g. 4%)",
  "tax_year": 2026,
  "tax_status": "Paid, Unpaid, or Delinquent",
  "amount_due": 883.66,
  "total_amount_due": 883.66,
  "amount_paid": 0.0,
  "is_delinquent": false,
  "current_bill_year": "2026",
  "tax_breakdown": {{
    "ad_valorem": [
      {{"authority": "TAYLOR COUNTY", "tax_rate": 7.2426, "charged": 361.77, "paid": 0.0, "due": 361.77}}
    ],
    "non_ad_valorem": [
      {{"authority": "LANDFILL", "charged": 170.0, "paid": 0.0, "due": 170.0}}
    ],
    "total_ad_valorem_millage": 14.2251,
    "total_ad_valorem_due": 742.48
  }},
  "unpaid_bills": [
    {{"year": "2026", "folio": "3198", "status": "Current", "minimum_due": 883.66, "due": 883.66}}
  ],
  "assessments": [
    {{"authority": "TAYLOR COUNTY", "improv": 49950.0, "land": 0.0, "taxable": 49950.0}}
  ],
  "payment_history": [
    {{"tax_year": "2025", "folio": "3194", "receipt_number": "2025-7938", "paid_by": "PULIDO LUIS", "payment_date": "04/15/2026", "paid": 928.95}}
  ],
  "account_history": [
    {{"bill": "2025 Annual Bill", "year": "2025", "amount_due": "$928.95", "status": "Paid", "payment_date": "04/15/2026", "receipt_number": "2025-7938"}}
  ]
}}

Rules:
1. Extract ALL tabs completely. If a tab has tables (like Payment History or Ad Valorem), capture all rows.
2. If there are unpaid bills from prior years (e.g. status Delinquent or Unpaid with year < current tax year), set is_delinquent=true.
3. If legal description is present in any tab (like 'Legal Description' tab), capture the complete text.
4. If a field is not present on this portal, set it to null or empty list/string rather than guessing.
"""


def _format_markdown_table(title: str, headers: list[str], rows: list[list[str]]) -> str:
    """Format an extracted HTML table as clean Markdown."""
    lines: list[str] = []
    if title:
        lines.append(f"### {title}")
    if headers:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        if headers:
            clean_cells = [(row[i] if i < len(row) else "").replace("|", "-") for i in range(len(headers))]
        else:
            clean_cells = [c.replace("|", "-") for c in row]
        lines.append("| " + " | ".join(clean_cells) + " |")
    return "\n".join(lines)


def _heuristic_tax_tabs(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback when LLM is unavailable: visit all discovered candidates in order."""
    preferred_order = (
        "taxes",
        "unpaid",
        "assessment",
        "legal",
        "payment",
        "history",
        "bill",
        "transaction",
    )

    def rank(label: str) -> int:
        low = label.lower()
        for i, token in enumerate(preferred_order):
            if token in low:
                return i
        return 50

    picked = sorted(candidates, key=lambda c: rank(c.get("label") or ""))
    out = []
    for c in picked[:10]:
        sel = c.get("selector")
        if not sel:
            continue
        out.append({"label": c.get("label"), "click_selector": sel, "reason": "heuristic tab candidate"})
    return out


async def _locate_tab(scope: Frame | Page, click_selector: str, label: str):
    """Locate a tab element using selector with text fallbacks."""
    try:
        loc = scope.locator(click_selector).first
        if await loc.count() > 0:
            return loc
    except Exception:
        pass
    if label:
        try:
            loc = scope.locator(
                f"a:has-text('{label}'), button:has-text('{label}'), [role='tab']:has-text('{label}')"
            ).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
    return None


async def _read_detail_panel_text(scope: Frame | Page) -> str:
    """Fallback reader for visible panel text."""
    try:
        return await scope.evaluate(
            r"""() => {
                const panel = document.querySelector(
                    '.ajax__tab_panel:not([style*="display: none"]), [role="tabpanel"]:not([hidden]), .tab-pane.active, #MainContent_PropertyContainer_body, main, article'
                ) || document.body;
                return (panel.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 12000);
            }"""
        )
    except Exception:
        try:
            return (await scope.inner_text("body"))[:12000]
        except Exception:
            return ""


def heuristic_structure_multi_tab_tax(tabs: dict[str, Any], body_text: str) -> dict[str, Any]:
    """
    Robust Python heuristic to structure multi-tab tax details when LLM is offline or in mock test mode.
    Extracts ad valorem, non-ad valorem, unpaid bills, assessments, legal description, and payment history.
    """
    combined = f"{body_text}\n" + "\n".join(
        t.get("text", "") for t in tabs.values() if isinstance(t, dict)
    )

    record: dict[str, Any] = {
        "account_number": "",
        "parcel_number": "",
        "bill_number": "",
        "owner_name": "",
        "property_address": "",
        "mailing_address": "",
        "legal_description": "",
        "tax_district": "",
        "property_type": "",
        "discount_period": "",
        "tax_year": 2026,
        "tax_status": "Unknown",
        "amount_due": 0.0,
        "total_amount_due": 0.0,
        "amount_paid": 0.0,
        "is_delinquent": False,
        "current_bill_year": "2026",
        "tax_breakdown": {"ad_valorem": [], "non_ad_valorem": []},
        "unpaid_bills": [],
        "assessments": [],
        "payment_history": [],
        "account_history": [],
    }

    # 1. Header parsing (FloridaTax / Phenix / GovHub formats)
    m_acct = re.search(
        r"(?:Property\s+Tax\s+Account|Real\s+Estate\s+Account|Account\s*#?|Folio\s*#?):?\s*([A-Za-z0-9\-_.]+)",
        combined,
        re.I,
    )
    if m_acct:
        record["account_number"] = m_acct.group(1).strip()
        record["parcel_number"] = record["account_number"]

    m_owner = re.search(
        r"Owner:?\s*([A-Za-z0-9\s,.\-&]+?)(?:\s+Discount|\s+Tax|\s+Bill|\s+Mailing|\n|$)",
        combined,
        re.I,
    )
    if m_owner:
        record["owner_name"] = m_owner.group(1).strip()

    m_mail = re.search(
        r"MAILING\s+ADDRESS:?\s*([^\n]+(?:\s+[^\n]+)?)",
        combined,
        re.I,
    )
    if m_mail:
        record["mailing_address"] = re.sub(r"\s+", " ", m_mail.group(1).strip())

    m_situs = re.search(
        r"(?:PROPERTY\s+ADDRESS|SITUS):?\s*([^\n]+(?:\s+[^\n]+)?)",
        combined,
        re.I,
    )
    if m_situs:
        cleaned_situs = re.sub(r"\s+", " ", m_situs.group(1).strip())
        if cleaned_situs and not re.match(r"^(payment\s+options|taxes|bill)", cleaned_situs, re.I):
            record["property_address"] = cleaned_situs

    m_bill = re.search(r"Bill\s+Number:?\s*(\d+)", combined, re.I)
    if m_bill:
        record["bill_number"] = m_bill.group(1).strip()

    m_dist = re.search(r"Tax\s+District:?\s*([A-Za-z0-9\-_]+)", combined, re.I)
    if m_dist:
        record["tax_district"] = m_dist.group(1).strip()

    m_ptype = re.search(r"Property\s+Type:?\s*([A-Za-z0-9\s]+?)(?:\s+Discount|\s+Owner|\n|$)", combined, re.I)
    if m_ptype:
        record["property_type"] = m_ptype.group(1).strip()

    m_disc = re.search(r"Discount\s+Period:?\s*([0-9%]+)", combined, re.I)
    if m_disc:
        record["discount_period"] = m_disc.group(1).strip()

    m_year = re.search(r"Year:?\s*(\d{4})", combined, re.I)
    if m_year:
        record["tax_year"] = int(m_year.group(1))
        record["current_bill_year"] = m_year.group(1)

    m_due = re.search(
        r"(?:This\s+Bill|Total\s+Amount\s+Due|Total\s+Due|Amount\s+Due):?\s*\$?([\d,]+(?:\.\d{2})?)",
        combined,
        re.I,
    )
    if m_due:
        val = float(m_due.group(1).replace(",", ""))
        record["total_amount_due"] = val
        record["amount_due"] = val
        record["tax_status"] = "Unpaid" if val > 0 else "PAID"

    # 2. Iterate through tabs and their structured tables
    for tab_label, tab_info in tabs.items():
        if not isinstance(tab_info, dict):
            continue
        low_label = tab_label.lower()
        tab_text = tab_info.get("text", "")
        tables = tab_info.get("tables", [])

        # Legal Description Tab
        if "legal" in low_label or "description" in low_label:
            cleaned_legal = re.sub(r"^.*?legal\s+description\s*", "", tab_text, flags=re.I).strip()
            record["legal_description"] = cleaned_legal or tab_text.strip()

        # Taxes Tab (Ad Valorem and Non-Ad Valorem)
        if "tax" in low_label or "charge" in low_label:
            for tbl in tables:
                title = tbl.get("title", "").lower()
                rows = tbl.get("rows", [])
                if "non-ad" in title or "non ad" in title:
                    for r in rows:
                        if len(r) >= 4:
                            record["tax_breakdown"]["non_ad_valorem"].append({
                                "authority": r[0],
                                "charged": r[1],
                                "paid": r[2],
                                "due": r[3],
                            })
                elif "ad valorem" in title or (rows and len(rows[0]) >= 5):
                    for r in rows:
                        if len(r) >= 5:
                            record["tax_breakdown"]["ad_valorem"].append({
                                "authority": r[0],
                                "tax_rate": r[1],
                                "charged": r[2],
                                "paid": r[3],
                                "due": r[4],
                            })

        # All Unpaid Bills Tab
        if "unpaid" in low_label or "bill" in low_label:
            for tbl in tables:
                rows = tbl.get("rows", [])
                for r in rows:
                    if len(r) >= 5 and re.match(r"^\d{4}$", r[0].strip()):
                        record["unpaid_bills"].append({
                            "year": r[0].strip(),
                            "folio": r[1].strip(),
                            "status": r[2].strip(),
                            "minimum_due": r[3].strip(),
                            "due": r[4].strip(),
                        })

        # Assessments Tab
        if "assessment" in low_label or "value" in low_label:
            for tbl in tables:
                rows = tbl.get("rows", [])
                for r in rows:
                    if len(r) >= 4 and not r[0].lower().startswith("authority"):
                        record["assessments"].append({
                            "authority": r[0],
                            "values": r[1:],
                        })

        # Payment History Tab
        if "payment" in low_label or "history" in low_label or "transaction" in low_label:
            for tbl in tables:
                rows = tbl.get("rows", [])
                for r in rows:
                    if len(r) >= 6 and re.match(r"^\d{4}$", r[0].strip()):
                        year = r[0].strip()
                        folio = r[1].strip()
                        receipt = r[2].strip()
                        paid_by = r[3].strip()
                        p_date = r[4].strip()
                        p_paid = r[5].strip()

                        record["payment_history"].append({
                            "tax_year": year,
                            "folio": folio,
                            "receipt_number": receipt,
                            "paid_by": paid_by,
                            "payment_date": p_date,
                            "paid": p_paid,
                        })
                        record["account_history"].append({
                            "bill": f"{year} Annual Bill",
                            "year": year,
                            "amount_due": p_paid,
                            "status": "Paid",
                            "payment_date": p_date,
                            "receipt_number": receipt,
                        })

    # Delinquency detection: if unpaid bills exist for earlier years
    if record["unpaid_bills"]:
        years = [int(b["year"]) for b in record["unpaid_bills"] if b.get("year", "").isdigit()]
        if len(years) > 1 or any(y < record["tax_year"] for y in years):
            record["is_delinquent"] = True

    return record


async def extract_tax_bill_detail_all_tabs(
    page: Page,
    query_value: str = "",
) -> dict[str, Any]:
    """
    Discover bill-detail tabs via page analysis + AI, click each, and return merged text/tables metadata.
    Completely dynamic across all counties, no hardcoded county names or fixed tab lists.
    """
    all_candidates: list[dict[str, Any]] = []
    is_detail = False

    for idx, frame in enumerate(page.frames):
        try:
            res = await frame.evaluate(TAX_DETAIL_TAB_CANDIDATES_JS)
            if res.get("is_tax_bill_detail"):
                is_detail = True
            for c in res.get("candidates") or []:
                c["frame_index"] = idx
                c["frame_url"] = frame.url
                all_candidates.append(c)
        except Exception:
            continue

    if not is_detail:
        return {"is_tax_bill_detail": False, "combined_markdown": "", "tabs": {}}

    tabs_to_visit: list[dict[str, Any]] = []
    page_excerpt = ""
    try:
        page_excerpt = (await page.inner_text("body"))[:4000]
    except Exception:
        page_excerpt = ""

    # 1. Plan Tab Visits using LLM or Heuristics
    if llm_configured() and all_candidates:
        try:
            prompt = TAX_DETAIL_TAB_PROMPT.format(
                query_value=query_value,
                url=page.url or "",
                title=await page.title() if not page.is_closed() else "",
                candidates_json=json.dumps(all_candidates, indent=2),
                page_excerpt=page_excerpt,
            )
            resp, model_used = await chat_completions_create(
                create_kwargs={
                    "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You identify all in-page tax bill detail content tabs on county tax portals. "
                                "Return all of them for full capture, never a subset. "
                                "Respond strictly in valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            ai_data = json.loads(resp.choices[0].message.content or "{}")
            logger.info("AI tax detail tab plan (via %s): %s", model_used, ai_data)
            if ai_data.get("is_tax_bill_detail") is False:
                return {"is_tax_bill_detail": False, "combined_markdown": "", "tabs": {}}
            tabs_to_visit = ai_data.get("tabs_to_visit") or []
        except Exception as exc:
            logger.warning("AI tax detail tab analysis fallback: %s", exc)

    if not tabs_to_visit and all_candidates:
        tabs_to_visit = [
            {"label": c.get("label"), "click_selector": c.get("selector"), "reason": "heuristic"}
            for c in _heuristic_tax_tabs(all_candidates)
        ]

    captured: dict[str, Any] = {}
    md_parts: list[str] = []

    async def capture_current(label: str) -> None:
        """Capture active tab panel structured tables and text."""
        for frame in page.frames:
            try:
                data = await frame.evaluate(TAB_PANEL_EXTRACTION_JS)
                txt = (data.get("text") or "").strip()
                tbls = data.get("tables") or []
                if len(txt) < 30 and not tbls:
                    continue

                captured[label] = {
                    "text": txt,
                    "tables": tbls,
                    "url": frame.url,
                }

                md_section = [f"## TAB: {label}"]
                if tbls:
                    for t in tbls:
                        md_section.append(
                            _format_markdown_table(t.get("title", ""), t.get("headers", []), t.get("rows", []))
                        )
                if txt:
                    md_section.append(txt)

                md_parts.append("\n\n".join(md_section) + "\n")
                break
            except Exception as e:
                logger.debug("Frame tab extraction note: %s", e)

    # Capture initial view (e.g. Taxes tab or single page view)
    initial_label = "Initial"
    if tabs_to_visit and tabs_to_visit[0].get("label"):
        initial_label = tabs_to_visit[0]["label"]
    await capture_current(initial_label)

    # 2. Visit each tab in order
    visited: set[str] = {initial_label.lower()}
    for tab in tabs_to_visit:
        label = (tab.get("label") or "").strip()
        sel = (tab.get("click_selector") or "").strip()
        if not label and not sel:
            continue
        key = (label or sel).lower()
        if key in visited:
            continue
        visited.add(key)

        clicked = False
        for frame in page.frames:
            try:
                loc = await _locate_tab(frame, sel, label)
                if loc is None or await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=600):
                    continue
                await loc.click(timeout=4000)
                clicked = True
                await page.wait_for_timeout(1400)
                break
            except Exception:
                continue

        if clicked:
            tab_label = label or sel
            await capture_current(tab_label)
            logger.info("Captured tax detail tab: %s", tab_label)

    # Restore initial tab view so subsequent screenshots reflect default bill
    first = tabs_to_visit[0] if tabs_to_visit else None
    if first:
        first_label = (first.get("label") or "").strip()
        first_sel = (first.get("click_selector") or "").strip()
        for frame in page.frames:
            try:
                loc = await _locate_tab(frame, first_sel, first_label)
                if loc is None or await loc.count() == 0:
                    continue
                await loc.click(timeout=4000)
                await page.wait_for_timeout(600)
                break
            except Exception:
                continue

    combined_md = "\n".join(md_parts).strip()

    # 3. AI Structuring of the full multi-tab record
    structured_record: dict[str, Any] = {}
    if llm_configured() and combined_md:
        try:
            resp, model_used = await chat_completions_create(
                create_kwargs={
                    "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You extract and structure complete property tax records from multi-tab county tax collector data. "
                                "Return all fields strictly matching the JSON schema."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"{TAX_ACCOUNT_EXTRACTION_PROMPT}\n\nTAX BILL AND ALL TABS CONTENT:\n{combined_md}",
                        },
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            structured_record = json.loads(resp.choices[0].message.content or "{}")
            logger.info("AI structured tax record generated via %s", model_used)
        except Exception as ai_rec_err:
            logger.warning("AI tax record structuring fallback: %s", ai_rec_err)

    # 4. Fallback heuristic structuring & merge
    body_summary = ""
    try:
        body_summary = (await page.inner_text("body"))[:10000]
    except Exception:
        body_summary = ""

    heuristic_record = heuristic_structure_multi_tab_tax(captured, body_summary)
    if not structured_record:
        structured_record = heuristic_record
    else:
        # Merge missing fields from heuristic into structured record
        for k, v in heuristic_record.items():
            if v and (k not in structured_record or not structured_record[k]):
                structured_record[k] = v

    return {
        "is_tax_bill_detail": True,
        "combined_markdown": combined_md,
        "tabs": captured,
        "tab_labels": list(captured.keys()),
        "structured_record": structured_record,
    }
