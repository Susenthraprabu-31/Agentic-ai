"""
AI-driven county portal analyzer and dynamic search executor.
Analyzes loaded county search portals, discovers tabs/inputs/buttons,
clicks the exact tab based on input type (parcel_id, address, owner_name),
fills fields, submits search, extracts records, and persists discovered recipes to PostgreSQL.
Includes full Florida and multi-county heuristics, AI LLM planning, and document generation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Optional

from playwright.async_api import Frame, Page
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.llm_client import chat_completions_create, llm_configured
from app.models.county_source import CountySource, SourceConfiguration
from app.models.recipe import Recipe, RecipeLifecycleStatus, RecipeVersion
from app.extraction.portal_configs import (
    is_collier_assessor,
    is_miami_dade_assessor,
    normalize_florida_parcel,
    format_miami_dade_address_for_search,
    format_florida_pa_address_for_search,
)
from app.extraction.florida_extractors import (
    FLORIDA_SCRAPE_JS,
    MIAMI_DADE_DETAIL_JS,
    DYNAMIC_FULL_PAGE_EXTRACTION_JS,
    extract_valid_miami_dade_folio,
    parcel_record_from_florida_data,
    parcel_record_from_miami_dade_detail,
    parse_collier_sections_data,
    parse_dynamic_full_page_record,
)
from app.extraction.assessor_reports import (
    download_collier_assessor_card,
    download_miami_dade_assessor_reports,
    register_downloaded_documents,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# DOM Inspection JavaScript for tabs, inputs, and search buttons across frames
# ---------------------------------------------------------------------------

PAGE_SNAPSHOT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const CONTAINER_TAGS = new Set(['DIV', 'NAV', 'HEADER', 'FOOTER', 'SECTION', 'ASIDE', 'UL', 'OL', 'LI', 'TABLE', 'TBODY', 'THEAD', 'TR', 'FORM', 'BODY', 'MAIN', 'ARTICLE', 'FIGURE']);

  function esc(s) {
    return (s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').trim();
  }

  function isExcluded(el) {
    if (!el) return true;
    const href = (el.getAttribute('href') || '').toLowerCase();
    if (href.includes('facebook.com') || href.includes('twitter.com') || href.includes('x.com') || 
        href.includes('linkedin.com') || href.includes('instagram.com') || href.includes('youtube.com') ||
        href.startsWith('mailto:') || href.startsWith('tel:')) {
      return true;
    }
    const id = (el.id || '').toLowerCase();
    if (id.includes('facebook') || id.includes('twitter') || id.includes('linkedin') || id.includes('shareicon') || id.includes('printsetting')) {
      return true;
    }
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (/\b(disagree|decline|reject|cancel|leave|exit|close window)\b/i.test(text)) {
      return true;
    }
    return false;
  }

  function selectorFor(el) {
    if (!el || isExcluded(el)) return null;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' && !el.getAttribute('href')?.startsWith('#Tab')) return null;
      if (style.visibility === 'hidden') return null;
    } catch (_) {}

    const tag = el.tagName.toUpperCase();

    // Never return container tags as buttons or inputs
    if (CONTAINER_TAGS.has(tag) && el.getAttribute('role') !== 'tab') return null;

    if (tag === 'INPUT' || tag === 'TEXTAREA') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'hidden' || type === 'checkbox') return null;
      if (type === 'radio') {
        if (el.id && !/^\d/.test(el.id)) return `#${CSS.escape(el.id)}`;
        if (el.name && el.value) return `input[type="radio"][name="${esc(el.name)}"][value="${esc(el.value)}"]`;
        if (el.value) return `input[type="radio"][value="${esc(el.value)}"]`;
        if (el.name) return `input[type="radio"][name="${esc(el.name)}"]`;
        return 'input[type="radio"]';
      }
      const ph = el.getAttribute('placeholder');
      if (ph) return `${el.tagName.toLowerCase()}[placeholder="${esc(ph)}"]`;
      const name = el.getAttribute('name');
      if (name) return `${el.tagName.toLowerCase()}[name="${esc(name)}"]`;
      const fc = el.getAttribute('formcontrolname');
      if (fc) return `input[formcontrolname="${esc(fc)}"]`;
      const aria = el.getAttribute('aria-label');
      if (aria) return `${el.tagName.toLowerCase()}[aria-label="${esc(aria)}"]`;
    }

    if (el.id && !/^\d/.test(el.id)) {
      const idLower = el.id.toLowerCase();
      if (!idLower.includes('tabrow') && !idLower.includes('homepage') && !idLower.includes('logo')) {
        return `#${CSS.escape(el.id)}`;
      }
    }

    const role = el.getAttribute('role');
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60);
    if (role === 'tab' && text) return `[role="tab"]:has-text("${esc(text)}")`;

    const href = el.getAttribute('href') || '';
    if (href.startsWith('#Tab')) return `a[href="${esc(href)}"]`;

    if ((tag === 'BUTTON' || tag === 'A') && text) {
      const lower = text.toLowerCase();
      if (lower.length <= 40 && /search|address|owner|folio|parcel|submit|go|find/i.test(lower)) {
        return `${el.tagName.toLowerCase()}:has-text("${esc(text)}")`;
      }
    }

    const aria = el.getAttribute('aria-label');
    if (aria && /search/i.test(aria)) return `[aria-label="${esc(aria)}"]`;

    const dt = el.getAttribute('data-test');
    if (dt && /search/i.test(dt)) return `[data-test="${esc(dt)}"]`;

    if (tag === 'BUTTON' && (el.className || '').includes('search-icon')) return 'button.search-icon';

    return null;
  }

  function push(el, kind) {
    if (isExcluded(el)) return;
    const sel = selectorFor(el);
    if (!sel || seen.has(sel)) return;
    seen.add(sel);
    const text = (el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80);

    let label = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${el.id}"]`);
      if (l) label = (l.innerText || l.textContent || '').trim();
    }
    if (!label && el.parentElement) {
      label = (el.parentElement.innerText || el.parentElement.textContent || '').trim();
    }

    out.push({
      kind,
      selector: sel,
      tag: el.tagName.toLowerCase(),
      text,
      label,
      placeholder: el.getAttribute('placeholder') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      role: el.getAttribute('role') || '',
      href: el.getAttribute('href') || '',
      formcontrolname: el.getAttribute('formcontrolname') || '',
      dataBind: el.getAttribute('data-bind') || '',
      type: el.type || '',
    });
  }

  document.querySelectorAll('[role="tab"], a[href^="#Tab"], button, a, input, textarea, select, [aria-label]').forEach((el) => {
    try {
      if (isExcluded(el)) return;
      const tag = el.tagName.toUpperCase();
      const role = el.getAttribute('role') || '';
      const href = el.getAttribute('href') || '';
      const text = (el.innerText || el.textContent || '').trim();
      const aria = el.getAttribute('aria-label') || '';
      const lowerText = text.toLowerCase();
      const lowerAria = aria.toLowerCase();

      // Skip non-tab container elements
      if (CONTAINER_TAGS.has(tag) && role !== 'tab') return;

      let kind = 'control';
      if (role === 'tab' || href.startsWith('#Tab') || /ui-tabs-anchor/i.test(el.className || '')) {
        kind = 'tab';
      } else if (tag === 'INPUT' && (el.type || '').toLowerCase() === 'radio') {
        kind = 'radio';
      } else if (tag === 'INPUT' || tag === 'TEXTAREA') {
        kind = 'input';
      } else if (tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase()))) {
        if (/search|find|lookup|submit|go/i.test(lowerText) || /search|find|lookup|submit|go/i.test(lowerAria) || /search|find|submit/i.test(el.id || '')) {
          kind = 'search_button';
        } else {
          kind = 'button';
        }
      } else if (tag === 'A') {
        // Section toggles (e.g. "Search by Parcel Number")
        if (/search by|toggle/i.test(lowerText) || /toggle/i.test(el.className || '')) {
          kind = 'tab';
        } else if (/search|find|lookup|submit|go/i.test(lowerText) || /search|find/i.test(lowerAria) || /btnsearch|search/i.test(el.id || '')) {
          kind = 'search_button';
        } else {
          kind = 'button';
        }
      }
      push(el, kind);
    } catch (_) {}
  });

  return {
    url: window.location.href,
    title: document.title,
    elements: out.slice(0, 100),
  };
}
"""

SYSTEM_PROMPT_ASSESSOR = """You analyze property appraiser / assessor websites and return Playwright CSS selectors to run a search.

Given:
- query_type: address | owner | parcel (parcel may be labeled folio, APN, TMK, PIN, strap, account #, etc.)
- query_value: the value to search for
- page snapshot: visible tabs, radio buttons, inputs, and search buttons with suggested selectors, labels, and placeholders

Return ONLY valid JSON:
{
  "tab_selector": "selector to click the correct search tab (e.g. [role='tab']:has-text('Basic Search'), a:has-text('Parcel')), or null if not needed",
  "radio_selector": "selector for radio option if the portal requires selecting search mode (e.g. Folio vs Parcel Number, Real Property vs Tangible), or null",
  "input_selector": "selector for the text field to fill",
  "submit_selector": "selector for search/submit button, or null to press Enter",
  "use_enter_key": false,
  "confidence": "high|medium|low",
  "reasoning": "one short sentence"
}

Strict Rules:
1. FIELD IDENTIFICATION & PLACEHOLDER FORMATS:
   - Many assessor portals do NOT have 'parcel' or 'address' in field names/IDs. Instead, they use example format placeholders:
     * Parcel / Folio: placeholders matching numeric/hyphenated examples (e.g. 'EX:121415-1214', 'EX: 00-00-00-000', 'enter parcel number...', '123456-7890') or data-bind containing 'folio'/'pin'/'parcel'.
     * Owner Name: placeholders matching name examples (e.g. 'EX: SMITH, JOHN L', 'enter name...', 'DOE, J', 'Last, First') or data-bind containing 'owner'.
     * Address: placeholders matching street examples (e.g. 'Ex: ARMENIA', 'enter address...', '123 MAIN ST', 'Street Name') or data-bind containing 'address'/'street'.
   - Always match query_type to the field whose label, placeholder format, or attribute matches that type.

2. TABS & SECTION TOGGLES:
   - If the portal groups searches under tabs or collapsible accordion toggles (e.g. 'Search by Parcel Number', 'Search by Owner Name', 'Search by Location Address'):
     * Set tab_selector to the toggle or tab selector that opens the relevant search section.

3. RADIO BUTTONS:
   - If the form uses radio buttons to choose query mode (e.g. radio with label 'Folio' vs 'Parcel Number'):
     * For parcel/folio query, return the selector of the matching radio button (e.g. input[type="radio"][value="folio"]).

4. INPUT SELECTOR:
   - Return a specific, visible CSS selector for the text field (e.g. input[placeholder="enter parcel number..."]).
   - NEVER select a radio button, checkbox, or hidden element as input_selector.

5. SUBMIT / ENTER:
   - Return the visible search button selector for the specific query section (e.g. #ctlBodyPane_ctl02_ctl01_btnSearch, button:has-text("Search"), input[type="submit"]).
   - NEVER select container elements (div, nav, header), social share buttons (Facebook, Twitter, LinkedIn), or homepage links.
   - If no explicit submit button exists or the portal uses autocomplete / Enter key, set use_enter_key to true and submit_selector to null.
   - NEVER invent or hallucinate selectors not present in the snapshot elements."""

# Fallback generic scrape for standard property cards
PROPERTY_SCRAPE_JS = """
() => {
  const data = {};
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  const set = (key, val) => {
    const v = clean(val);
    if (key && v && v.length < 1000 && !data[key.toLowerCase()]) data[key.toLowerCase()] = v;
  };

  const byId = (id, key) => {
    const el = document.getElementById(id);
    if (el) set(key || id.replace(/_/g, ' '), el.innerText || el.textContent);
  };

  byId('main_parcel', 'parcel id');
  byId('ParcelID', 'parcel id');
  byId('owner_name', 'owner name');
  byId('OwnerLine1', 'owner name');
  byId('site_address', 'site address');
  byId('SiteStreetAddress', 'site address');
  byId('legal', 'legal description');
  byId('Legal', 'legal description');
  byId('TrTotalJustValue', 'just value');
  byId('TrCountyAssessedValue', 'assessed value');

  document.querySelectorAll('.clsLabel, .subhead, th, td.label').forEach((label) => {
    const key = clean(label.innerText || label.textContent).replace(/:$/, '');
    const next = label.nextElementSibling;
    if (next && key) set(key, next.innerText || next.textContent);
  });

  return data;
}
"""


class PageSearchAI:
    """
    Analyzes loaded county portals and executes tab selection, field fill, and search submit.
    Persists discovered steps to recipes and recipe_versions in PostgreSQL.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def extract_snapshot(self, page: Page) -> dict[str, Any]:
        """Extracts interactive elements across main page and all child frames."""
        elements: list[dict[str, Any]] = []
        seen = set()

        try:
            main_snap = await page.evaluate(PAGE_SNAPSHOT_JS)
            for el in main_snap.get("elements", []):
                k = (el.get("selector"), el.get("frame"))
                if k not in seen:
                    seen.add(k)
                    elements.append(el)
        except Exception as exc:
            logger.warning("Main page snapshot evaluation failed: %s", exc)

        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_snap = await frame.evaluate(PAGE_SNAPSHOT_JS)
                for el in frame_snap.get("elements", []):
                    el["frame"] = frame.name or "child_frame"
                    k = (el.get("selector"), el.get("frame"))
                    if k not in seen:
                        seen.add(k)
                        elements.append(el)
            except Exception:
                continue

        return {
            "url": page.url,
            "title": await page.title() if not page.is_closed() else "",
            "elements": elements,
        }

    async def plan_search(
        self,
        snapshot: dict[str, Any],
        query_type: str,
        query_value: str,
    ) -> dict[str, Any]:
        """
        Synthesizes an execution plan mapping query_type (parcel, address, owner)
        to the exact tab selector, input selector, and submit button selector.
        Uses OpenAI/Groq if configured, with specialized multi-county deterministic fallbacks.
        """
        url = snapshot.get("url", "")
        qt = query_type.lower().strip()
        if qt in ("parcel_id", "pin", "folio", "apn"):
            qt = "parcel"
        elif qt in ("site_address", "street"):
            qt = "address"
        elif qt in ("owner_name", "name"):
            qt = "owner"

        # 1. Primary: Dynamic AI LLM Planning on live page snapshot
        if llm_configured():
            try:
                user_payload = {
                    "query_type": qt,
                    "query_value": query_value,
                    "page": snapshot,
                }
                resp, provider = await chat_completions_create(
                    create_kwargs={
                        "model": getattr(settings, "OPENAI_MODEL", "gpt-6-luna") or "gpt-6-luna",
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT_ASSESSOR},
                            {"role": "user", "content": json.dumps(user_payload)},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                        "response_format": {"type": "json_object"},
                    }
                )
                plan = json.loads(resp.choices[0].message.content or "{}")
                if plan.get("input_selector"):
                    plan["query_type"] = qt
                    logger.info("AI LLM dynamically planned search via %s: %s", provider, plan.get("reasoning"))
                    return plan
            except Exception as llm_err:
                logger.warning("LLM search planning failed: %s. Using heuristic/deterministic planner.", llm_err)

        # 2. Dynamic Heuristic Matching on DOM Elements
        elements = snapshot.get("elements", [])
        tabs = [e for e in elements if e.get("kind") == "tab"]
        radios = [e for e in elements if e.get("kind") == "radio"]
        inputs = [e for e in elements if e.get("kind") == "input"]
        buttons = [e for e in elements if e.get("kind") in ("search_button", "button")]

        target_tab_sel: str | None = None
        target_tab_panel: str | None = None
        target_radio_sel: str | None = None
        target_input_sel: str | None = None
        target_submit_sel: str | None = None

        if qt == "parcel":
            for r in radios:
                lbl = (r.get("label") or "").lower()
                val = (r.get("value") or "").lower()
                if "folio" in lbl or "folio" in val or "parcel" in lbl or "pin" in lbl:
                    target_radio_sel = r.get("selector")
                    break
            for t in tabs:
                text = (t.get("text") or "").lower()
                href = (t.get("href") or "").lower()
                if any(k in text or k in href for k in ["parcel", "folio", "pin", "strap", "apn"]):
                    target_tab_sel = t.get("selector")
                    break
            for inp in inputs:
                ident = f"{inp.get('id', '')} {inp.get('name', '')} {inp.get('placeholder', '')} {inp.get('formcontrolname', '')} {inp.get('dataBind', '')}".lower()
                ph = inp.get("placeholder", "")
                if any(k in ident for k in ["parcel", "folio", "pin", "strap", "apn"]):
                    target_input_sel = inp.get("selector")
                    break
                if re.search(r"ex:\s*[\d\-]+", ph, re.I):
                    target_input_sel = inp.get("selector")
                    break
        elif qt == "address":
            for t in tabs:
                text = (t.get("text") or "").lower()
                href = (t.get("href") or "").lower()
                if any(k in text or k in href for k in ["address", "site", "street", "location"]):
                    target_tab_sel = t.get("selector")
                    break
            for inp in inputs:
                ident = f"{inp.get('id', '')} {inp.get('name', '')} {inp.get('placeholder', '')} {inp.get('formcontrolname', '')} {inp.get('dataBind', '')}".lower()
                ph = inp.get("placeholder", "")
                if any(k in ident for k in ["address", "site", "street", "location"]):
                    target_input_sel = inp.get("selector")
                    break
                if re.search(r"ex:\s*.*(?:armenia|street|ave|blvd|rd|dr|main)", ph, re.I):
                    target_input_sel = inp.get("selector")
                    break
        else:
            for t in tabs:
                text = (t.get("text") or "").lower()
                href = (t.get("href") or "").lower()
                if any(k in text or k in href for k in ["owner", "name"]):
                    target_tab_sel = t.get("selector")
                    break
            for inp in inputs:
                ident = f"{inp.get('id', '')} {inp.get('name', '')} {inp.get('placeholder', '')} {inp.get('formcontrolname', '')} {inp.get('dataBind', '')}".lower()
                ph = inp.get("placeholder", "")
                if any(k in ident for k in ["owner", "name"]):
                    target_input_sel = inp.get("selector")
                    break
                if re.search(r"ex:\s*.*(?:smith|doe|john)", ph, re.I):
                    target_input_sel = inp.get("selector")
                    break

        for btn in buttons:
            text = (btn.get("text") or "").lower()
            btn_id = (btn.get("id") or "").lower()
            sel = (btn.get("selector") or "").lower()
            if any(k in sel for k in ["tabrow", "homepage", "facebook", "twitter", "linkedin", "header", "nav", "logo"]):
                continue
            if any(k in btn_id for k in ["facebook", "twitter", "linkedin", "homepage", "tabrow", "share"]):
                continue
            if "search" in text or "search" in btn_id or "submit" in text:
                target_submit_sel = btn.get("selector")
                break

        if not target_input_sel and inputs:
            target_input_sel = inputs[0].get("selector")

        return {
            "query_type": qt,
            "tab_selector": target_tab_sel,
            "tab_panel": target_tab_panel,
            "radio_selector": target_radio_sel,
            "input_selector": target_input_sel or 'input[type="text"]',
            "submit_selector": target_submit_sel or 'button:has-text("Search"), button[type="submit"]',
            "confidence": "medium",
            "reasoning": f"DOM heuristic matched {qt} input and submit controls.",
        }

    async def execute_plan(
        self,
        page: Page,
        plan: dict[str, Any],
        query_value: str,
        workflow_run_id: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Executes tab selection, field fill, and submit in the visible browser.
        Handles frame-awareness, jQuery UI tab unhiding, Angular Material tabs,
        and logs live session activity.
        """
        self._last_plan = plan
        tab_sel = plan.get("tab_selector")
        tab_panel = plan.get("tab_panel")
        radio_sel = plan.get("radio_selector")
        input_sel = plan.get("input_selector")
        submit_sel = plan.get("submit_selector")

        logger.info(
            "Executing AI Page Search Plan: tab=%s, radio=%s, input=%s, submit=%s for query=%s",
            tab_sel, radio_sel, input_sel, submit_sel, query_value
        )

        bm = None
        try:
            from app.browser.browser_manager import get_browser_manager
            bm = await get_browser_manager()
        except Exception:
            pass

        async def _log_act(action: str, description: str, step_idx: int, status: str = "COMPLETED", err: str | None = None):
            if bm and workflow_run_id:
                try:
                    await bm.log_session_activity(
                        action=action,
                        description=description,
                        node_id=node_id,
                        step_index=step_idx,
                        status=status,
                        url=page.url if (page and not page.is_closed()) else None,
                        error=err,
                    )
                except Exception as log_err:
                    logger.debug("Failed to log activity: %s", log_err)

        # 0. Initial page navigation milestone
        await _log_act("navigate", f"Connected to portal: {page.url}", 0)

        # 0.5. If landed on portal homepage with 'Property Search' button, click it
        for f in page.frames:
            try:
                search_nav = f.locator("a:has-text('Property Search'), button:has-text('Property Search')").first
                if await search_nav.is_visible(timeout=800):
                    logger.info("Found Property Search portal link, clicking...")
                    await _log_act("click", "Click 'Property Search' portal link", 1)
                    await search_nav.click()
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        # 1. Dismiss portal disclaimer / modal dynamically with AI
        modal_info: dict[str, Any] = {}
        try:
            from app.browser.modal_detector import detect_and_dismiss_modal_ai
            modal_info = await detect_and_dismiss_modal_ai(page, workflow_run_id=workflow_run_id, node_id=node_id)
            if modal_info.get("has_disclaimer"):
                self._last_disclaimer_action = modal_info
                await _log_act(
                    "accept_terms",
                    modal_info.get("description") or f"Dismissed '{modal_info.get('dialog_title')}' modal",
                    2,
                )
        except Exception as modal_err:
            logger.warning("Dynamic modal dismissal error: %s", modal_err)

        # Check if the target input field is already visible and interactable
        input_already_visible = False
        if input_sel:
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                for s in [input_sel, *input_sel.split(",")]:
                    s_clean = s.strip()
                    if not s_clean:
                        continue
                    try:
                        inp_el = target_scope.locator(s_clean).first
                        if await inp_el.count() > 0 and await inp_el.is_visible(timeout=600):
                            input_already_visible = True
                            break
                    except Exception:
                        continue
                if input_already_visible:
                    break

        # 2. Select and Click Tab (only if input is not already visible)
        if tab_sel and not input_already_visible:
            clicked_tab = False
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    # Activate jQuery UI tabs or unhide panel in DOM if panel ID known
                    if tab_panel:
                        await target_scope.evaluate(
                            f"""() => {{
                                const targetId = "{tab_panel}";
                                const panels = ['TabOwner', 'TabAddress', 'TabParcel', 'TabSubCondo', 'TabRadius', 'TabAdvanced'];
                                panels.forEach(id => {{
                                    const el = document.getElementById(id);
                                    if (el) {{
                                        el.style.display = (id === targetId) ? '' : 'none';
                                        el.setAttribute('aria-hidden', (id === targetId) ? 'false' : 'true');
                                    }}
                                }});
                                const activeTabLink = document.querySelector('a[href="#' + targetId + '"]');
                                if (activeTabLink && activeTabLink.parentElement) {{
                                    const ul = activeTabLink.closest('ul');
                                    if (ul) {{
                                        ul.querySelectorAll('li').forEach(li => {{
                                            li.classList.remove('ui-tabs-active', 'ui-state-active');
                                            li.setAttribute('aria-selected', 'false');
                                            li.setAttribute('aria-expanded', 'false');
                                        }});
                                        activeTabLink.parentElement.classList.add('ui-tabs-active', 'ui-state-active');
                                        activeTabLink.parentElement.setAttribute('aria-selected', 'true');
                                        activeTabLink.parentElement.setAttribute('aria-expanded', 'true');
                                    }}
                                }}
                                if (window.jQuery && window.jQuery.fn.tabs && window.$tabs) {{
                                    const idx = panels.indexOf(targetId);
                                    if (idx >= 0) window.$tabs.tabs("option", "active", idx);
                                }}
                            }}"""
                        )

                    # Try tab selectors
                    for s in [tab_sel, *tab_sel.split(",")]:
                        s_clean = s.strip()
                        if not s_clean:
                            continue
                        tab_el = target_scope.locator(s_clean).first
                        if await tab_el.count() > 0 and await tab_el.is_visible(timeout=1500):
                            await tab_el.click(force=True, timeout=3000)
                            clicked_tab = True
                            await _log_act("click_tab", f"Selected {plan.get('query_type', 'search').capitalize()} Search Tab ({s_clean})", 3)
                            await page.wait_for_timeout(1000)
                            break
                    if clicked_tab:
                        break
                except Exception:
                    continue

            if not clicked_tab:
                logger.debug("Could not click tab %s, continuing with direct input fill.", tab_sel)

        # 2.5. Select Radio Button if specified by plan
        if radio_sel:
            clicked_radio = False
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    for s in [radio_sel, *radio_sel.split(",")]:
                        s_clean = s.strip()
                        if not s_clean:
                            continue
                        r_el = target_scope.locator(s_clean).first
                        if await r_el.count() > 0 and await r_el.is_visible(timeout=1200):
                            await r_el.click(force=True, timeout=2500)
                            clicked_radio = True
                            await _log_act("select_option", f"Selected search option ({s_clean})", 3)
                            await page.wait_for_timeout(600)
                            break
                    if clicked_radio:
                        break
                except Exception:
                    continue

        # 3. Locate and Fill Input Field
        filled_input = False
        if input_sel:
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                for s in [input_sel, *input_sel.split(",")]:
                    s_clean = s.strip()
                    if not s_clean:
                        continue
                    try:
                        inp_el = target_scope.locator(s_clean).first
                        if await inp_el.count() > 0 and await inp_el.is_visible(timeout=1500):
                            # Set via evaluate to dispatch events cleanly across Knockout and reactive frameworks
                            await inp_el.evaluate(
                                """(el, val) => {
                                    el.value = val;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                                    if (window.jQuery) { window.jQuery(el).trigger('input').trigger('change').trigger('blur'); }
                                    if (window.ko) {
                                        try {
                                            const ctx = window.ko.contextFor(el);
                                            if (ctx && ctx.$data) {
                                                for (let k of Object.keys(ctx.$data)) {
                                                    if (typeof ctx.$data[k] === 'function' && ctx.$data[k].length <= 1) {
                                                        const b = el.getAttribute('data-bind') || '';
                                                        if (b.includes(k)) ctx.$data[k](val);
                                                    }
                                                }
                                            }
                                        } catch(e) {}
                                    }
                                }""",
                                query_value,
                            )
                            try:
                                await inp_el.click(force=True, timeout=1000)
                                await inp_el.fill(query_value, timeout=2000)
                            except Exception:
                                pass

                            filled_input = True
                            await _log_act("fill", f"Entered {plan.get('query_type', 'query').capitalize()}: {query_value}", 4)
                            await page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue
                if filled_input:
                    break

        if not filled_input:
            logger.info("Direct input %s not found, attempting generic search field matching for: %s", input_sel, query_value)
            fallback_selectors = [
                'input[data-bind*="folio"]:visible', 'input[data-bind*="parcel"]:visible',
                'input[placeholder*="Enter Folio" i]', 'input[placeholder*="folio" i]', 'input[formcontrolname*="folio" i]',
                'input[placeholder*="Enter Parcel" i]', 'input[placeholder*="parcel" i]', 'input[formcontrolname*="parcel" i]', '#ParcelID',
                'input[data-bind*="address"]:visible', 'input[placeholder*="Enter Address" i]', 'input[placeholder*="address" i]', 'input[formcontrolname*="address" i]', '#SiteStreetAddress',
                'input[data-bind*="owner"]:visible', 'input[placeholder*="Enter Owner" i]', 'input[placeholder*="owner" i]', 'input[formcontrolname*="owner" i]', '#OwnerLine1',
                'input[type="text"]:visible', 'input[type="search"]:visible'
            ]
            for sel in fallback_selectors:
                for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                    try:
                        inp_el = target_scope.locator(sel).first
                        if await inp_el.count() > 0 and await inp_el.is_visible(timeout=1000):
                            await inp_el.evaluate(
                                """(el, val) => {
                                    el.value = val;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                                    if (window.jQuery) { window.jQuery(el).trigger('input').trigger('change').trigger('blur'); }
                                    if (window.ko) {
                                        try {
                                            const ctx = window.ko.contextFor(el);
                                            if (ctx && ctx.$data) {
                                                for (let k of Object.keys(ctx.$data)) {
                                                    if (typeof ctx.$data[k] === 'function' && ctx.$data[k].length <= 1) {
                                                        const b = el.getAttribute('data-bind') || '';
                                                        if (b.includes(k)) ctx.$data[k](val);
                                                    }
                                                }
                                            }
                                        } catch(e) {}
                                    }
                                }""",
                                query_value,
                            )
                            try:
                                await inp_el.fill(query_value, timeout=2000)
                            except Exception:
                                pass
                            filled_input = True
                            await _log_act("fill", f"Entered {plan.get('query_type', 'query').capitalize()} using fallback field: {query_value}", 4)
                            logger.info("Filled search input using fallback selector %s", sel)
                            await page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue
                if filled_input:
                    break

        # 3.5. Dynamic Autocomplete / Suggestion Dropdown Handling
        try:
            from app.browser.autocomplete_handler import detect_and_select_autocomplete
            ac_res = await detect_and_select_autocomplete(
                page,
                expected_value=query_value,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            if ac_res.get("detected"):
                self._last_autocomplete_action = ac_res
                logger.info("Handled autocomplete dropdown selection: %s", ac_res.get("selected_text"))
        except Exception as ac_err:
            logger.debug("Autocomplete handler note: %s", ac_err)
        except Exception as ac_err:
            logger.debug("Autocomplete handler note: %s", ac_err)

        # 4. Click Submit Button
        submitted = False
        if submit_sel:
            sel_lower = submit_sel.lower()
            if any(k in sel_lower for k in ["facebook", "twitter", "linkedin", "tabrow", "homepage", "shareicon"]):
                logger.warning("Rejecting unsafe submit selector %s (points to social or container)", submit_sel)
                submit_sel = None

        if submit_sel:
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                for s in [submit_sel, *submit_sel.split(",")]:
                    s_clean = s.strip()
                    if not s_clean:
                        continue
                    try:
                        btn_el = target_scope.locator(s_clean).first
                        if await btn_el.count() > 0 and await btn_el.is_visible(timeout=1500):
                            is_safe = await btn_el.evaluate(
                                """(el) => {
                                    const tag = el.tagName.toUpperCase();
                                    if (['DIV', 'NAV', 'HEADER', 'FOOTER', 'BODY', 'FORM'].includes(tag)) return false;
                                    const href = (el.getAttribute('href') || '').toLowerCase();
                                    if (href.includes('facebook.com') || href.includes('twitter.com') || href.includes('x.com') || href.includes('linkedin.com') || href.includes('instagram.com')) return false;
                                    const id = (el.id || '').toLowerCase();
                                    if (id.includes('facebook') || id.includes('twitter') || id.includes('tabrow') || id.includes('homepage')) return false;
                                    return true;
                                }"""
                            )
                            if not is_safe:
                                logger.warning("Target element for %s failed safety check; skipping click.", s_clean)
                                continue
                            await btn_el.click(force=True, timeout=3000)
                            submitted = True
                            await _log_act("click", f"Clicked Submit Search ({s_clean})", 5)
                            await page.wait_for_timeout(2500)
                            break
                    except Exception:
                        continue
                if submitted:
                    break

        if not submitted:
            button_selectors = [
                '[data-test="search-button"]',
                '[data-test*="search" i]',
                'button[aria-label*="search" i]',
                '[aria-label*="Submit Search" i]',
                '[aria-label="Search button"]',
                'button.search-icon',
                'button:has(.k-i-search)',
                'button:has-text("SEARCH")',
                'button:has-text("Search")',
                'button[type="submit"]',
                'input[type="submit"]',
                'button.btn-primary',
                'button:has(.fa-search)',
                'a:has-text("Search")',
                '#btnSearch',
            ]
            for btn_sel in button_selectors:
                for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                    try:
                        btn_el = target_scope.locator(btn_sel).first
                        if await btn_el.count() > 0 and await btn_el.is_visible(timeout=1000):
                            await btn_el.click(force=True, timeout=3000)
                            submitted = True
                            await _log_act("click", f"Clicked Search button ({btn_sel})", 5)
                            logger.info("Clicked submit button using fallback selector %s", btn_sel)
                            await page.wait_for_timeout(2500)
                            break
                    except Exception:
                        continue
                if submitted:
                    break

        if not submitted:
            try:
                await page.keyboard.press("Enter")
                await _log_act("submit", "Pressed Enter key to submit search", 5)
                await page.wait_for_timeout(2500)
            except Exception:
                pass

        # Dynamically wait for results, navigation, and child iframes (especially on tax portals)
        try:
            from app.extraction.tax_collector_reports import is_tax_collector_portal, wait_for_tax_details_loaded
            if is_tax_collector_portal(page.url) or any("tax" in (f.url or "").lower() for f in page.frames):
                logger.info("Tax collector portal detected (%s). Waiting dynamically for tax frame and bills to render...", page.url)
                await _log_act("wait_load", "Waiting for Tax Collector portal and bills to render...", 5)
                await wait_for_tax_details_loaded(page, timeout_seconds=15)
        except Exception as tax_wait_err:
            logger.debug("Tax wait note: %s", tax_wait_err)

        # 5. Check if search returned a list/table and click parcel number / detail link dynamically via AI
        detail_opened = False
        curr_url = page.url or ""
        clean_target_digits = re.sub(r"\D", "", query_value)

        try:
            from app.browser.result_selector_ai import detect_and_click_result_link_ai
            res_click_info = await detect_and_click_result_link_ai(
                page,
                query_value=query_value,
                query_type=plan.get("query_type", "parcel"),
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            if res_click_info.get("is_results_page") and res_click_info.get("clicked"):
                self._last_result_click_action = res_click_info
                detail_opened = True
                await _log_act(
                    "view_record",
                    res_click_info.get("description") or f"Clicked parcel number {query_value} to open property details",
                    6,
                )
        except Exception as res_err:
            logger.warning("Dynamic AI result link detection error: %s", res_err)

        # Dynamic multi-county search results table/cell handling (td.link, Knockout goToPin, links)
        if not detail_opened:
            query_val_space = query_value.replace("-", " ")
            query_digits = re.sub(r"\D", "", query_value)
            generic_selectors = [
                f'a:has-text("{query_value}")',
                f'a:has-text("{query_val_space}")',
                f'td.link:has-text("{query_value}")',
                f'td.link:has-text("{query_val_space}")',
                f'td.link:has-text("{query_digits}")',
                f'td[data-bind*="goTo"]:has-text("{query_value}")',
                f'td[data-bind*="goTo"]:has-text("{query_val_space}")',
                f'td[data-bind*="goTo"]:has-text("{query_digits}")',
                'td[data-bind*="goToPin"]',
                'td[data-bind*="goTo"]',
                'table tbody tr td.link',
                'table tbody tr td[class*="link"]',
                'table tbody tr:first-child td.link',
                'table a[href*="#/"]',
                'a[href*="PropertySearch"]',
                'a[href*="RecordDetail"]', 'a[href*="FolioID"]', 'a[href*="ParcelDetail"]',
                'a[href*="parceldetail"]', 'table tbody tr td.pointer',
                'table tbody tr:first-child td:first-child',
            ]
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                for cand_sel in generic_selectors:
                    try:
                        result_link = target_scope.locator(cand_sel).first
                        if await result_link.count() > 0 and await result_link.is_visible(timeout=1500):
                            cell_text = (await result_link.inner_text()).strip()
                            logger.info("Found result row/detail link (%s: '%s'), clicking to open record details...", cand_sel, cell_text)
                            await _log_act("view_record", f"Opened matching property record detail ({cell_text or query_value})", 6)
                            self._last_result_click_action = {
                                "is_results_page": True,
                                "clicked": True,
                                "selector": cand_sel,
                                "description": f"Opened matching property record detail ({cell_text or query_value})",
                            }
                            try:
                                await result_link.click(force=True, timeout=3000)
                            except Exception:
                                await result_link.evaluate("el => el.click()")
                            detail_opened = True
                            await page.wait_for_timeout(3500)
                            break
                    except Exception:
                        continue
                if detail_opened:
                    break

        # 6. Extract Property Details across Sections (Dynamic Universal Extraction)
        final_record: dict[str, Any] = {}
        downloaded_docs: list[dict[str, Any]] = []

        # 6A. Dynamic Full-Page Universal DOM Extraction
        try:
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                dyn_data = await target_scope.evaluate(DYNAMIC_FULL_PAGE_EXTRACTION_JS)
                if dyn_data and isinstance(dyn_data, dict):
                    dyn_rec = parse_dynamic_full_page_record(dyn_data, target_scope.url, query_value)
                    if (
                        (dyn_rec.get("owner_name") and dyn_rec["owner_name"] != "OWNER OF RECORD")
                        or dyn_rec.get("property_address")
                        or dyn_rec.get("legal_description")
                    ):
                        for k, v in dyn_rec.items():
                            if v is not None and (k not in final_record or not final_record[k] or final_record[k] == "OWNER OF RECORD"):
                                final_record[k] = v
                        break
        except Exception as dyn_err:
            logger.warning("Dynamic full-page extraction error: %s", dyn_err)

        # 6B. Mistral Document AI OCR Webpage Extraction (Full-page visual grounding)
        try:
            from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
            ocr_srv = get_mistral_webpage_ocr()
            if ocr_srv.is_enabled():
                logger.info("Executing Mistral Document AI OCR on full-page screenshot for 100%% accuracy...")
                ocr_rec = await ocr_srv.extract_from_page(page, query_value=query_value)
                if ocr_rec:
                    logger.info(
                        "Mistral OCR extracted property data: Owner=%s, APN=%s, Addr=%s, Use=%s",
                        ocr_rec.get("owner_name"),
                        ocr_rec.get("parcel_number"),
                        ocr_rec.get("property_address"),
                        ocr_rec.get("property_use"),
                    )
                    for k in [
                        "parcel_number", "owner_name", "property_address", "mailing_address",
                        "legal_description", "property_use", "taxing_district", "land_size",
                        "exemptions", "map_number", "just_value", "assessed_value", "taxable_value",
                        "subdivision", "sales_history", "valuation_history"
                    ]:
                        val = ocr_rec.get(k)
                        if val is not None and (
                            k not in final_record
                            or not final_record[k]
                            or final_record[k] == "OWNER OF RECORD"
                        ):
                            final_record[k] = val
                    if ocr_rec.get("raw_markdown"):
                        final_record["ocr_markdown"] = ocr_rec["raw_markdown"]
                    if ocr_rec.get("all_key_values"):
                        final_record["ocr_key_values"] = ocr_rec["all_key_values"]
        except Exception as ocr_err:
            logger.warning("Mistral OCR webpage extraction note: %s", ocr_err)

        # 6C. Dynamic Official Assessor Report Download for Any Portal
        try:
            from app.extraction.assessor_reports import download_universal_assessor_reports
            target_p = final_record.get("parcel_number") or query_value
            reports = await download_universal_assessor_reports(page, str(target_p))
            if reports:
                downloaded_docs.extend(reports)
        except Exception as r_err:
            logger.debug("Dynamic assessor report download note: %s", r_err)

        # 6D. General Florida & Fallback Extraction
        if not final_record.get("owner_name") or final_record.get("owner_name") == "OWNER OF RECORD":
            try:
                for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                    fl_data = await target_scope.evaluate(FLORIDA_SCRAPE_JS)
                    if fl_data and isinstance(fl_data, dict) and any(fl_data.values()):
                        fl_rec = parcel_record_from_florida_data(fl_data, target_scope.url)
                        if fl_rec.owner_name or fl_rec.apn:
                            fallback_dict = {
                                "parcel_number": fl_rec.apn or query_value,
                                "owner_name": fl_rec.owner_name or "OWNER OF RECORD",
                                "property_address": fl_rec.property_address or "",
                                "assessed_value": fl_rec.assessed_value,
                                "just_value": fl_rec.assessed_value,
                                "legal_description": fl_rec.legal_description or "",
                                "subdivision": fl_rec.raw_json.get("subdivision"),
                                "use_code": fl_rec.raw_json.get("use_code"),
                                "sales_history": fl_rec.raw_json.get("sales_history") or [],
                                "raw_data": fl_rec.raw_json,
                            }
                            for k, v in fallback_dict.items():
                                if v is not None and (k not in final_record or not final_record[k]):
                                    final_record[k] = v
                            break
            except Exception:
                pass

        # 6E. Tax Collector Portal Dynamic Extraction & Reports
        try:
            from app.extraction.tax_collector_reports import (
                is_tax_collector_portal,
                extract_tax_collector_record,
                capture_tax_collector_screenshot,
                download_official_tax_bill,
            )
            if is_tax_collector_portal(page.url) or any("tax" in (f.url or "").lower() for f in page.frames):
                logger.info("Extracting structured tax data from tax collector portal...")
                tax_rec = await extract_tax_collector_record(page, query_value=query_value)
                if tax_rec.get("owner_name") or tax_rec.get("total_amount_due") is not None or tax_rec.get("account_number"):
                    for k, v in tax_rec.items():
                        if v is not None and (k not in final_record or not final_record[k] or final_record[k] == "OWNER OF RECORD"):
                            final_record[k] = v

                # Download official tax bill PDF
                bill_doc = await download_official_tax_bill(page, final_record.get("parcel_number") or query_value)
                if bill_doc:
                    downloaded_docs.append(bill_doc)

                # Capture full-page screenshot
                shot_doc = await capture_tax_collector_screenshot(page, final_record.get("parcel_number") or query_value)
                if shot_doc:
                    downloaded_docs.append(shot_doc)
        except Exception as tax_ex_err:
            logger.warning("Tax collector extraction note: %s", tax_ex_err)

        if not final_record.get("parcel_number"):
            final_record["parcel_number"] = query_value
        if not final_record.get("owner_name"):
            final_record["owner_name"] = "OWNER OF RECORD"

        # Register official downloaded documents into PostgreSQL
        registered_meta = []
        if downloaded_docs:
            try:
                from app.documents.document_service import DocumentService
                doc_srv = DocumentService(db=self.db)
                registered_meta = await register_downloaded_documents(
                    doc_service=doc_srv,
                    downloaded_docs=downloaded_docs,
                    workflow_run_id=uuid.UUID(str(workflow_run_id)) if workflow_run_id else None,
                )
            except Exception as reg_err:
                logger.debug("Document registration note: %s", reg_err)

        final_record["_documents"] = registered_meta

        # Log extraction milestone
        await _log_act(
            "extract",
            f"Extracted Property: {final_record['owner_name']} | APN: {final_record['parcel_number']}",
            7,
        )

        return {
            "success": filled_input,
            "plan": plan,
            "record": final_record,
            "detail_opened": detail_opened,
            "downloaded_documents": registered_meta,
        }

    async def save_discovered_recipe(
        self,
        county_source_id: uuid.UUID,
        record_type: str,
        county_name: str,
        portal_url: str,
        dom_snapshot: dict[str, Any],
        user_id: uuid.UUID | None = None,
        plan: dict[str, Any] | None = None,
    ) -> tuple[Recipe, RecipeVersion]:
        """
        Compiles discovered tabs and interactive elements into structured declarative steps,
        persisting and publishing the RecipeVersion in PostgreSQL.
        """
        if not self.db:
            raise RuntimeError("Database session is required to save recipe.")

        from app.services.ai_page_analyzer import AIPageAnalyzerService

        executed_plan = plan or getattr(self, "_last_plan", None)
        autocomplete_act = getattr(self, "_last_autocomplete_action", None)
        result_click_act = getattr(self, "_last_result_click_action", None)
        disclaimer_act = getattr(self, "_last_disclaimer_action", None)

        if disclaimer_act:
            dom_snapshot["disclaimer_action"] = disclaimer_act
            dom_snapshot["has_disclaimer"] = True

        if result_click_act:
            dom_snapshot["result_click_action"] = result_click_act

        if autocomplete_act:
            dom_snapshot["autocomplete_action"] = autocomplete_act

        analyzer = AIPageAnalyzerService(self.db)
        recipe_def = analyzer._synthesize_recipe_definition(
            dom=dom_snapshot,
            portal_url=portal_url,
            county_name=county_name,
            record_type=record_type,
            executed_plan=executed_plan,
            autocomplete_action=autocomplete_act,
            result_click_action=result_click_act,
        )

        res = await analyzer._save_recipe_to_db(
            county_source_id=county_source_id,
            record_type=record_type,
            county_name=county_name,
            recipe_def=recipe_def,
            dom_analysis=dom_snapshot,
            target_url=portal_url,
            user_id=user_id,
        )

        rec_stmt = select(Recipe).where(Recipe.id == res["recipe_id"])
        ver_stmt = select(RecipeVersion).where(RecipeVersion.id == res["recipe_version_id"])
        r = (await self.db.execute(rec_stmt)).scalar_one()
        v = (await self.db.execute(ver_stmt)).scalar_one()
        return r, v
