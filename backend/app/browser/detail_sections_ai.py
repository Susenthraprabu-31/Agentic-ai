"""AI-driven expansion of collapsed property appraiser detail sections before OCR/capture."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from playwright.async_api import Frame, Locator, Page

from app.browser.property_capture_prep import _href_looks_like_print_route
from app.core.config import settings
from app.core.llm_client import chat_completions_create, llm_configured

logger = logging.getLogger(__name__)

DETAIL_SECTION_CANDIDATES_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    const bodyText = (document.body ? document.body.innerText : '').slice(0, 14000).toLowerCase();

    const isDetail =
        /parcel|folio|strap|pcn|property\s+detail|owner|situs|legal\s+description|assessed|just\s+value|taxable/i.test(bodyText) &&
        !/search\s+by\s+owner|enter\s+parcel|property\s+search\s+results/i.test(bodyText.slice(0, 2500));

    if (!isDetail) {
        return { is_property_detail: false, candidates: [] };
    }

    const esc = (s) => (s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').trim();

    const isVisible = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
    };

    const noise =
        /^(home|search|map|menu|close|print|pdf|login|help|nearby|show\s+full\s+map|show\s+5\s+year|show\s+10\s+year)$/i;

    const sectionHint =
        /owner|sales|exemption|property\s+information|appraisal|assessed|taxable|taxes|building|land|valuation|legal|mailing|situs|location|value|history|transfer|feature|improvement|sketch|photo|document/i;

    function selectorFor(el) {
        if (el.id && !/^\d/.test(el.id)) return '#' + CSS.escape(el.id);
        const role = el.getAttribute('role');
        const text = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
        if (text && text.length >= 3 && text.length <= 64) {
            const tag = el.tagName.toLowerCase();
            if (tag === 'a' || tag === 'button') {
                return `${tag}:has-text("${esc(text)}")`;
            }
            return `${tag}:has-text("${esc(text)}")`;
        }
        return '';
    }

    function panelLooksCollapsed(header, panel) {
        if (!panel) return true;
        const s = window.getComputedStyle(panel);
        if (s.display === 'none' || s.visibility === 'hidden' || s.maxHeight === '0px') return true;
        if (panel.classList.contains('collapse') && !panel.classList.contains('show')) return true;
        if (panel.getAttribute('aria-hidden') === 'true') return true;
        const h = panel.getBoundingClientRect().height;
        return h < 12;
    }

    function addCandidate(el, label, reason, collapsed) {
        if (!isVisible(el)) return;
        const sel = selectorFor(el);
        if (!sel || seen.has(sel)) return;
        const t = (label || el.innerText || '').replace(/\s+/g, ' ').trim();
        if (t.length < 3 || t.length > 72 || noise.test(t)) return;
        seen.add(sel);
        out.push({
            index: out.length,
            label: t.slice(0, 72),
            selector: sel,
            collapsed: Boolean(collapsed),
            reason: reason || 'section_header',
            aria_expanded: el.getAttribute('aria-expanded'),
        });
    }

    document.querySelectorAll('[aria-expanded="false"]').forEach((el) => {
        const label = (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
        if (sectionHint.test(label) || sectionHint.test(el.getAttribute('aria-controls') || '')) {
            addCandidate(el, label, 'aria_expanded_false', true);
        }
    });

    document.querySelectorAll(
        'button, a, [role="button"], .accordion-toggle, .panel-heading, .panel-title, ' +
            '.ui-accordion-header, [data-toggle="collapse"], [data-bs-toggle="collapse"], tr, div'
    ).forEach((el) => {
        if (!isVisible(el)) return;
        const raw = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (raw.length < 4 || raw.length > 72) return;
        if (!sectionHint.test(raw)) return;
        if (noise.test(raw)) return;
        let collapsed = el.getAttribute('aria-expanded') === 'false';
        const panel =
            (el.getAttribute('aria-controls') && document.getElementById(el.getAttribute('aria-controls'))) ||
            el.nextElementSibling ||
            el.parentElement && el.parentElement.nextElementSibling;
        if (panel) collapsed = collapsed || panelLooksCollapsed(el, panel);
        if (collapsed || /^\+$|expand|show more/i.test(raw)) {
            addCandidate(el, raw, 'section_header', collapsed);
        }
    });

    document.querySelectorAll('span, i, div, button, a').forEach((icon) => {
        const t = (icon.innerText || icon.textContent || '').trim();
        if (!/^\+$|^▶$|^►$|expand$/i.test(t)) return;
        let header =
            icon.closest('tr, .panel-heading, .card-header, .accordion-header, [role="button"], button, a, div, li') ||
            icon.parentElement;
        for (let hop = 0; hop < 4 && header; hop++) {
            const lbl = (header.innerText || '').replace(/\s+/g, ' ').trim();
            if (lbl.length >= 8 && sectionHint.test(lbl)) {
                addCandidate(header, lbl, 'expand_icon_row', true);
                break;
            }
            header = header.parentElement;
        }
    });

    return { is_property_detail: true, candidates: out };
}
"""

COMPACT_DETAIL_SECTION_LABELS_JS = r"""
() => {
    const sectionHint =
        /owner|sales|exemption|property\s+information|appraisal|assessed|taxable|taxes|building|valuation/i;
    const noise = /^(home|search|map|menu|close|print|show\s+5\s+year|show\s+10\s+year)$/i;
    const out = [];
    const seen = new Set();
    document.querySelectorAll(
        'tr, td, th, div, button, a, span, [class*="header"], [class*="panel"], [class*="accordion"], [class*="section"]'
    ).forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.height > 88 || r.height < 8 || r.width < 60) return;
        let t = (el.innerText || '').replace(/\s+/g, ' ').trim();
        t = t.replace(/\s*[\u002b\+\u2212\-]\s*$/u, '').trim();
        if (t.length < 6 || t.length > 56 || noise.test(t)) return;
        if (!sectionHint.test(t)) return;
        const key = t.toLowerCase();
        if (!seen.has(key)) {
            seen.add(key);
            out.push(t);
        }
    });
    return out;
}
"""

DETAIL_SECTION_EXPAND_PROMPT = """You analyze a property appraiser / assessor **parcel detail** page. Many counties use stacked **collapsed sections** (accordions, + icons, aria-expanded=false) hiding Owner, Sales, Exemptions, Property Information, Appraised/Assessed Values, Taxes, and similar.

Your job: return **every collapsed section** that must be clicked open so OCR and extraction capture full data. Include sections that are already open only if they appear collapsed in the candidate list.

Do NOT click site navigation (Home, Search, Map, Print, Nearby Sales as navigation away). DO click in-page section headers and + expand controls for property data blocks.

Return ONLY valid JSON:
{{
  "is_property_detail": true,
  "sections_to_expand": [
    {{"label": "Owner Information", "click_selector": "tr:has-text(\\"Owner Information\\")", "reason": "collapsed owner block"}},
    {{"label": "Sales Information", "click_selector": "...", "reason": "..."}}
  ],
  "skip_reason": null
}}

If the page is not a parcel detail view or all sections are already expanded, set sections_to_expand=[].

URL: {url}
Title: {title}
Query context: {query_value}

Section candidates (from DOM analysis):
{candidates_json}

Visible page excerpt:
{page_excerpt}
"""


def _heuristic_sections_fixed(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candidates:
        if c.get("collapsed") is False and (c.get("aria_expanded") or "") == "true":
            continue
        sel = (c.get("selector") or "").strip()
        if not sel:
            continue
        out.append(
            {
                "label": c.get("label") or f"Section {c.get('index')}",
                "click_selector": sel,
                "reason": c.get("reason") or "heuristic",
            }
        )
    return out[:20]


async def _section_click_is_safe(loc: Locator) -> bool:
    try:
        href = (await loc.get_attribute("href") or "").strip()
        if href and _href_looks_like_print_route(href):
            return False
        onclick = (await loc.get_attribute("onclick") or "").lower()
        if "window.print" in onclick or "printbuilding" in onclick:
            return False
        target = (await loc.get_attribute("target") or "").lower()
        if target == "_blank" and href and _href_looks_like_print_route(href):
            return False
    except Exception:
        pass
    return True


async def _locate_section(scope: Frame | Page, click_selector: str, label: str):
    if click_selector:
        try:
            loc = scope.locator(click_selector).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
    if label:
        short = label.strip()[:56]
        try:
            loc = scope.get_by_text(re.compile(re.escape(short[:40]), re.I)).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
        esc = short[:48].replace("'", "\\'")
        try:
            loc = scope.locator(
                f"tr:has-text('{esc}'), div:has-text('{esc}'), "
                f"button:has-text('{esc}'), a:has-text('{esc}'), "
                f"[role='button']:has-text('{esc}')"
            ).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
    return None


async def _compact_section_labels(page: Page) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for frame in page.frames:
        try:
            for raw in await frame.evaluate(COMPACT_DETAIL_SECTION_LABELS_JS) or []:
                t = (raw or "").strip()
                if not t:
                    continue
                key = t.lower()
                if key in seen:
                    continue
                seen.add(key)
                labels.append(t)
        except Exception:
            continue
    return labels


async def expand_property_detail_sections_ai(
    page: Page,
    query_value: str = "",
) -> dict[str, Any]:
    """
    Discover collapsed property-detail sections, plan expansion with AI, click each open.
    Returns metadata for logging; does not extract fields itself.
    """
    all_candidates: list[dict[str, Any]] = []
    is_detail = False

    for idx, frame in enumerate(page.frames):
        try:
            res = await frame.evaluate(DETAIL_SECTION_CANDIDATES_JS)
            if res.get("is_property_detail"):
                is_detail = True
            for c in res.get("candidates") or []:
                c["frame_index"] = idx
                c["frame_url"] = frame.url
                all_candidates.append(c)
        except Exception:
            continue

    if not is_detail:
        return {"is_property_detail": False, "expanded": 0, "sections": []}

    compact_labels = await _compact_section_labels(page)
    if not all_candidates and compact_labels:
        all_candidates = [
            {
                "index": i,
                "label": lab,
                "selector": "",
                "collapsed": True,
                "reason": "compact_section_bar",
            }
            for i, lab in enumerate(compact_labels)
        ]

    page_excerpt = ""
    try:
        page_excerpt = (await page.inner_text("body"))[:5000]
    except Exception:
        page_excerpt = ""

    sections_to_expand: list[dict[str, Any]] = []

    if llm_configured() and all_candidates:
        try:
            title = ""
            try:
                title = await page.title() if not page.is_closed() else ""
            except Exception:
                pass
            prompt = DETAIL_SECTION_EXPAND_PROMPT.format(
                url=page.url or "",
                title=title,
                query_value=query_value,
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
                                "You identify collapsed property appraiser detail sections to expand for full data capture. "
                                "Return every collapsed data section, never site navigation. Respond in valid JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            ai_data = json.loads(resp.choices[0].message.content or "{}")
            logger.info("AI property detail section plan (via %s): %s", model_used, ai_data.get("sections_to_expand"))
            if ai_data.get("is_property_detail") is False:
                return {"is_property_detail": False, "expanded": 0, "sections": []}
            sections_to_expand = ai_data.get("sections_to_expand") or []
        except Exception as exc:
            logger.warning("AI detail section expansion plan fallback: %s", exc)

    if not sections_to_expand:
        sections_to_expand = _heuristic_sections_fixed(all_candidates)
    if not sections_to_expand and compact_labels:
        sections_to_expand = [
            {"label": lab, "click_selector": "", "reason": "compact_bar_heuristic"}
            for lab in compact_labels
        ]

    expanded_labels: list[str] = []
    seen_keys: set[str] = set()

    for section in sections_to_expand:
        label = (section.get("label") or "").strip()
        sel = (section.get("click_selector") or "").strip()
        key = (label or sel).lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        clicked = False
        for frame in page.frames:
            try:
                loc = await _locate_section(frame, sel, label)
                if loc is None or await loc.count() == 0:
                    continue
                if not await loc.is_visible(timeout=800):
                    continue
                if not await _section_click_is_safe(loc):
                    logger.info("Skipping section control (print-layout link): %s", label or sel)
                    continue
                await loc.scroll_into_view_if_needed(timeout=3000)
                try:
                    await loc.click(timeout=4000)
                except Exception:
                    await loc.click(force=True, timeout=4000)
                clicked = True
                await page.wait_for_timeout(900)
                break
            except Exception as click_err:
                logger.debug("Section expand click %s: %s", label or sel, click_err)
                continue

        if clicked:
            expanded_labels.append(label or sel)
            logger.info("Expanded property detail section: %s", label or sel)

    if expanded_labels:
        await page.wait_for_timeout(500)
        for section in sections_to_expand:
            label = (section.get("label") or "").strip()
            if not label or label in expanded_labels:
                continue
            for frame in page.frames:
                try:
                    loc = await _locate_section(frame, "", label)
                    if loc is None or await loc.count() == 0:
                        continue
                    await loc.scroll_into_view_if_needed(timeout=2000)
                    await loc.click(force=True, timeout=3000)
                    await page.wait_for_timeout(600)
                    expanded_labels.append(label)
                    break
                except Exception:
                    continue

    return {
        "is_property_detail": True,
        "expanded": len(expanded_labels),
        "sections": expanded_labels,
    }