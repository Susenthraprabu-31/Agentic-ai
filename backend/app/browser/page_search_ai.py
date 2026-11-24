"""
AI-driven county portal analyzer and dynamic search executor.
Analyzes loaded county search portals, discovers tabs/inputs/buttons,
clicks the exact tab based on input type (parcel_id, address, owner_name),
fills fields, submits search, extracts records, and persists discovered recipes to PostgreSQL.
Includes full Florida and multi-county heuristics, AI LLM planning, and document generation.
"""
from __future__ import annotations

import asyncio
import base64
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
    if (id.includes('goog-gt') || id.includes('google_translate') || id.startsWith('gt-')) {
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

  const elRole = (el.getAttribute('role') || '').toLowerCase();
  // Never return container tags as buttons or inputs (except role=button search divs)
  if (CONTAINER_TAGS.has(tag) && elRole !== 'tab' && elRole !== 'button' && (el.id || '').toLowerCase() !== 'search') {
    return null;
  }

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
      if (el.id && !/^\d/.test(el.id)) {
        const idLower = el.id.toLowerCase();
        if (!idLower.includes('tabrow') && !idLower.includes('homepage') && !idLower.includes('logo')) {
          return `#${CSS.escape(el.id)}`;
        }
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

    if ((tag === 'BUTTON' || tag === 'A' || (tag === 'DIV' && elRole === 'button')) && text) {
      const lower = text.toLowerCase();
      if (lower.length <= 40 && /search|address|owner|folio|parcel|submit|go|find/i.test(lower)) {
        if (el.id && !/^\d/.test(el.id)) return `#${CSS.escape(el.id)}`;
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
      try {
        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (l) label = (l.innerText || l.textContent || '').replace(/\s+/g, ' ').trim();
      } catch (_) {}
    }
    if (!label) {
      const row = el.closest('tr');
      if (row) {
        const cells = Array.from(row.querySelectorAll('td, th'));
        const cell = el.closest('td, th');
        if (cell) {
          const idx = cells.indexOf(cell);
          if (idx > 0) {
            const prevText = (cells[idx - 1].innerText || cells[idx - 1].textContent || '')
              .replace(/\s+/g, ' ').trim();
            if (prevText && prevText.length < 120) label = prevText;
          }
        }
      }
    }
    if (!label) {
      const aria = el.getAttribute('aria-label');
      if (aria) label = aria.trim();
    }
    if (!label && el.parentElement) {
      const pt = (el.parentElement.innerText || el.parentElement.textContent || '').replace(/\s+/g, ' ').trim();
      if (pt && pt.length < 80) label = pt;
    }

    let layoutTop = 0;
    let layoutLeft = 0;
    try {
      const rect = el.getBoundingClientRect();
      layoutTop = Math.round(rect.top);
      layoutLeft = Math.round(rect.left);
    } catch (_) {}

    let panelId = '';
    let sectionLabel = '';
    try {
      const pane =
        el.closest('[id*="BodyPane"]') ||
        el.closest('[id*="ctlBodyPane"]') ||
        el.closest('fieldset') ||
        el.closest('table');
      if (pane && pane.id) panelId = pane.id;
      const labelRoot = pane || el.parentElement;
      if (labelRoot) {
        const chunk = (labelRoot.innerText || labelRoot.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 420);
        const m = chunk.match(/Search by[^.(\n]{0,90}/i);
        if (m) sectionLabel = m[0].trim();
      }
    } catch (_) {}

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
      layout_top: layoutTop,
      layout_left: layoutLeft,
      panel_id: panelId,
      section_label: sectionLabel,
    });
  }

  document.querySelectorAll('[role="tab"], a[href^="#Tab"], button, a, input, textarea, select, [aria-label], [role="button"], #search, div.button').forEach((el) => {
    try {
      if (isExcluded(el)) return;
      const tag = el.tagName.toUpperCase();
      const role = el.getAttribute('role') || '';
      const href = el.getAttribute('href') || '';
      const text = (el.innerText || el.textContent || '').trim();
      const aria = el.getAttribute('aria-label') || '';
      const lowerText = text.toLowerCase();
      const lowerAria = aria.toLowerCase();

      // Skip non-tab container elements (allow role=button submit divs)
      if (CONTAINER_TAGS.has(tag) && role !== 'tab' && role !== 'button' && (el.id || '').toLowerCase() !== 'search') return;

      let kind = 'control';
      if (role === 'tab' || href.startsWith('#Tab') || /ui-tabs-anchor/i.test(el.className || '')) {
        kind = 'tab';
      } else if (tag === 'INPUT' && (el.type || '').toLowerCase() === 'radio') {
        kind = 'radio';
      } else if (tag === 'SELECT') {
        kind = 'select';
      } else if (tag === 'INPUT' || tag === 'TEXTAREA') {
        kind = 'input';
      } else if (tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button'].includes((el.type || '').toLowerCase()))) {
        if (/^(owner name|parcel id|address|sales|real estate|tangible)$/i.test(lowerText.trim())
            || /^(parcel|folio|pin|strap|apn)(\s*(id|#|number))?$/i.test(lowerText.trim())) {
          kind = 'tab';
        } else if (/search|find|lookup|submit|go/i.test(lowerText) || /search|find|lookup|submit|go/i.test(lowerAria) || /search|find|submit/i.test(el.id || '')) {
          kind = 'search_button';
        } else {
          kind = 'button';
        }
      } else if (tag === 'A') {
        // Section toggles (e.g. "Search by Parcel Number")
        if (/btnheadertoggle|headertoggle/i.test(el.id || '')) {
          kind = 'tab';
        } else if (/search by|toggle/i.test(lowerText) || /toggle/i.test(el.className || '')) {
          kind = 'tab';
        } else if (/btnsearch|btn-search/i.test(el.id || '')) {
          kind = 'search_button';
        } else if (/^search$/i.test(lowerText.trim()) && (href.startsWith('#') || href.toLowerCase().startsWith('javascript'))) {
          kind = 'search_button';
        } else {
          // Header/menu links such as "Parcel Search" are navigation — not form submit
          kind = 'button';
        }
      } else if (
        role === 'button' ||
        ((tag === 'DIV' || tag === 'SPAN') && (
          /^search$/i.test(lowerText.trim()) ||
          (el.id || '').toLowerCase() === 'search' ||
          /submitform/i.test(el.getAttribute('onclick') || '')
        ))
      ) {
        if (/search|find|lookup|submit/i.test(lowerText) || (el.id || '').toLowerCase() === 'search') {
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
    elements: out.slice(0, 200),
  };
}
"""

FILL_INPUT_JS = """(el, val) => {
    el.focus();
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('keyup', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    if (window.jQuery) { window.jQuery(el).trigger('input').trigger('change').trigger('keyup').trigger('blur'); }
    if (el.classList && (el.classList.contains('tt-input') || el.getAttribute('role') === 'combobox')) {
        try { el.dispatchEvent(new Event('keydown', { bubbles: true })); } catch (_) {}
    }
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
}"""

SCHNEIDER_ENSURE_PANEL_OPEN_JS = """(toggleOrInputSel) => {
    const resolve = (sel) => {
        if (!sel) return null;
        const s = String(sel).trim();
        try {
            const el = document.querySelector(s);
            if (el) return el;
        } catch (_) {}
        if (s.startsWith('#')) {
            try { return document.getElementById(s.slice(1)); } catch (_) {}
        }
        return null;
    };
    const paneFromSelector = (sel) => {
        const m = String(sel || '').match(/ctlBodyPane_ctl\\d+/i);
        return m ? document.getElementById(m[0]) : null;
    };
    const isInteractable = (el) => {
        if (!el) return false;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return false;
        if (el.disabled || el.readOnly) return false;
        return true;
    };
    const paneHasVisibleInputs = (pane) => {
        if (!pane) return false;
        const inputs = pane.querySelectorAll(
            'input:not([type="hidden"]):not([type="checkbox"]), textarea, select'
        );
        for (const inp of inputs) {
            if (isInteractable(inp)) return true;
        }
        return false;
    };
    const toggleLooksExpanded = (toggle) => {
        if (!toggle) return null;
        const aria = toggle.getAttribute('aria-expanded');
        if (aria === 'true') return true;
        if (aria === 'false') return false;
        const img = toggle.querySelector('img');
        if (img) {
            const blob = (
                (img.alt || '') + ' ' + (img.title || '') + ' ' + (img.getAttribute('aria-label') || '')
            ).toLowerCase();
            if (/collapse|hide|minus|\\[\\s*-\\s*\\]/.test(blob)) return true;
            if (/expand|show|plus|\\[\\s*\\+\\s*\\]/.test(blob)) return false;
        }
        return null;
    };
    const s = String(toggleOrInputSel || '').trim();
    let toggle = null;
    let pane = null;
    if (/btnheadertoggle/i.test(s)) {
        toggle = resolve(s);
        pane = toggle ? toggle.closest('[id*="ctlBodyPane"]') : paneFromSelector(s);
    } else {
        const inp = resolve(s);
        pane = inp
            ? (inp.closest('[id*="ctlBodyPane"]') || inp.closest('fieldset'))
            : paneFromSelector(s);
        if (pane) {
            toggle = pane.querySelector(
                '[id*="btnHeaderToggle"], [id*="HeaderToggle"], [id*="headerToggle"]'
            );
        }
    }
    if (!pane) return { ready: false, clicked: false, reason: 'no_pane' };
    if (paneHasVisibleInputs(pane)) {
        return { ready: true, clicked: false, state: 'open' };
    }
    if (!toggle) {
        return { ready: false, clicked: false, reason: 'no_toggle' };
    }
    const expandedHint = toggleLooksExpanded(toggle);
    if (expandedHint === true) {
        return { ready: false, clicked: false, reason: 'panel_marked_open', state: 'wait' };
    }
    if (expandedHint === false || expandedHint === null) {
        try {
            toggle.scrollIntoView({ block: 'center' });
            toggle.click();
        } catch (_) {}
        if (paneHasVisibleInputs(pane)) {
            return { ready: true, clicked: true, state: 'opened' };
        }
        return { ready: false, clicked: true, state: 'toggled' };
    }
    return { ready: false, clicked: false, reason: 'unknown' };
}"""

# Legacy name — callers should prefer ensure_search_panel_for_selector() in Python.
EXPAND_PANEL_FOR_INPUT_JS = SCHNEIDER_ENSURE_PANEL_OPEN_JS

SINGLE_FIELD_SEARCH_PREP_JS = """(inputSelector) => {
    const resolve = (sel) => {
        if (!sel) return null;
        const s = String(sel).trim();
        try {
            const el = document.querySelector(s);
            if (el) return el;
        } catch (_) {}
        if (s.startsWith('#')) {
            try { return document.getElementById(s.slice(1)); } catch (_) {}
        }
        return null;
    };
    const inp = resolve(inputSelector);
    if (!inp) return false;
    const root = inp.closest('form') || inp.closest('section') || inp.closest('main') || document.body;
    root.querySelectorAll('input[type="text"], input:not([type]), textarea').forEach((el) => {
        if (el === inp) return;
        try {
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (_) {}
    });
    root.querySelectorAll('input[type="radio"]').forEach((r) => {
        const blob = ((r.id || '') + ' ' + (r.name || '') + ' ' + (r.value || '')).toLowerCase();
        if (/folio|parcel|strap|pin/.test(blob)) {
            try { r.checked = false; } catch (_) {}
        }
    });
    return true;
}"""

PAIR_SUBMIT_CLICK_JS = """(inputSelector) => {
    const resolveInput = (sel) => {
        if (!sel) return null;
        const s = String(sel).trim();
        try {
            const el = document.querySelector(s);
            if (el) return el;
        } catch (_) {}
        if (s.startsWith('#')) {
            try { return document.getElementById(s.slice(1)); } catch (_) {}
        }
        return null;
    };
    const inp = resolveInput(inputSelector);
    if (!inp) return false;
    const roots = [];
    const pushRoot = (el) => { if (el && !roots.includes(el)) roots.push(el); };
    pushRoot(inp.closest('form'));
    pushRoot(inp.closest('[id*="BodyPane"]'));
    pushRoot(inp.closest('[id*="ctlBodyPane"]'));
    pushRoot(inp.closest('fieldset'));
    pushRoot(inp.closest('[class*="tab-pane"]'));
    pushRoot(inp.closest('table'));
    pushRoot(inp.parentElement);
    for (const root of roots) {
        if (!root) continue;
        const btn =
            root.querySelector('a[id*="btnSearch"], button[id*="btnSearch"], input[id*="btnSearch"], a[id*="BtnSearch"], button[id*="BtnSearch"]') ||
            root.querySelector('[id$="btnSearch"], [id$="BtnSearch"]');
        if (btn) {
            try { btn.scrollIntoView({ block: 'center' }); } catch (_) {}
            btn.click();
            return true;
        }
        const candidates = root.querySelectorAll(
            'a, button, input[type="submit"], input[type="button"], [role="button"], div.button, #search'
        );
        for (const el of candidates) {
            const tag = el.tagName.toUpperCase();
            if (tag === 'INPUT' && (el.type || '').toLowerCase() === 'hidden') continue;
            const t = ((el.innerText || el.textContent || el.value || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
            if (t === 'go' || t === 'go!') continue;
            if (t === 'search' || (el.id || '').toLowerCase() === 'search' || (t.includes('search') && t.length < 20)) {
                try { el.scrollIntoView({ block: 'center' }); } catch (_) {}
                el.click();
                if (typeof submitForm === 'function') {
                    try { submitForm(0); } catch (_) {}
                }
                return true;
            }
        }
    }
    const pageSearch = document.querySelector('#search[role="button"], div#search.button, div#search');
    if (pageSearch) {
        try { pageSearch.scrollIntoView({ block: 'center' }); } catch (_) {}
        pageSearch.click();
        if (typeof submitForm === 'function') {
            try { submitForm(0); } catch (_) {}
        }
        return true;
    }
    if (typeof submitForm === 'function') {
        try { submitForm(0); return true; } catch (_) {}
    }
    return false;
}"""

INVOKE_PORTAL_SUBMIT_JS = """() => {
    const pick = document.querySelector(
        '#search[role="button"], div#search.button, div#search, [role="button"].button'
    );
    if (pick) {
        try { pick.scrollIntoView({ block: 'center' }); } catch (_) {}
        pick.click();
        if (typeof submitForm === 'function') {
            try { submitForm(0); return true; } catch (_) {}
        }
        return true;
    }
    if (typeof submitForm === 'function') {
        try { submitForm(0); return true; } catch (_) {}
    }
    return false;
}"""


def _norm_blob(*parts: str) -> str:
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).lower().strip()


def _element_blob(el: dict[str, Any]) -> str:
    return _norm_blob(
        el.get("label", ""),
        el.get("placeholder", ""),
        el.get("name", ""),
        el.get("id", ""),
        el.get("text", ""),
        el.get("formcontrolname", ""),
        el.get("dataBind", ""),
    )


def _snapshot_is_tax_collector_portal(snapshot: dict[str, Any]) -> bool:
    from app.extraction.tax_collector_reports import is_tax_collector_portal

    return is_tax_collector_portal(
        snapshot.get("url", "") or "",
        snapshot.get("title", "") or "",
        (snapshot.get("html_sample") or "")[:800],
    )


def _is_unified_tax_search_input(el: dict[str, Any]) -> bool:
    """County tax portals often use one box for name, address, account, and folio."""
    if el.get("kind") != "input":
        return False
    blob = _element_blob(el)
    if "name, address" in blob:
        return True
    return (
        "address" in blob
        and "account" in blob
        and ("folio" in blob or "account number" in blob)
    )


def _is_combined_criterion_search_input(el: dict[str, Any]) -> bool:
    """Single hero/search box accepting owner, address, and parcel/PCN (assessor home pages)."""
    if el.get("kind") != "input":
        return False
    if _is_unified_tax_search_input(el):
        return True
    blob = _element_blob(el).lower()
    has_address = "address" in blob
    has_owner = "owner" in blob or "last name" in blob or "name, first" in blob
    has_parcel = any(k in blob for k in ("pcn", "parcel", "folio", "parcel control", "property number"))
    if "search by" in blob and has_address and (has_owner or has_parcel):
        return True
    return has_address and has_owner and has_parcel


def _element_from_plan_selector(elements: list[dict[str, Any]], selector: str) -> dict[str, Any] | None:
    sel = (selector or "").strip()
    if not sel:
        return None
    sid = sel.lstrip("#").replace("\\:", ":")
    for e in elements:
        if (e.get("selector") or "").strip() == sel:
            return e
        if (e.get("id") or "") == sid:
            return e
    return None


def _collect_submit_candidates_from_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Include input[type=submit|button] Search controls — not only snapshot search_button entries."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in elements:
        kind = e.get("kind")
        if kind in ("search_button", "button"):
            sel = (e.get("selector") or "").strip()
            eid = (e.get("id") or "").lower()
            if any(k in eid for k in ("goog-gt", "google_translate", "gt-thumb")):
                continue
            if sel and sel not in seen:
                seen.add(sel)
                out.append(e)
        elif kind == "input":
            et = (e.get("type") or "").lower()
            if et not in ("submit", "button"):
                continue
            blob = _element_blob(e).lower()
            eid = (e.get("id") or "").lower()
            val = (e.get("text") or e.get("value") or "").lower()
            if not (
                "search" in blob
                or val == "search"
                or eid.endswith("btnsearch")
                or "btnsearch" in eid
                or eid.endswith("btnreal")
                or "btnreal" in eid
            ):
                continue
            copy = dict(e)
            copy["kind"] = "search_button"
            sel = (copy.get("selector") or "").strip()
            if sel and sel not in seen:
                seen.add(sel)
                out.append(copy)
    return out


def _plan_submit_matches_snapshot(plan: dict[str, Any], elements: list[dict[str, Any]]) -> bool:
    sel = (plan.get("submit_selector") or "").strip()
    if not sel.startswith("#"):
        return False
    hit = _element_from_plan_selector(elements, sel)
    if not hit:
        return False
    eid = (hit.get("id") or "").lower()
    if any(k in eid for k in ("goog-gt", "google_translate", "gt-thumb")):
        return False
    blob = _element_blob(hit).lower()
    if hit.get("kind") == "input" and (hit.get("type") or "").lower() in ("submit", "button"):
        return "search" in blob or "btn" in eid
    return hit.get("kind") == "search_button" or "search" in blob


def _is_spurious_mode_tab_label(text: str) -> bool:
    t = _norm_blob(text).strip().lower()
    if not t:
        return True
    if any(
        p in t
        for p in (
            "address change",
            "change of address",
            "mailing address",
            "map search",
            "advanced sales",
            "more search",
            "homestead",
            "e-file",
            "e file",
            "quick link",
        )
    ):
        return True
    return len(t) > 44


def _is_spurious_mode_tab_selector(selector: str) -> bool:
    low = (selector or "").lower()
    return any(
        p in low
        for p in (
            "address change",
            "mailing",
            "map search",
            "homestead",
            "goog-gt",
            "thumbup",
        )
    )


def _score_unified_tax_search_bonus(el: dict[str, Any]) -> float:
    blob = _element_blob(el)
    bonus = 0.0
    for tok, w in (
        ("name, address", 200),
        ("account number", 150),
        ("folio number", 140),
        ("folio", 110),
        ("account", 85),
    ):
        if tok in blob:
            bonus += w
    ph = (el.get("placeholder") or "").lower()
    if "search" in ph or "name" in ph and "address" in ph:
        bonus += 60
    return bonus


def _score_input_for_query(el: dict[str, Any], qt: str) -> float:
    if el.get("kind") != "input":
        return -999.0
    blob = _element_blob(el)
    score = 0.0
    ident = f"{el.get('id', '')} {el.get('name', '')}".lower()
    if qt == "parcel":
        if re.search(r"\balt\s*key\b|\baltkey\b", blob):
            score -= 280
        if any(k in ident for k in ("txtparcel", "txtfolio", "txtpin", "txtstrap")):
            score += 140
        if any(k in ident for k in ("txtpropid", "txtowner", "txtaddress")):
            score -= 220
        for tok, w in (
            ("parcel number", 130),
            ("parcel id", 125),
            ("parcel #", 120),
            ("folio number", 115),
            ("folio", 95),
            ("parcel", 90),
            ("pin", 80),
            ("apn", 80),
            ("tmk", 75),
            ("strap", 70),
            ("account", 65),
        ):
            if tok in blob:
                score += w
        ph = (el.get("placeholder") or "")
        ph_l = ph.lower()
        if re.search(r"#+\s*-\s*#+", ph) or re.search(r"\d{2}-\d{2}-\d{2}", ph):
            score += 100
        if "3 min" in ph_l or "min)" in ph_l:
            score += 50
        if re.search(r"ex:\s*[\d\-]+", ph, re.I):
            score += 70
        section = _norm_blob(el.get("section_label", ""))
        if "parcel" in section and "alt" not in section:
            score += 80
        if "alt key" in section or "altkey" in section:
            score -= 200
        if re.search(r"\bname\b", blob) and "parcel" not in blob:
            score -= 120
        if "lastname" in blob or "firstname" in blob or "owner" in blob:
            score -= 150
        for bad in (
            "mailing",
            "street name",
            "street #",
            "subdiv",
            "nbhd",
            "tax year",
            "sort",
            "direction",
            "suffix",
            "unit",
            "alt key",
            "altkey",
        ):
            if bad in blob and "parcel" not in blob:
                score -= 80
    elif qt == "address":
        if _is_combined_criterion_search_input(el):
            return 220.0 + _score_unified_tax_search_bonus(el)
        if _is_unified_tax_search_input(el):
            return 200.0 + _score_unified_tax_search_bonus(el)
        if any(k in ident for k in ("txtaddress", "txtmailing", "txtsitus")):
            score += 150
        if any(k in ident for k in ("txtowner", "txtname", "ownername")):
            score -= 280
        for tok, w in (
            ("mailing address", 100),
            ("mail address", 95),
            ("site address", 90),
            ("situs", 85),
            ("location address", 85),
            ("street address", 80),
            ("mailing", 70),
        ):
            if tok in blob:
                score += w
        if "street name" in blob:
            score += 45
        if "address" in blob:
            score += 30
        if "street" in blob and "street #" not in blob:
            score += 20
        ph = (el.get("placeholder") or "").lower()
        lbl = _norm_blob(el.get("label", ""))
        if lbl.startswith("address") or lbl == "address:":
            score += 200
        if lbl.startswith("owner") or "owner name" in lbl:
            score -= 280
        if re.search(r"ex:\s*armenia", ph, re.I):
            score -= 280
        if re.search(r"ex:\s*.+,.+", ph):
            score -= 220
        if re.search(r"ex:\s*.*(?:street|ave|blvd|rd|dr|main|\d)", ph, re.I):
            score += 45
        if "enter address" in ph or "address or range" in ph:
            score += 85
        section = _norm_blob(el.get("section_label", ""))
        if any(k in section for k in ("location address", "site address", "mailing address")):
            score += 110
        if "owner name" in section or "search by owner" in section:
            score -= 200
        if "parcel number" in section and "address" not in section:
            score -= 120
        if re.search(r"\balt\s*key\b|\baltkey\b", blob):
            score -= 250
        for bad in ("parcel", "folio", "owner", "subdiv", "nbhd", "tax year", "sort", "results", "rng", "twp", "sec", " blk", " lot"):
            if bad in blob and "mailing" not in blob and "street name" not in blob and "street #" not in blob:
                score -= 80
        if re.search(r"\bname\b", blob) and "street name" not in blob:
            score -= 100
        if "street #" in blob or "street number" in blob:
            score += 40
    else:
        for tok, w in (("owner name", 95), ("owner", 90)):
            if tok in blob:
                score += w
        if re.search(r"\bname\b", blob) and "street name" not in blob and "user name" not in blob:
            score += 75
        if re.search(r"ex:\s*.*(?:smith|doe|john)", el.get("placeholder", ""), re.I):
            score += 50
        for bad in ("address", "parcel", "altkey", "street", "subdiv", "nbhd"):
            if bad in blob:
                score -= 40
    return score


def _body_pane_prefix(control_id: str) -> str:
    """e.g. ctlBodyPane_ctl04_ctl01_txtAddress -> ctlBodyPane_ctl04."""
    if not control_id:
        return ""
    m = re.match(r"(ctlBodyPane_ctl\d+)", control_id.strip(), re.I)
    return m.group(1) if m else ""


def _section_toggle_selector_for_input(inp: dict[str, Any]) -> str | None:
    """Schneider/qPublic accordion header toggle beside the target input panel."""
    if not inp:
        return None
    prefix = _body_pane_prefix(inp.get("id") or "") or (inp.get("panel_id") or "").strip().lstrip("#")
    if prefix:
        return f"#{prefix}_btnHeaderToggle"
    return None


def _aspnet_control_group(element_id: str) -> str:
    """Shared prefix for Schneider/qPublic txt* and btnSearch controls in one panel."""
    if not element_id:
        return ""
    low = element_id.lower()
    for marker in ("_txt", "_btn", "_inp"):
        idx = low.find(marker)
        if idx > 0:
            return low[:idx]
    return low


def _primary_input_element(plan: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    fills = plan.get("fills") or []
    if fills:
        sel = (fills[0].get("selector") or "").strip()
        if sel:
            hit = next((e for e in elements if e.get("selector") == sel), None)
            if hit:
                return hit
    inp_sel = (plan.get("input_selector") or "").strip()
    if inp_sel:
        return next((e for e in elements if e.get("selector") == inp_sel), None)
    return None


def _score_submit_for_context(
    btn: dict[str, Any],
    primary: dict[str, Any] | None,
    qt: str,
) -> float:
    score = _score_submit_button(btn)
    if not primary:
        return score
    btn_id = _aspnet_control_group(btn.get("id") or "")
    inp_id = _aspnet_control_group(primary.get("id") or "")
    if inp_id and btn_id and inp_id == btn_id:
        score += 320
    p_panel = (primary.get("panel_id") or "").strip()
    b_panel = (btn.get("panel_id") or "").strip()
    if p_panel and b_panel and p_panel == b_panel:
        score += 260
    pt = int(primary.get("layout_top") or 0)
    bt = int(btn.get("layout_top") or 0)
    if pt and bt:
        dist = abs(pt - bt)
        if dist <= 50:
            score += 220
        elif dist <= 140:
            score += 130
        elif dist > 320:
            score -= 180
    section = _norm_blob(btn.get("section_label") or primary.get("section_label") or "")
    if qt == "address":
        if any(k in section for k in ("location address", "site address", "situs", "mailing address")):
            score += 140
        if any(k in section for k in ("owner", "parcel number", "alternate id", "sales")):
            score -= 220
    elif qt == "owner":
        if "owner" in section:
            score += 140
        if "address" in section and "owner" not in section:
            score -= 120
    elif qt == "parcel":
        if "parcel" in section:
            score += 120
        if "owner" in section or ("address" in section and "parcel" not in section):
            score -= 100
    if (btn.get("id") or "").lower().endswith("btnsearch"):
        score += 40
    text = _norm_blob(btn.get("text", ""))
    if text in ("go", "go!"):
        score -= 220
        if pt and bt and abs(pt - bt) > 180:
            score -= 120
    sel = (btn.get("selector") or "")
    if ":has-text" in sel and not (btn.get("id") or "").strip():
        score -= 80
    if "has-text(\"go\")" in sel.lower() or "has-text('go')" in sel.lower():
        score -= 300
    return score


def _pick_submit_for_search_context(
    buttons: list[dict[str, Any]],
    plan: dict[str, Any],
    elements: list[dict[str, Any]],
    qt: str,
    *,
    min_score: float = 40.0,
) -> dict[str, Any] | None:
    filtered = [b for b in buttons if not _is_navigation_search_link(b)]
    if not filtered:
        return None
    primary = _primary_input_element(plan, elements)
    return _pick_best_element(
        filtered,
        lambda b: _score_submit_for_context(b, primary, qt),
        min_score=min_score,
    )


def _is_navigation_search_link(el: dict[str, Any]) -> bool:
    tag = (el.get("tag") or "").lower()
    text = _norm_blob(el.get("text", ""))
    href = (el.get("href") or "").lower()
    if tag != "a":
        return False
    if text in ("search", "go", "find", "submit"):
        return False
    if "search" in text and any(k in text for k in ("parcel", "property", "map", "sales", "owner")):
        return True
    if href and (".aspx" in href or "/search" in href or href.startswith("http")) and text != "search":
        return True
    return False


def _score_submit_button(el: dict[str, Any]) -> float:
    if el.get("kind") not in ("search_button", "button"):
        return -999.0
    if _is_navigation_search_link(el):
        return -500.0
    blob = _element_blob(el)
    text = _norm_blob(el.get("text", ""))
    tag = (el.get("tag") or "").lower()
    el_type = (el.get("type") or "").lower()
    score = 0.0
    if el.get("kind") == "search_button":
        score += 25
    if text == "search":
        score += 120
    elif "search" in text and len(text) < 24:
        score += 70
    elif "search" in text:
        score += 20
    elif any(k in text for k in ("find", "lookup", "go", "submit")):
        score += 45
    if el_type == "submit":
        score += 95
    if tag == "button":
        score += 30
    if tag == "input" and el_type in ("submit", "button"):
        score += 85
    if tag == "a":
        score -= 70
    if tag == "div":
        if (el.get("role") or "").lower() == "button" or (el.get("id") or "").lower() == "search":
            score += 110
        else:
            score -= 120
    for bad in ("facebook", "twitter", "linkedin", "instagram", "tabrow", "homepage", "share", "map search"):
        if bad in blob or bad in text:
            score -= 250
    eid = (el.get("id") or "").lower()
    if any(k in eid for k in ("goog-gt", "google_translate", "gt-thumb", "translate")):
        score -= 600
    return score


def _forbidden_input_for_query(el: dict[str, Any], qt: str) -> bool:
    if el.get("kind") != "input":
        return True
    if _is_combined_criterion_search_input(el):
        return False
    blob = _element_blob(el)
    ident = f"{el.get('id', '')} {el.get('name', '')}".lower()
    if qt == "address":
        if _is_unified_tax_search_input(el):
            return False
        ph_l = (el.get("placeholder") or "").lower()
        lbl_l = (el.get("label") or "").lower()
        if re.search(r"ex:\s*armenia", ph_l, re.I):
            return True
        if re.search(r"ex:\s*.+,.+", ph_l):
            return True
        if re.search(r"ex:\s*\d{5}\b", ph_l, re.I):
            if not (lbl_l.startswith("address") or lbl_l == "address:" or "address" in lbl_l):
                return True
        if lbl_l.startswith("owner") or "owner name" in lbl_l:
            return True
        if any(k in ident for k in ("txtowner", "txtname", "ownername")):
            return True
        if re.search(r"\balt\s*key\b|\baltkey\b", blob):
            return True
        if any(k in blob for k in ("parcel", "folio", "pin", "apn", "nbhd", "subdiv", "tax year", "results/page")):
            return True
        if re.search(r"\bname\b", blob) and "street name" not in blob:
            return True
        if any(k in blob for k in ("rng", "twp", " sec", " blk", " lot")) and "street" not in blob:
            return True
    elif qt == "parcel":
        if any(k in ident for k in ("txtpropid", "txtowner", "txtaddress")):
            return True
        if re.search(r"\balt\s*key\b|\baltkey\b", blob):
            return True
        if any(k in blob for k in ("mailing address", "street name", "street #", "owner", "lastname", "firstname")):
            return True
        if re.search(r"\bname\b", blob) and "parcel" not in blob and "folio" not in blob:
            return True
    elif qt == "owner":
        if any(k in blob for k in ("street #", "mailing", "altkey", "parcel", "folio")):
            return True
    return False


def _filter_inputs_for_query(inputs: list[dict[str, Any]], qt: str) -> list[dict[str, Any]]:
    return [inp for inp in inputs if not _forbidden_input_for_query(inp, qt)]


async def ensure_search_panel_for_selector(
    page: Page,
    selector: str,
    *,
    max_attempts: int = 4,
) -> bool:
    """Expand Schneider/qPublic accordion once; never close an already-open search panel."""
    target = (selector or "").strip()
    if not target:
        return False
    for attempt in range(max_attempts):
        progressed = False
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                res = await target_scope.evaluate(SCHNEIDER_ENSURE_PANEL_OPEN_JS, target)
                if res.get("ready"):
                    return True
                if res.get("reason") == "panel_marked_open" or res.get("clicked"):
                    progressed = True
                    break
            except Exception:
                continue
        if not progressed:
            break
        await page.wait_for_timeout(650 if attempt < 2 else 950)
    return False


def selector_looks_wrong_for_address(selector: str) -> bool:
    s = (selector or "").lower()
    return any(k in s for k in ("armenia", "owner name", "txtowner", "txtname", "ownerline"))


def _placeholder_from_selector(sel: str) -> str | None:
    m = re.search(r'placeholder\s*=\s*["\']([^"\']+)["\']', sel or "", re.I)
    return m.group(1) if m else None


def _normalize_placeholder(ph: str) -> str:
    return re.sub(r"\s+", " ", (ph or "").strip()).lower()


def match_snapshot_input_selector(sel: str, elements: list[dict[str, Any]]) -> str:
    """Align vision/LLM placeholder selectors with live DOM (spacing/casing)."""
    if not sel or not elements:
        return sel
    for e in elements:
        if e.get("selector") == sel:
            return _canonical_element_selector(e)
    want_ph = _placeholder_from_selector(sel)
    if want_ph:
        want_norm = _normalize_placeholder(want_ph)
        for e in elements:
            if e.get("kind") != "input":
                continue
            if _normalize_placeholder(e.get("placeholder") or "") == want_norm:
                return _canonical_element_selector(e)
    return sel


def remap_plan_fill_selectors(plan: dict[str, Any], elements: list[dict[str, Any]]) -> None:
    for fill in plan.get("fills") or []:
        sel = (fill.get("selector") or "").strip()
        if sel:
            fill["selector"] = match_snapshot_input_selector(sel, elements)
    if plan.get("input_selector"):
        plan["input_selector"] = match_snapshot_input_selector(
            str(plan["input_selector"]), elements
        )


def _pick_best_element(
    elements: list[dict[str, Any]],
    scorer,
    min_score: float = 10.0,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -999.0
    for el in elements:
        s = scorer(el)
        if s > best_score:
            best_score = s
            best = el
    if best is None or best_score < min_score:
        return None
    return best


def _normalize_query_value(qt: str, query_value: str) -> str:
    val = re.sub(r"\s+", " ", (query_value or "").strip())
    if qt == "address" and "," in val:
        val = val.split(",", 1)[0].strip()
    return val


def _full_mailing_line(query_value: str) -> str:
    val = re.sub(r"\s+", " ", (query_value or "").strip())
    if "," in val:
        val = val.split(",", 1)[0].strip()
    return val


def _parse_street_address(address: str) -> dict[str, str]:
    cleaned = _normalize_query_value("address", address)
    out: dict[str, str] = {"full": cleaned}
    match = re.match(r"^(\d+)\s+(.+)$", cleaned)
    if not match:
        return out
    out["street_number"] = match.group(1)
    rest = match.group(2).strip()
    direction_tokens = {
        "n", "s", "e", "w", "ne", "nw", "se", "sw",
        "north", "south", "east", "west",
    }
    suffix_tokens = {
        "st", "street", "ave", "avenue", "blvd", "boulevard", "rd", "road", "dr", "drive",
        "ln", "lane", "ct", "court", "way", "pl", "place", "cir", "circle", "hwy", "highway",
        "pkwy", "parkway", "trl", "trail",
    }
    parts = rest.split()
    if not parts:
        return out
    direction = ""
    if parts[0].lower().rstrip(".") in direction_tokens:
        direction = parts.pop(0)
    elif parts and parts[-1].lower().rstrip(".") in direction_tokens:
        direction = parts.pop(-1)
    suffix = ""
    if parts and parts[-1].lower().rstrip(".") in suffix_tokens:
        suffix = parts.pop(-1)
    street_name = " ".join(parts)
    if direction:
        out["direction"] = direction
    if suffix:
        out["suffix"] = suffix
    if street_name:
        out["street_name"] = street_name
    return out


def _field_role_score(blob: str, role: str) -> float:
    b = (blob or "").strip().lower()
    if role == "street_prefix_dir":
        if "direction" in b:
            return 100.0
        return 0.0
    if role == "street_suffix_dir":
        if b in ("dir", "dir:", "-dir-") or b.endswith(" dir") or b.startswith("dir "):
            return 90.0
        if b == "dir" and "direction" not in b:
            return 80.0
        return 0.0
    weights: dict[str, tuple[tuple[str, float], ...]] = {
        "street_number": (("street #", 100), ("street number", 100), ("house number", 90), ("str #", 80)),
        "direction": (("direction", 90),),
        "street_name": (("street name", 100), ("str name", 90)),
        "suffix": (("suffix", 90), ("-suffix-", 90), ("street type", 80)),
        "tax_year": (("tax year", 100), ("taxyear", 90), ("roll year", 80)),
    }
    score = 0.0
    for tok, w in weights.get(role, ()):
        if tok in b:
            score += w
    return score


NO_RESULTS_JS = r"""
() => {
  const t = (document.body ? document.body.innerText : '').toLowerCase();
  return (
    t.includes('did not find any records') ||
    t.includes('no records found') ||
    t.includes('your search did not find') ||
    t.includes('no results found') ||
    t.includes('0 records found')
  );
}
"""

PICK_TAX_YEAR_JS = r"""
() => {
  const body = document.body ? document.body.innerText : '';
  const cur = body.match(/data current as of\s+\d{1,2}\/[a-z]+\/(\d{4})/i);
  const dataYear = cur ? parseInt(cur[1], 10) : null;
  const nowYear = new Date().getFullYear();
  const preferred = dataYear && !Number.isNaN(dataYear) ? dataYear : nowYear;
  return { preferred, dataYear, nowYear };
}
"""


ADDRESS_FIELD_VALUE_TEMPLATES = {
    "street_number": "{{input.address_street_number}}",
    "direction": "{{input.address_direction}}",
    "street_name": "{{input.address_street_name}}",
    "suffix": "{{input.address_suffix}}",
    "mailing_line": "{{input.address}}",
}


def _append_tax_year_fill(
    elements: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> None:
    selects = [e for e in elements if e.get("kind") == "select"]
    tax_pick = _pick_best_element(
        selects, lambda e: _field_role_score(_element_blob(e), "tax_year"), min_score=50.0
    )
    if tax_pick and not any(f.get("field_role") == "tax_year" for f in fills):
        fills.append(
            {
                "selector": tax_pick["selector"],
                "value": "__auto_tax_year__",
                "field_role": "tax_year",
                "control": "select",
            }
        )


def _build_structured_address_fills(
    inputs: list[dict[str, Any]],
    query_value: str,
    elements: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    parsed = _parse_street_address(query_value)
    if not parsed.get("street_name"):
        return []
    allowed = _filter_inputs_for_query(inputs, "address")
    selects = [e for e in (elements or []) if e.get("kind") == "select"]
    controls = allowed + selects
    fills: list[dict[str, Any]] = []
    for role, key in (
        ("street_number", "street_number"),
        ("street_prefix_dir", "direction"),
        ("street_name", "street_name"),
        ("suffix", "suffix"),
    ):
        val = parsed.get(key, "")
        if not val:
            continue
        role_pick = _pick_best_element(
            controls,
            lambda e, r=role: _field_role_score(_element_blob(e), r),
            min_score=35.0,
        )
        if role_pick:
            control = "select" if role_pick.get("kind") == "select" else "input"
            out_val = val
            if role == "street_prefix_dir" and len(val) <= 3:
                out_val = val.upper()
            elif role == "suffix" and control == "select":
                out_val = val.upper().rstrip(".")
            fills.append(
                {
                    "selector": role_pick["selector"],
                    "value": out_val,
                    "field_role": role,
                    "control": control,
                }
            )
    if elements:
        _append_tax_year_fill(elements, fills)
    return fills


def _pick_mailing_address_field(inputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    allowed = _filter_inputs_for_query(inputs, "address")
    best: dict[str, Any] | None = None
    best_score = -999.0
    for inp in allowed:
        blob = _element_blob(inp)
        if not any(k in blob for k in ("mailing address", "mail address", "mailing", "site address")):
            continue
        top = float(inp.get("layout_top") or 0)
        score = _score_input_for_query(inp, "address") + (top / 1500.0)
        if score > best_score:
            best_score = score
            best = inp
    if best is None or best_score < 20:
        return None
    return best


def _is_stacked_qpublic_portal(snapshot: dict[str, Any]) -> bool:
    url = (snapshot.get("url") or "").lower()
    return "schneidercorp.com" in url or "qpublic.net" in url or "beacon." in url


def _is_accordion_header_toggle(el: dict[str, Any] | None) -> bool:
    if not el:
        return False
    eid = (el.get("id") or "").lower()
    sel = (el.get("selector") or "").lower()
    return "btnheadertoggle" in eid or "btnheadertoggle" in sel


def _is_portal_main_nav_tab(el: dict[str, Any]) -> bool:
    """Top Beacon/qPublic menu (Results, Map, Sales…) — not search accordions."""
    if _is_accordion_header_toggle(el):
        return False
    text = _norm_blob(el.get("text", ""))
    href = (el.get("href") or "").lower()
    if any(
        k in text
        for k in (
            "results",
            "sales results",
            "sales list",
            "sales search",
            "tax estimator",
            "advanced search results",
            " layers",
            "layer",
        )
    ):
        return True
    if "pagetypeid=" in href:
        if "pagetypeid=2" in href and "search" in text:
            return False
        if "pagetypeid=4" in href:
            return False
        return True
    if text in ("home", "map") or text.startswith("map "):
        return True
    return False


def _pick_query_mode_tab(elements: list[dict[str, Any]], qt: str) -> dict[str, Any] | None:
    """Best search-mode switch (Parcel ID / Address / Owner) from snapshot — not portal nav."""
    keywords: dict[str, tuple[str, ...]] = {
        "parcel": ("parcel id", "parcel number", "parcel #", "folio", "pin", "strap", "apn", "parcel"),
        "address": ("address", "site address", "situs", "location address", "street address"),
        "owner": ("owner name", "owner"),
    }
    want = keywords.get(qt, keywords["parcel"])
    best: dict[str, Any] | None = None
    best_score = -1.0
    for el in elements:
        if el.get("kind") not in ("tab", "button", "control"):
            continue
        if _is_portal_main_nav_tab(el):
            continue
        text = _norm_blob(el.get("text", "")).strip()
        if text.lower() in ("search", "go", "submit", "find", "lookup"):
            continue
        if _is_spurious_mode_tab_label(text):
            continue
        href = (el.get("href") or "").lower()
        if href and any(href.endswith(ext) for ext in (".pdf", ".doc", ".docx")):
            continue
        blob = f"{text} {_norm_blob(el.get('label', ''))}"
        for kw in want:
            score = 0.0
            if text == kw:
                score = 100.0
            elif text.startswith(kw) and len(text) <= len(kw) + 8:
                score = 92.0
            elif kw in blob:
                score = 72.0
            if score > best_score:
                best_score = score
                best = el
    return best if best_score >= 70.0 else None


def _input_panel_matches_query(el: dict[str, Any], qt: str) -> bool:
    section = _norm_blob(el.get("section_label", ""))
    ident = f"{el.get('id', '')} {el.get('name', '')}".lower()
    aria = (el.get("label") or el.get("placeholder") or "").lower()
    blob = f"{section} {ident} {aria}"
    if qt == "address":
        if any(k in ident for k in ("txtowner", "txtname", "ownername")):
            return False
        return any(
            k in blob
            for k in ("location address", "txtaddress", "site address", "situs", "mailing address", "enter address")
        )
    if qt == "parcel":
        if any(k in ident for k in ("txtaddress", "txtowner", "txtpropid", "propid")):
            return False
        return any(k in blob for k in ("parcel number", "parcel id", "txtparcel", "folio", "pin", "strap", "enter parcel"))
    if qt == "owner":
        if any(k in ident for k in ("txtaddress", "txtparcel")):
            return False
        return any(k in blob for k in ("owner name", "txtowner", "enter name", "owner"))
    return True


def _toggle_selector_from_control_id(control_id: str) -> str | None:
    prefix = _body_pane_prefix(control_id)
    return f"#{prefix}_btnHeaderToggle" if prefix else None


def _canonical_element_selector(el: dict[str, Any]) -> str:
    eid = (el.get("id") or "").strip()
    if eid and not re.match(r"^\d", eid):
        safe = eid.replace(":", "\\:")
        return f"#{safe}" if ":" not in eid else f"#{eid}"
    sel = (el.get("selector") or "").strip()
    return sel


async def ensure_schneider_search_workspace(page: Page) -> bool:
    """
    Leave bulk Results / export pages and open the Real Property Search form
  (Beacon / qPublic). Returns True when a search form is likely visible.
    """
    url = (page.url or "").lower()
    if "schneidercorp" not in url and "qpublic" not in url:
        return False
    try:
        body = (await page.locator("body").inner_text(timeout=4000) or "")[:3500].lower()
    except Exception:
        body = ""
    bulk = "5000 results" in body or ("maximum)" in body and "parcel results" in body)
    wrong = bulk or "pagetypeid=3" in url or ("pagetypeid=5" in url and "search" not in body[:500])
    if not wrong and "pagetypeid=2" in url:
        return True
    if not wrong and any(k in body for k in ("search by owner name", "search by location address", "search by parcel")):
        return True

    labels = (
        "Real Property Search",
        "Property Search",
        "Parcel Search",
        "Record Search",
    )
    for scope in [page, *page.frames]:
        for label in labels:
            try:
                link = scope.get_by_role("link", name=label, exact=False).first
                if await link.count() == 0 or not await link.is_visible(timeout=1200):
                    continue
                await link.click(force=True, timeout=4000)
                await page.wait_for_timeout(2000)
                logger.info("Navigated to search workspace via %r", label)
                return True
            except Exception:
                continue
    return not wrong


def _pick_location_address_input(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    inputs = _filter_inputs_for_query(
        [e for e in snapshot.get("elements", []) if e.get("kind") == "input"],
        "address",
    )
    return _pick_best_element(inputs, lambda e: _score_input_for_query(e, "address"), min_score=85.0)


def _single_line_address_fill(snapshot: dict[str, Any], query_value: str) -> list[dict[str, Any]]:
    loc = _pick_location_address_input(snapshot)
    if not loc:
        mailing = build_mailing_line_fills(snapshot, query_value)
        return mailing
    return [
        {
            "selector": _canonical_element_selector(loc),
            "value": _full_mailing_line(query_value),
            "field_role": "mailing_line",
            "control": "input",
        }
    ]


def build_mailing_line_fills(snapshot: dict[str, Any], query_value: str) -> list[dict[str, Any]]:
    inputs = [e for e in snapshot.get("elements", []) if e.get("kind") == "input"]
    mailing_pick = _pick_mailing_address_field(inputs)
    if not mailing_pick:
        return []
    fills: list[dict[str, Any]] = [
        {
            "selector": mailing_pick["selector"],
            "value": _full_mailing_line(query_value),
            "field_role": "mailing_line",
            "control": "input",
        }
    ]
    _append_tax_year_fill(snapshot.get("elements", []), fills)
    return fills


def build_search_fills(snapshot: dict[str, Any], qt: str, query_value: str) -> list[dict[str, str]]:
    elements = snapshot.get("elements", [])
    inputs = [e for e in elements if e.get("kind") == "input"]
    normalized = _normalize_query_value(qt, query_value)

    if qt == "address":
        if _is_stacked_qpublic_portal(snapshot):
            single = _single_line_address_fill(snapshot, query_value)
            if single:
                return single

        combined = [e for e in inputs if _is_combined_criterion_search_input(e)]
        if combined:
            best_combined = _pick_best_element(
                combined,
                lambda e: _score_input_for_query(e, "address"),
                min_score=80.0,
            )
            if best_combined:
                return [
                    {
                        "selector": _canonical_element_selector(best_combined),
                        "value": normalized,
                        "field_role": "combined_search",
                    }
                ]

        if _snapshot_is_tax_collector_portal(snapshot):
            tax_inputs = [
                e
                for e in inputs
                if e.get("kind") == "input"
                and (
                    _is_unified_tax_search_input(e)
                    or not _forbidden_input_for_query(e, "address")
                )
            ]
            tax_best = _pick_best_element(
                tax_inputs,
                lambda e: _score_input_for_query(e, "address") + _score_unified_tax_search_bonus(e),
                min_score=55.0,
            )
            if tax_best:
                return [
                    {
                        "selector": _canonical_element_selector(tax_best),
                        "value": normalized,
                        "field_role": "unified_tax_search",
                    }
                ]

        allowed = _filter_inputs_for_query(inputs, "address")
        structured = _build_structured_address_fills(inputs, query_value, elements)
        street_name_pick = _pick_best_element(
            allowed,
            lambda e: _field_role_score(_element_blob(e), "street_name"),
            min_score=35.0,
        )
        street_num_pick = _pick_best_element(
            allowed,
            lambda e: _field_role_score(_element_blob(e), "street_number"),
            min_score=35.0,
        )
        mailing_fills = build_mailing_line_fills(snapshot, query_value)

        if street_name_pick and street_num_pick and len(structured) >= 2:
            return structured

        if mailing_fills:
            return mailing_fills

        if structured:
            return structured

        best = _pick_best_element(allowed, lambda e: _score_input_for_query(e, qt), min_score=40.0)
        if best and _score_input_for_query(best, qt) >= 40:
            return [
                {
                    "selector": _canonical_element_selector(best),
                    "value": normalized,
                    "field_role": "mailing_line",
                }
            ]
        return []

    allowed = _filter_inputs_for_query(inputs, qt) if qt in ("parcel", "owner") else inputs
    best = _pick_best_element(allowed, lambda e: _score_input_for_query(e, qt), min_score=15.0)
    if not best:
        return []
    return [{"selector": _canonical_element_selector(best), "value": normalized}]


def refine_plan_from_snapshot(plan: dict[str, Any], snapshot: dict[str, Any], qt: str, query_value: str) -> dict[str, Any]:
    elements = snapshot.get("elements", [])
    fills = build_search_fills(snapshot, qt, query_value)
    structured_address = qt == "address" and len(fills) >= 2

    if qt == "address":
        llm_sel = (plan.get("input_selector") or "").lower()
        structured_fills = _build_structured_address_fills(
            [e for e in elements if e.get("kind") == "input"],
            query_value,
            elements,
        )
        mailing_fills = build_mailing_line_fills(snapshot, query_value)
        if _is_stacked_qpublic_portal(snapshot):
            single = _single_line_address_fill(snapshot, query_value)
            if single:
                plan["fills"] = single
                plan["input_selector"] = single[0]["selector"]
                plan["address_search_mode"] = "mailing_line"
                structured_fills = []
        elif structured_fills:
            plan["fills"] = structured_fills
            plan["input_selector"] = structured_fills[0]["selector"]
            plan["address_search_mode"] = "try_structured_then_mailing"
        elif fills:
            plan["fills"] = fills
            plan["input_selector"] = fills[0]["selector"]
            plan["address_search_mode"] = "mailing_line" if fills[0].get("field_role") == "mailing_line" else "structured"
        if mailing_fills and structured_fills:
            plan["alternate_fills"] = mailing_fills
        elif mailing_fills and not structured_fills and not fills:
            plan["fills"] = mailing_fills
            plan["address_search_mode"] = "mailing_line"
        if fills:
            plan["fills"] = fills
            plan["input_selector"] = fills[0]["selector"]
            for i, fill in enumerate(plan["fills"]):
                hit = next((e for e in elements if e.get("selector") == fill.get("selector")), None)
                if hit:
                    plan["fills"][i]["selector"] = _canonical_element_selector(hit)
            plan["input_selector"] = plan["fills"][0]["selector"]
        elif plan.get("fills"):
            bad_sel = (plan["fills"][0].get("selector") or "").lower()
            bad_el = next(
                (e for e in elements if e.get("selector") == plan["fills"][0].get("selector")),
                {"kind": "input", "selector": bad_sel, "placeholder": bad_sel, "label": ""},
            )
            if "armenia" in bad_sel or _forbidden_input_for_query(bad_el, "address"):
                plan.pop("fills", None)
                plan.pop("input_selector", None)
        if not plan.get("fills"):
            llm_inp = (plan.get("input_selector") or "").strip()
            hit = _element_from_plan_selector(elements, llm_inp) if llm_inp else None
            if hit and hit.get("kind") == "input" and not _forbidden_input_for_query(hit, "address"):
                plan["fills"] = [
                    {
                        "selector": _canonical_element_selector(hit),
                        "value": _normalize_query_value("address", query_value),
                        "field_role": "combined_search",
                    }
                ]
                plan["input_selector"] = plan["fills"][0]["selector"]
            else:
                plan.pop("input_selector", None)
        if "altkey" in llm_sel or "alt key" in llm_sel or "armenia" in llm_sel:
            plan.pop("input_selector", None)
    elif qt == "parcel":
        llm_sel = (plan.get("input_selector") or "").lower()
        if any(k in llm_sel for k in ("altkey", "alt_key", "alt key")):
            plan.pop("input_selector", None)
        if fills:
            plan["fills"] = fills
            plan["input_selector"] = fills[0]["selector"]
        elif not plan.get("fills"):
            parcel_pick = _pick_best_element(
                _filter_inputs_for_query(
                    [e for e in elements if e.get("kind") == "input"],
                    "parcel",
                ),
                lambda e: _score_input_for_query(e, "parcel"),
                min_score=40.0,
            )
            if parcel_pick:
                plan["fills"] = [
                    {
                        "selector": parcel_pick["selector"],
                        "value": _normalize_query_value("parcel", query_value),
                        "field_role": "parcel_number",
                    }
                ]
                plan["input_selector"] = parcel_pick["selector"]
        if plan.get("input_selector") and any(
            k in (plan.get("input_selector") or "").lower() for k in ("altkey", "alt_key")
        ):
            plan.pop("input_selector", None)
            plan["fills"] = fills or plan.get("fills")
    elif fills:
        plan["fills"] = fills
        plan["input_selector"] = fills[0]["selector"]

    if not structured_address and qt != "address":
        inputs = _filter_inputs_for_query(
            [e for e in elements if e.get("kind") == "input"],
            qt,
        )
        best_input = _pick_best_element(inputs, lambda e: _score_input_for_query(e, qt), min_score=15.0)
        if best_input:
            llm_score = _score_input_for_query(
                next((e for e in inputs if e.get("selector") == plan.get("input_selector")), {}),
                qt,
            )
            if _score_input_for_query(best_input, qt) >= llm_score + 15:
                plan["input_selector"] = best_input["selector"]
                if not plan.get("fills"):
                    plan["fills"] = [
                        {"selector": best_input["selector"], "value": _normalize_query_value(qt, query_value)}
                    ]

    buttons = _collect_submit_candidates_from_elements(elements)
    best_submit = _pick_submit_for_search_context(buttons, plan, elements, qt)
    trusted_llm_submit = _plan_submit_matches_snapshot(plan, elements)
    if best_submit and not trusted_llm_submit:
        submit_sel = (plan.get("submit_selector") or "").lower()
        unsafe = any(k in submit_sel for k in ("facebook", "twitter", "tabrow", "homepage", "linkedin", "goog-gt", "thumbup"))
        nav_phrase = any(k in submit_sel for k in ("parcel search", "property search", "map search"))
        generic_submit = ":has-text(" in (plan.get("submit_selector") or "").lower()
        if unsafe or nav_phrase or generic_submit or not plan.get("submit_selector"):
            plan["submit_selector"] = best_submit["selector"]
        else:
            existing = next((b for b in buttons if b.get("selector") == plan.get("submit_selector")), None)
            if existing is None:
                existing = _element_from_plan_selector(elements, plan.get("submit_selector") or "")
            primary = _primary_input_element(plan, elements)
            existing_score = _score_submit_for_context(existing, primary, qt) if existing else -999.0
            best_score = _score_submit_for_context(best_submit, primary, qt)
            if existing_score < best_score:
                plan["submit_selector"] = best_submit["selector"]
    primary = _primary_input_element(plan, elements)
    if primary and not _input_panel_matches_query(primary, qt):
        rebuilt = build_search_fills(snapshot, qt, query_value)
        if rebuilt:
            plan["fills"] = rebuilt
            plan["input_selector"] = rebuilt[0]["selector"]
            for fill in plan["fills"]:
                hit = next((e for e in elements if e.get("selector") == fill.get("selector")), None)
                if hit:
                    fill["selector"] = _canonical_element_selector(hit)
            if plan["fills"]:
                plan["input_selector"] = plan["fills"][0]["selector"]
            primary = None
    primary = _primary_input_element(plan, elements)
    if primary:
        toggle = _section_toggle_selector_for_input(primary)
        if toggle:
            plan["tab_selector"] = toggle
            plan["tab_panel"] = _body_pane_prefix(primary.get("id") or "") or primary.get("panel_id")
    elif plan.get("tab_selector") and "btnheadertoggle" not in str(plan.get("tab_selector", "")).lower():
        plan.pop("tab_selector", None)
    if _is_spurious_mode_tab_selector(str(plan.get("tab_selector") or "")):
        plan.pop("tab_selector", None)
    combined_only = bool(plan.get("fills")) and plan.get("fills", [{}])[0].get("field_role") == "combined_search"
    mode_tab = None if combined_only else _pick_query_mode_tab(elements, qt)
    if mode_tab:
        mode_sel = _canonical_element_selector(mode_tab)
        llm_tab = (plan.get("tab_selector") or "").lower()
        needs_mode = not plan.get("tab_selector") or _is_spurious_mode_tab_selector(llm_tab) or (
            qt == "parcel"
            and any(k in llm_tab for k in ("owner", "address", "sales"))
        ) or (
            qt == "address" and "owner" in llm_tab
        )
        if needs_mode and not _is_spurious_mode_tab_selector(mode_sel):
            plan["tab_selector"] = mode_sel
    plan["query_type"] = qt
    return plan


QUERY_CONDITIONS = {
    "parcel": "input.parcel_id",
    "address": "input.address",
    "owner": "input.owner_name",
}


def build_runtime_steps_from_plan(plan: dict[str, Any], *, include_navigate: bool = False, portal_url: str = "") -> list[dict[str, Any]]:
    """Turn a validated AI search plan into Playwright recipe steps for this execution."""
    qt = (plan.get("query_type") or "parcel").lower()
    cond = QUERY_CONDITIONS.get(qt, "input.parcel_id")
    steps: list[dict[str, Any]] = []
    if include_navigate and portal_url:
        steps.append(
            {
                "action": "navigate",
                "url": portal_url,
                "description": "Navigate to county search portal",
                "timeout_ms": 25000,
            }
        )

    tab_sel = plan.get("tab_selector")
    if tab_sel:
        steps.append(
            {
                "action": "click_if_visible",
                "selector": tab_sel,
                "condition": cond,
                "description": f"Click {qt} search mode tab/section",
                "optional": True,
            }
        )
        steps.append(
            {
                "action": "expand_search_section",
                "selector": (plan.get("fills") or [{}])[0].get("selector") or plan.get("input_selector") or "",
                "condition": cond,
                "description": f"Ensure {qt} search fields are visible",
                "optional": True,
            }
        )

    radio_sel = plan.get("radio_selector")
    if radio_sel:
        steps.append(
            {
                "action": "click_if_visible",
                "selector": radio_sel,
                "condition": cond,
                "description": "Select search mode radio option",
                "optional": True,
            }
        )

    fills = plan.get("fills") or []
    if not fills and plan.get("input_selector"):
        value_tpl = {
            "parcel": "{{input.parcel_id}}",
            "address": "{{input.address}}",
            "owner": "{{input.owner_name}}",
        }.get(qt, "{{input.parcel_id}}")
        fills = [{"selector": plan["input_selector"], "value": value_tpl}]

    for fill in fills:
        sel = (fill.get("selector") or "").strip()
        if not sel:
            continue
        role = (fill.get("field_role") or "").strip()
        control = (fill.get("control") or "input").lower()
        val = fill.get("value", "")
        if isinstance(val, str) and "{{" not in val:
            value = val
        elif role and role in ADDRESS_FIELD_VALUE_TEMPLATES:
            value = ADDRESS_FIELD_VALUE_TEMPLATES[role]
        else:
            value = val or {
                "parcel": "{{input.parcel_id}}",
                "address": "{{input.address}}",
                "owner": "{{input.owner_name}}",
            }.get(qt, "{{input.parcel_id}}")
        desc = f"Fill {role.replace('_', ' ')}" if role else "Enter search criteria"
        wait_step = {
            "action": "wait_for_selector",
            "selector": sel,
            "condition": cond,
            "description": f"Wait for field ({desc})",
            "timeout_ms": 4000,
            "optional": True,
        }
        steps.append(wait_step)
        if control == "select" or role == "tax_year":
            step: dict[str, Any] = {
                "action": "select",
                "selector": sel,
                "condition": cond,
                "description": desc,
            }
            if value == "__auto_tax_year__" or role == "tax_year":
                step["auto_tax_year"] = True
            elif role == "street_prefix_dir":
                step["label"] = value
            else:
                step["value"] = value
            steps.append(step)
        else:
            steps.append(
                {
                    "action": "fill",
                    "selector": sel,
                    "value": value,
                    "condition": cond,
                    "description": desc,
                }
            )

    submit_sel = plan.get("submit_selector")
    if submit_sel:
        steps.append(
            {
                "action": "click",
                "selector": submit_sel,
                "condition": cond,
                "description": "Submit property search",
            }
        )
    post_search_selector = (
        "table.ui-jqgrid-btable tbody tr, table tbody tr.jqgrow, #ResultsGrid tbody tr, "
        "div[id*='searchResults'] table tbody tr, "
        "table tbody tr td, [role='row'], td:has-text('Site Location'), td:has-text('Name / Address'), "
        "td:has-text('Parcel ID'), a[href*='RecordDetail']"
    )
    steps.append(
        {
            "action": "wait_for_selector",
            "selector": post_search_selector,
            "description": "Wait for search results or property detail",
            "timeout_ms": 8000,
            "optional": True,
        }
    )
    steps.append(
        {
            "action": "wait",
            "duration": 1200,
            "description": "Brief pause after search submission",
            "optional": True,
        }
    )
    return steps


VISION_PROMPT_SUFFIX = """
You also receive a screenshot of the live portal. Use it together with the DOM snapshot to:
- Choose the correct inputs for the query_type (address vs Property Number / Parcel Number vs owner). Never fill Alt Key when searching by parcel/APN/folio.
- On county **tax collector** search pages (VisualGov, floridatax, Phenix, and similar): use **Property Number** for parcel queries; leave Roll Type and Tax Year at their defaults (e.g. All Roll Types / All Years) unless the instructions require a specific year.
- On multi-field address forms, return `fills` for Street #, Direction, Street Name, Suffix when visible.
- When a Mailing Address / Mail Address field exists, set `alternate_fills` with the full query_value as one line (e.g. "825 N HEATHROW DR"); many county CommonSearch portals accept that when split fields return no records.
- **SEARCH BUTTON (critical):** If the screenshot shows a yellow, gold, blue, or neutral **Search** control (often beside Roll Type / Tax Year dropdowns on tax collector forms), you MUST set `submit_selector` and `use_enter_key: false`. Never leave `submit_selector` null and never press Enter when that button is visible — many portals block Enter or only run JavaScript on click.
- Many tax portals use `<div id="search" role="button" class="button" onclick="submitForm(0)">Search</div>` — NOT a `<button>` tag. When the snapshot shows `#search` or a `search_button` with id `search`, set `submit_selector` to `#search` exactly.
- Copy `submit_selector` **verbatim** from a `search_button` entry in the page snapshot when one exists. Tax collector Search controls may be `<input type="submit">`, `<input type="button">`, or `div#search[role=button]` — never use bare `button:has-text("Search")` when the snapshot shows `#search` or an input selector.
- Prefer, in order: exact snapshot `selector` for the Search control; then `#id` from snapshot; then `input[type="submit"][value="Search"], input[type="button"][value="Search"], input[value="Search" i]` before any `button:has-text(...)`.
- Click the form's submit control in the same section as the filled input (qPublic/Schneider stacks several Search buttons — use the one beside Location Address / Owner / Parcel panel you filled). On single-form tax collector pages there is only one Search — use that one.
- NEVER click sidebar or header links such as "Search Tax Database", "Parcel Search", or "Property Search" — only the in-form Search that activates the query you filled.
- Stacked Schneider/qPublic pages: expand the panel that contains the chosen input (btnHeaderToggle) before fill; never type into the first visible text box if it belongs to another section (e.g. Owner vs Location Address).
- On floridapa / Schneider GIS map portals: after search, open Parcel Details (not GIS Map), wait for owner/situs tables or detail iframe — do NOT click toolbar Print (native browser dialog). Automation saves PDF via Playwright directly to storage.
- **Homepage / hero combined search:** When one visible text box accepts owner, address, and parcel/PCN together with a adjacent **Search** submit (often `input[type=submit]` with an id such as `btnReal`), set `input_selector` to that box and `submit_selector` to that submit control exactly from the snapshot. Set `tab_selector` to null — do not click sidebar or quick links (Address Change, Map Search, forms). Never use browser translate widgets (`goog-gt-*`) as submit.
- **county-taxes.net / Miami-Dade (Phenix) — search stops when bills are on screen:** After Search, many parcels open the **account detail** immediately (table with columns BILL, AMOUNT DUE, STATUS, ACTION; rows like "2026 Annual Bill", Paid $…, Receipt #…, Print PDF). If the screenshot shows that bill history table, the search succeeded on the **current** page — return `tab_selector`, `radio_selector`, and any *extra* navigation selectors as **null**. Do NOT plan clicks on the blue **Menu** drawer, sidebar links (Property Tax, Business Tax, Reports, Get Bills By Email, Vehicle Registration), hamburger icons, or a second **Search**. Close mentally past any **Public notifications** overlay (X) — never set `submit_selector` to links inside that modal.
- **county-taxes.net results vs detail:** If a short results list appears, only one account row should be opened when multiple rows exist; if the detail table is already visible, do **not** add selectors that navigate again. Never use **Print (PDF)** or receipt links as `submit_selector` / `tab_selector` (they open print/PDF flows, not search).
- **county-taxes.net unified search bar:** On `…/property-tax` use the single name/address/account/folio field and the one in-page Search control — not header/menu Property Tax links.
- **Slow SPAs:** Follow assessor rule 9 — preparation **playwright_diagnostic_steps** from the live snapshot, then full selectors for recipe reuse once inputs are visible.
"""

SECURITY_PROMPT_SUFFIX = """
8. SECURITY BARRIERS (Cloudflare, Turnstile, CAPTCHA, bot checks):
   - If the screenshot or snapshot shows "Performing security verification", Cloudflare/CLOUDFLARE branding, Turnstile widgets, "Verify you are human", CAPTCHA, or rate-limit pages, you MUST NOT return county search selectors yet.
   - Set **security_barrier_detected** to true and **security_barrier_type** to one of: cloudflare, turnstile, captcha, rate_limit.
   - Set tab_selector, radio_selector, input_selector, submit_selector, fills, and alternate_fills to null.
   - Return **playwright_diagnostic_steps**: a JSON array of Playwright recipe steps to clear the barrier, then execution will re-plan the search.
   - Use the same step shape as runtime recipes: each step has **action**, **selector** (CSS string when needed), **description**, optional **duration** / **timeout_ms**, **optional**: true for fragile clicks.
   - Allowed actions: **screenshot**, **wait**, **wait_for_selector**, **wait_for_load_state**, **click**, **click_if_visible**, **fill**, **type**, **check**.
   - Cloudflare / Turnstile: wait for widget, click the visible challenge control when appropriate (e.g. `.cf-turnstile`, `iframe[src*='challenges.cloudflare.com']`, `#challenge-form input[type='checkbox']`), wait for auto-redirect or for challenge iframe to disappear, then screenshot.
   - Classic CAPTCHA: use **fill** / **type** on text inputs shown in the snapshot; **click** submit/verify buttons from the snapshot only (never invent selectors). Image/audio CAPTCHA may not be solvable — still return best-effort visible controls with `optional: true`.
   - Example playwright_diagnostic_steps:
     [
       {"action": "screenshot", "description": "Security barrier before recovery"},
       {"action": "wait", "duration": 2000, "description": "Let Turnstile script load"},
       {"action": "wait_for_selector", "selector": ".cf-turnstile, iframe[src*='challenges.cloudflare.com']", "state": "visible", "timeout_ms": 20000, "optional": true},
       {"action": "click_if_visible", "selector": ".cf-turnstile", "description": "Activate Turnstile widget", "optional": true},
       {"action": "wait", "duration": 5000, "description": "Wait for Cloudflare verification"},
       {"action": "wait_for_load_state", "state": "networkidle", "timeout_ms": 15000, "optional": true},
       {"action": "screenshot", "description": "After security challenge interaction"}
     ]
   - reasoning: briefly describe which controls you saw and what the steps attempt; do not return county search selectors in the same response.
9. When **security_barrier_detected** is true, rule 8 overrides rules 1–7: return only **playwright_diagnostic_steps** (click/fill/wait as needed) and null out all search selectors.
"""

def _default_security_diagnostic_steps() -> list[dict[str, Any]]:
    from app.browser.tax_verification_challenge import county_taxes_security_diagnostic_steps

    return county_taxes_security_diagnostic_steps() + [
        {
            "action": "screenshot",
            "description": "After security challenge interaction",
        },
    ]


DEFAULT_SECURITY_DIAGNOSTIC_STEPS: list[dict[str, Any]] = _default_security_diagnostic_steps()

def _ai_system_prompt() -> str:
    return SYSTEM_PROMPT_ASSESSOR + VISION_PROMPT_SUFFIX + SECURITY_PROMPT_SUFFIX


def _sanitize_diagnostic_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep Playwright steps safe for security-barrier recovery (no navigation/extract)."""
    allowed = frozenset(
        {
            "screenshot",
            "wait",
            "wait_for_selector",
            "wait_for_load_state",
            "click",
            "click_if_visible",
            "fill",
            "type",
            "check",
            "uncheck",
            "select",
        }
    )
    out: list[dict[str, Any]] = []
    for step in steps or []:
        action = str(step.get("action") or "").lower().strip()
        if action in allowed:
            out.append(step)
    return out or _default_security_diagnostic_steps()


def _security_barrier_plan(
    *,
    barrier_type: str,
    reason: str,
    diagnostic_steps: list[dict[str, Any]] | None = None,
    query_type: str = "parcel",
) -> dict[str, Any]:
    steps = _sanitize_diagnostic_steps(diagnostic_steps)
    return {
        "query_type": query_type,
        "security_barrier_detected": True,
        "security_barrier_type": barrier_type,
        "playwright_diagnostic_steps": steps,
        "tab_selector": None,
        "radio_selector": None,
        "input_selector": None,
        "submit_selector": None,
        "fills": None,
        "alternate_fills": None,
        "use_enter_key": False,
        "confidence": "high",
        "reasoning": reason,
    }


async def _page_security_hint(page: Page | None) -> str | None:
    if not page or page.is_closed():
        return None
    try:
        from app.browser.security_detector import SecurityDetector

        sec = await SecurityDetector.inspect_page(page)
        if sec.is_detected:
            return sec.reason or sec.event_type or "Security barrier detected"
    except Exception:
        pass
    return None


def _plan_indicates_security_barrier(plan: dict[str, Any]) -> bool:
    if plan.get("security_barrier_detected"):
        return True
    btype = (plan.get("security_barrier_type") or "").strip().lower()
    return btype in ("cloudflare", "turnstile", "captcha", "rate_limit", "security")


async def _run_security_diagnostic_steps(
    page: Page,
    steps: list[dict[str, Any]],
    workflow_run_id: str | None,
    node_id: str | None,
    portal_url: str | None = None,
) -> None:
    from app.browser.tax_verification_challenge import attempt_county_taxes_human_verification
    from app.browser.step_runner import PlaywrightStepRunner

    try:
        await attempt_county_taxes_human_verification(page)
        await page.wait_for_timeout(800)
    except Exception as exc:
        logger.debug("County-taxes verification click attempt: %s", exc)

    runner = PlaywrightStepRunner(page, str(workflow_run_id or ""), str(node_id or ""))
    await runner.execute_steps(
        _sanitize_diagnostic_steps(steps),
        {},
        security_barrier_mode=True,
    )
    if portal_url:
        from app.browser.navigation import recover_blank_main_frame

        await recover_blank_main_frame(
            page,
            portal_url,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
        )


def _plan_needs_ai_page_preparation(plan: dict[str, Any]) -> bool:
    """AI-authored wait/load steps while the portal SPA is still mounting (not a security barrier)."""
    if _plan_indicates_security_barrier(plan):
        return False
    diag = _sanitize_diagnostic_steps(plan.get("playwright_diagnostic_steps"))
    if not diag:
        return False
    has_search = bool(plan.get("input_selector") or plan.get("fills"))
    conf = (plan.get("confidence") or "").strip().lower()
    return not has_search or conf == "low"


async def _run_ai_page_preparation_steps(
    page: Page,
    steps: list[dict[str, Any]],
    workflow_run_id: str | None,
    node_id: str | None,
) -> None:
    from app.browser.step_runner import PlaywrightStepRunner

    runner = PlaywrightStepRunner(page, str(workflow_run_id or ""), str(node_id or ""))
    await runner.execute_steps(
        _sanitize_diagnostic_steps(steps),
        {},
        security_barrier_mode=False,
    )


SYSTEM_PROMPT_ASSESSOR = """You analyze property appraiser, assessor, and county tax collector websites and return Playwright CSS selectors to run a search.

Given:
- query_type: address | owner | parcel (parcel may be labeled folio, APN, TMK, PIN, strap, account #, property number, etc.)
- query_value: the value to search for
- page snapshot: visible tabs, radio buttons, inputs, and search buttons with suggested selectors, labels, and placeholders

Return ONLY valid JSON:
{
  "tab_selector": "selector to click the correct search tab (e.g. [role='tab']:has-text('Basic Search'), a:has-text('Parcel')), or null if not needed",
  "radio_selector": "selector for radio option if the portal requires selecting search mode (e.g. Folio vs Parcel Number, Real Property vs Tangible), or null",
  "input_selector": "selector for the primary text field to fill",
  "fills": [{"selector": "css selector", "value": "value to type"}],
  "alternate_fills": [{"selector": "css selector", "value": "full address for mailing/site line"}],
  "address_search_mode": "structured | mailing_line | try_structured_then_mailing",
  "submit_selector": "selector for the in-form search/submit control that runs the filled query",
  "use_enter_key": false,
  "confidence": "high|medium|low",
  "reasoning": "one short sentence",
  "security_barrier_detected": false,
  "security_barrier_type": null,
  "playwright_diagnostic_steps": []
}

Strict Rules:
1. FIELD IDENTIFICATION & PLACEHOLDER FORMATS:
   - Many assessor and tax collector portals do NOT have 'parcel' or 'address' in field names/IDs. Instead, they use example format placeholders or row labels:
     * Parcel / Folio / Property Number: for query_type parcel, use the field labeled Property Number, Parcel Number, Parcel ID, Folio, or similar — NEVER Alt Key / Altkey (a separate identifier on many GIS portals like DeSoto). On tax collector "Search Selection" forms with Roll Type and Tax Year, fill only the one search row that matches query_type (e.g. Property Number for parcel).
     * **Single-criterion rule:** When page text says use only ONE search option, return exactly ONE `fills` entry for the matching field. Set `fills[].value` to the raw `query_value` only (no field name, no `#propertynumber` prefix, no extra labels). Prefer `#propertynumber` or the snapshot input whose label is "Property Number". Leave Owner, Address, House Number, Street Name, and Tax Bill Number unfilled (do not include them in `fills`).
     * Parcel / Folio: placeholders matching numeric/hyphenated examples (e.g. '##-##-##-####-####-####', 'EX:121415-1214', 'enter parcel number...') or data-bind containing 'folio'/'pin'/'parcel'.
     * Owner Name: placeholders matching name examples (e.g. 'EX: SMITH, JOHN L', 'enter name...', 'DOE, J', 'Last, First') or data-bind containing 'owner'.
     * Address — two valid patterns (choose based on visible labels, never Altkey/parcel fields):
       A) Structured: Street #, Direction (select), Street Name, and Suffix (select) parsed from query_value.
       B) Single line: paste the full street address into Mailing Address / Mail Address / Site Address (e.g. "825 N HEATHROW DR") when that field exists — especially when Instructions mention mailing or when structured search may fail.
     * Set alternate_fills in JSON when both patterns are available on the same form (structured first, mailing_line as fallback).
   - Always match query_type to the field whose label, placeholder format, or attribute matches that type.
   - For address on forms with multiple street fields, return `fills` with one entry per visible input, OR `alternate_fills` with a single Mailing Address entry.

2. TABS & SECTION TOGGLES (Beacon / qPublic / Schneider):
   - **Never** set `tab_selector` to top navigation ("Results", "Sales Search", "Map", "Tax Estimator") — those leave the search form.
   - On the search page, multiple **accordion panels** (Owner, Location Address, Parcel Number, Prop ID) share one screen. Each has `..._btnHeaderToggle`.
   - Pick `input_selector` only from the panel matching `query_type` (`txtAddress` / aria "Location Address" for address; `txtParcel` for parcel).
   - `tab_selector` MUST be `#ctlBodyPane_ctlNN_btnHeaderToggle` where `NN` matches the chosen input's panel (`ctlBodyPane_ctl02_...` → `#ctlBodyPane_ctl02_btnHeaderToggle`).
   - Do NOT fill Owner / Prop ID when searching by address; do NOT fill Owner / Address when searching by parcel.

3. RADIO BUTTONS:
   - If the form uses radio buttons to choose query mode (e.g. radio with label 'Folio' vs 'Parcel Number'):
     * For parcel/folio query, return the selector of the matching radio button (e.g. input[type="radio"][value="folio"]).

4. INPUT SELECTOR:
   - Return a specific, visible CSS selector for the text field (e.g. input[placeholder="enter parcel number..."]).
   - NEVER select a radio button, checkbox, or hidden element as input_selector.

5. SUBMIT / SEARCH ACTIVATION:
   - ALWAYS return a concrete `submit_selector` when any in-form Search / Find / Submit control is visible in the snapshot or screenshot. Set `use_enter_key` to false. Do NOT set `submit_selector` to null when a Search button is on screen — tax collector and assessor sites often disable Enter (onkeydown blocks key 13) or only call submitForm() from a button click.
   - **Use snapshot selectors first:** If the page snapshot lists an element with kind `search_button` and text like "Search", set `submit_selector` to that element's `selector` field exactly. Do not substitute `button:has-text("Search")` unless the snapshot itself uses that pattern for a `<button>` tag.
   - **Input vs button tags:** County tax collector Search controls are frequently `<input type="submit" value="Search">`, `<input type="button" value="Search">`, or `<div id="search" role="button">Search</div>` calling `submitForm(0)`. Playwright's `button:has-text("Search")` does NOT match those. Prefer `#search`, `#id` from snapshot, `input[type="submit"][value="Search"]`, `input[type="button"][value="Search"]`, then `button:has-text("Search")` only as last resort.
   - Stacked qPublic/Schneider portals show several identical "Search" controls (Owner, Location Address, Parcel, etc.). Pick the btnSearch in the same BodyPane/panel as the input you filled — prefer `#...btnSearch` id selectors, never a bare `button:has-text("Search")` when multiple search buttons exist.
   - Tax collector unified forms (one screen with Owner Name, Location Address, Property Number, Roll Type, Tax Year): there is a single shared Search control (often yellow/gold) next to the dropdowns — use that control's snapshot selector, not sidebar links like "Search Tax Database".
   - NEVER select container elements (div, nav, header), social share buttons (Facebook, Twitter, LinkedIn), homepage links, or nav menu items whose text contains "Parcel Search" / "Property Search" / "Search Tax Database".
   - Only if the snapshot has zero search_button or submit input and the portal is a typeahead that submits on Enter, set use_enter_key to true and submit_selector to null.
   - NEVER invent or hallucinate selectors not present in the snapshot elements.

6. GIS MAP / FLORIDAPA POST-SEARCH (for extraction and documents):
   - After results load, click the matching parcel row or navigate to `PageTypeID=4` detail with KeyValue = parcel.
   - Open the **Parcel Details** tab (not GIS Map / Record Search) and wait until Owner / Situs / values appear (often inside an iframe).
   - On GIS map portals, never click toolbar **Print** (window.print); use Playwright PDF render after Parcel Details loads. On other sites, prefer **Print (PDF)** download links with href, not generic Print buttons.

7. TAX COLLECTOR POST-SEARCH (planning context):
   - After Search on multi-year tax bill portals, the same page may list links like `R - <property number> - <year>` (one per tax year in a results table). That is a successful search — not a failed submit.
   - Downstream automation opens **each** listed result in order, captures tax records/screenshots/PDFs, then returns to the results table for the next year. Plan `submit_selector` as `#search` when present; use a single `fills` entry for Property Number only.

8. COUNTY-TAXES.NET / MIAMI-DADE — INLINE DETAIL (critical for extraction, plan search only):
   - Phenix/Miami-Dade often skips a separate “results” page: one Search lands on the **tax account** with annual bill rows (2026 Annual Bill, 2025 Annual Bill, …), AMOUNT DUE, STATUS, Print (PDF). When the snapshot or screenshot already shows that grid, state in `reasoning` that **detail is already displayed** and keep `tab_selector` null — do not return selectors for Menu, Property Tax nav, or duplicate Search.
   - Do not confuse the slide-out **Menu** (Get Bills By Email, Property Tax, Reports) with search controls; never set `tab_selector` or `submit_selector` to those links.
   - **Public notifications** pop-up: not part of search; ignore its links for fill/submit; automation dismisses the X separately.
   - **Print (PDF)** in the ACTION column is for downloads, not navigation to “more detail” — never use as submit or tab.
   - parcel/address query: fill the unified search field once, click the form Search once. If the URL already contains an account segment and the bill table is visible, return minimal plan (no further clicks) so extraction reads the current page.
   - Wrong behavior to avoid: navigating to another property-tax route or menu item after bills are visible; leaving the account page without extracting AMOUNT DUE / Paid / Receipt data on screen.

9. PAGE STILL LOADING (heavy SPA — not a security barrier):
   - When bundles are downloading and search inputs are missing from the snapshot, keep **security_barrier_detected** false.
   - Set **input_selector**, **fills**, **submit_selector**, **tab_selector**, and **radio_selector** to null.
   - Return **playwright_diagnostic_steps** only: `wait`, `wait_for_load_state`, `wait_for_selector` (use **timeout_ms** up to 90000 on selectors from the partial snapshot or likely mount targets).
   - Execution runs those steps, re-analyzes the DOM, and asks you again for the full search plan (stored in recipes on success). Do not guess folio/parcel CSS before the field exists in the snapshot."""

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

    async def _plan_search_with_vision(
        self,
        page: Page,
        snapshot: dict[str, Any],
        qt: str,
        query_value: str,
        workflow_run_id: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Uses a live screenshot plus DOM snapshot so the LLM can locate fields and the real Search button."""
        if not llm_configured() or page.is_closed():
            return None
        try:
            png = await page.screenshot(full_page=True, type="png", timeout=15000)
        except Exception as shot_err:
            logger.warning("Search vision screenshot failed: %s", shot_err)
            return None
        if not png or len(png) < 500:
            return None

        portal_hint = (snapshot.get("url") or "").strip()
        if workflow_run_id:
            from app.browser.run_portal_context import get_run_portal_url

            portal_hint = portal_hint or (get_run_portal_url(workflow_run_id) or "")
        if portal_hint and (page.url or "").startswith("about:"):
            from app.browser.navigation import recover_blank_main_frame

            await recover_blank_main_frame(
                page,
                portal_hint,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            try:
                png = await page.screenshot(full_page=True, type="png", timeout=15000)
            except Exception as shot_err:
                logger.warning("Search vision screenshot retry failed: %s", shot_err)
                return None

        if workflow_run_id:
            try:
                from app.browser.browser_manager import get_browser_manager
                from app.browser.navigation import best_page_url

                bm = await get_browser_manager()
                await bm.log_session_activity(
                    action="vision_analyze",
                    description=f"AI vision analyzing search form for {qt} query",
                    node_id=node_id,
                    step_index=2,
                    status="COMPLETED",
                    url=best_page_url(page, portal_hint),
                )
            except Exception:
                pass

        b64 = base64.b64encode(png).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"
        user_payload: dict[str, Any] = {
            "query_type": qt,
            "query_value": query_value,
            "page": snapshot,
        }
        if snapshot.get("security_barrier_hint"):
            user_payload["security_barrier_hint"] = snapshot["security_barrier_hint"]
        user_text = json.dumps(user_payload)
        resp, provider = await chat_completions_create(
            create_kwargs={
                "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
                "messages": [
                    {"role": "system", "content": _ai_system_prompt()},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                        ],
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 600,
                "response_format": {"type": "json_object"},
            }
        )
        plan = json.loads(resp.choices[0].message.content or "{}")
        if _plan_indicates_security_barrier(plan):
            plan["query_type"] = qt
            plan["vision_provider"] = provider
            plan["playwright_diagnostic_steps"] = _sanitize_diagnostic_steps(
                plan.get("playwright_diagnostic_steps")
            )
            logger.warning("Vision plan: security barrier (%s)", plan.get("security_barrier_type"))
            return plan
        if plan.get("input_selector") or plan.get("fills"):
            plan["query_type"] = qt
            plan["vision_provider"] = provider
            logger.info("Vision search plan via %s: %s", provider, plan.get("reasoning"))
            return plan
        return None

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

        from app.browser.navigation import best_page_url

        return {
            "url": best_page_url(page),
            "title": await page.title() if not page.is_closed() else "",
            "elements": elements,
        }

    async def _selector_visible(self, page: Page, selector: str, timeout_ms: int = 800) -> bool:
        if not selector:
            return False
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for part in [selector, *selector.split(",")]:
                s_clean = part.strip()
                if not s_clean:
                    continue
                try:
                    loc = target_scope.locator(s_clean).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=timeout_ms):
                        return True
                except Exception:
                    continue
        return False

    async def _selector_attached(self, page: Page, selector: str) -> bool:
        if not selector:
            return False
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for part in [selector, *selector.split(",")]:
                s_clean = part.strip()
                if not s_clean:
                    continue
                try:
                    if await target_scope.locator(s_clean).first.count() > 0:
                        return True
                except Exception:
                    continue
        return False

    async def _page_has_property_search_form(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if "searchselect" in url or re.search(r"/property/search", url):
            return True
        if "/search" in url and "schneidercorp" not in url and "qpublic" not in url:
            return True
        try:
            body = (await page.locator("body").inner_text(timeout=3000) or "").lower()
            from app.browser.property_capture_prep import body_looks_like_search_selection_form

            if body_looks_like_search_selection_form(body):
                return True
            if "property search" in body and any(
                k in body for k in ("parcel id", "owner name", "real estate", "tangible", "property number")
            ):
                return True
        except Exception:
            pass
        return False

    async def _page_shows_tax_bill_result_list(self, page: Page, query_value: str) -> bool:
        """VisualGov-style post-search: property number links by tax year on the same page."""
        q_digits = re.sub(r"\D", "", query_value or "")
        parts: list[str] = []
        try:
            parts.append(await page.locator("body").inner_text(timeout=2500) or "")
        except Exception:
            pass
        body = "\n".join(parts).lower()
        if not body:
            return False
        if "view or pay a tax bill" in body or "click the property number below" in body:
            return True
        if "search selection" in body and q_digits and len(q_digits) >= 8:
            years = set(re.findall(r"\b20\d{2}\b", body, re.I))
            if len(years) >= 2 and q_digits in re.sub(r"\D", "", body):
                return True
        if q_digits and len(q_digits) >= 8:
            esc = re.escape(query_value.strip()).replace(r"\-", r"[\s\-]*")
            if re.search(rf"\bR\s*-\s*{esc}\s*-\s*20\d{{2}}\b", body, re.I):
                return True
            if query_value.strip().lower() in body and re.search(r"\bR\s*-\s*[\d\-]+\s*-\s*20\d{2}\b", body, re.I):
                if q_digits in re.sub(r"\D", "", body):
                    return True
        return False

    async def _search_form_still_needs_submit(self, page: Page, query_value: str) -> bool:
        """True when a visible in-form Search control remains after fill (Playwright skipped real submit)."""
        from app.browser.property_capture_prep import body_looks_like_search_selection_form

        try:
            if await self._page_shows_property_detail(page, query_value, "parcel"):
                return False
        except Exception:
            pass

        bodies: list[str] = []
        try:
            bodies.append(await page.locator("body").inner_text(timeout=2500) or "")
        except Exception:
            pass
        for frame in page.frames:
            try:
                bodies.append(await frame.locator("body").inner_text(timeout=1200) or "")
            except Exception:
                continue
        if any(body_looks_like_search_selection_form(b) for b in bodies):
            return True

        submit_selectors = (
            '#search',
            'div#search[role="button"]',
            '[role="button"]:has-text("Search")',
            'input[type="submit"][value*="Search" i]',
            'input[type="button"][value*="Search" i]',
            'input[value="Search" i]',
            'button:has-text("Search")',
        )
        for scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for sel in submit_selectors:
                try:
                    loc = scope.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=500):
                        if await self._page_has_property_search_form(page):
                            return True
                        return True
                except Exception:
                    continue
        return False

    def _url_looks_like_stale_search(self, url: str) -> bool:
        u = (url or "").lower().strip()
        if not u:
            return True
        if "searchselect" in u or re.search(r"/property/search", u):
            return True
        if re.search(r"/search/?(\?id=)?$", u) or u.endswith("/search#parcel"):
            return True
        if "search?id=" in u:
            tail = u.split("search?id=", 1)[-1].split("&")[0].strip()
            if len(tail) < 2:
                return True
        return False

    async def _stale_search_portal_page(self, page: Page) -> bool:
        if not self._url_looks_like_stale_search(page.url or ""):
            return False
        try:
            body = (await page.locator("body").inner_text(timeout=2500) or "").lower()
            from app.browser.property_capture_prep import body_looks_like_search_selection_form

            if body_looks_like_search_selection_form(body):
                return True
            if "property search" in body and ("parcel id" in body or "owner name" in body):
                detail_markers = (
                    "land value",
                    "assessed value",
                    "just value",
                    "legal description",
                    "situs address",
                    "property use code",
                )
                if not any(m in body for m in detail_markers):
                    return True
        except Exception:
            pass
        return self._url_looks_like_stale_search(page.url or "")

    async def _page_shows_property_detail(self, page: Page, query_value: str, qt: str) -> bool:
        url = page.url or ""
        try:
            from app.browser.property_capture_prep import page_is_search_results_listing

            if await page_is_search_results_listing(page):
                return False
        except Exception:
            pass
        try:
            from app.extraction.tax_collector_reports import (
                is_tax_collector_portal,
                page_shows_tax_account_summary,
            )

            if is_tax_collector_portal(url) or any(
                is_tax_collector_portal(f.url or "") for f in page.frames
            ):
                if await page_shows_tax_account_summary(page, query_value):
                    return True
        except Exception:
            pass
        try:
            from app.browser.result_selector_ai import _query_match_tokens

            body = (await page.locator("body").inner_text(timeout=2000) or "").upper()
            tokens = _query_match_tokens(query_value, qt)
            if not tokens:
                return False
            hits = sum(1 for t in tokens if t.upper() in body)
            if hits < min(2, max(1, len(tokens))):
                return False
            markers = (
                "ASSESSED",
                "OWNER",
                "LEGAL DESCRIPTION",
                "JUST VALUE",
                "SITUS",
                "PROPERTY USE",
                "ACCOUNT SUMMARY",
                "AMOUNT DUE",
            )
            if any(m in body for m in markers):
                return True
        except Exception:
            pass
        return False

    async def _wait_for_property_detail_view(
        self,
        page: Page,
        query_value: str,
        qt: str,
        before_url: str,
        *,
        timeout_s: float = 14.0,
    ) -> bool:
        import time as _time

        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            if await self._page_shows_property_detail(page, query_value, qt):
                return True
            cur = page.url or ""
            if cur != before_url and not self._url_looks_like_stale_search(cur):
                if await self._page_shows_property_detail(page, query_value, qt):
                    return True
            await page.wait_for_timeout(500)
        return await self._page_shows_property_detail(page, query_value, qt)

    async def _prepare_single_field_search(self, page: Page, plan: dict[str, Any]) -> None:
        """Clear other basic-search fields so portals that allow one criterion accept the filled value."""
        input_sel = (
            (plan.get("fills") or [{}])[0].get("selector")
            or plan.get("input_selector")
            or ""
        ).strip()
        if not input_sel:
            return
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                await target_scope.evaluate(SINGLE_FIELD_SEARCH_PREP_JS, input_sel)
                return
            except Exception:
                continue

    async def _retry_panel_search_submit(self, page: Page, plan: dict[str, Any]) -> bool:
        input_sel = (
            (plan.get("fills") or [{}])[0].get("selector")
            or plan.get("input_selector")
            or ""
        ).strip()
        if not input_sel:
            return False
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                if await target_scope.evaluate(PAIR_SUBMIT_CLICK_JS, input_sel):
                    return True
            except Exception:
                continue
        submit_sel = (plan.get("submit_selector") or "").strip()
        if submit_sel:
            if await self._click_selector_on_page(page, submit_sel):
                return True
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                if await target_scope.evaluate(INVOKE_PORTAL_SUBMIT_JS):
                    return True
            except Exception:
                continue
        return False

    async def _activate_query_mode_tab(
        self,
        page: Page,
        plan: dict[str, Any],
        qt: str,
    ) -> bool:
        """Click the search-mode control (Parcel ID / Address / Owner) so its input becomes visible."""
        primary_sel = (
            (plan.get("fills") or [{}])[0].get("selector")
            or plan.get("input_selector")
            or ""
        ).strip()
        if primary_sel and await self._selector_visible(page, primary_sel, 1500):
            return True

        candidates: list[str] = []
        tab_sel = (plan.get("tab_selector") or "").strip()
        if tab_sel:
            candidates.append(tab_sel)
        snap = plan.get("_snapshot_elements") or []
        picked = _pick_query_mode_tab(snap, qt)
        if picked:
            candidates.append(_canonical_element_selector(picked))
        label_pool = {
            "parcel": ("Parcel ID", "Parcel Number", "Parcel", "Folio", "PIN"),
            "address": ("Address", "Site Address", "Location Address"),
            "owner": ("Owner Name", "Owner"),
        }
        for label in label_pool.get(qt, label_pool["parcel"]):
            candidates.extend(
                [
                    f"button:has-text('{label}')",
                    f"a:has-text('{label}')",
                    f"[role='tab']:has-text('{label}')",
                ]
            )
        seen: set[str] = set()
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if not await self._click_selector_on_page(page, cand):
                continue
            await page.wait_for_timeout(700)
            if primary_sel and await self._selector_visible(page, primary_sel, 2000):
                plan["tab_selector"] = cand
                return True
        return False

    async def validate_plan_on_page(self, page: Page, plan: dict[str, Any]) -> dict[str, Any]:
        """Verify planned selectors exist on the live page before Playwright execution."""
        missing: list[str] = []
        visible: list[str] = []

        for key in ("tab_selector", "radio_selector"):
            sel = plan.get(key)
            if not sel:
                continue
            if await self._selector_visible(page, str(sel)):
                visible.append(key)
            else:
                missing.append(key)

        fills = plan.get("fills") or []
        if not fills and plan.get("input_selector"):
            fills = [{"selector": plan["input_selector"]}]
        fill_visible = 0
        for fill in fills:
            sel = (fill.get("selector") or "").strip()
            if not sel:
                continue
            if await self._selector_visible(page, sel):
                fill_visible += 1
                visible.append(f"fill:{sel}")
            else:
                missing.append(f"fill:{sel}")

        submit_sel = plan.get("submit_selector")
        submit_visible = await self._selector_visible(page, str(submit_sel)) if submit_sel else False
        if submit_visible:
            visible.append("submit_selector")
        elif submit_sel:
            missing.append("submit_selector")

        qt = (plan.get("query_type") or "parcel").lower()
        forbidden_fill = False
        if qt == "address":
            for fill in fills:
                sel = (fill.get("selector") or "").strip()
                if not sel:
                    continue
                match = next(
                    (e for e in (plan.get("_snapshot_elements") or []) if e.get("selector") == sel),
                    None,
                )
                if not match:
                    ph = _placeholder_from_selector(sel)
                    if ph:
                        want = _normalize_placeholder(ph)
                        match = next(
                            (
                                e
                                for e in (plan.get("_snapshot_elements") or [])
                                if e.get("kind") == "input"
                                and _normalize_placeholder(e.get("placeholder") or "") == want
                            ),
                            {"kind": "input", "placeholder": ph, "label": "", "selector": sel},
                        )
                if match and _forbidden_input_for_query(match, "address"):
                    forbidden_fill = True
                    missing.append(f"forbidden_field:{sel}")
                if "altkey" in sel.lower() or "alt_key" in sel.lower():
                    forbidden_fill = True
                    missing.append(f"forbidden_field:{sel}")
        elif qt == "parcel":
            for fill in fills:
                sel = (fill.get("selector") or "").strip()
                if not sel:
                    continue
                if any(k in sel.lower() for k in ("altkey", "alt_key", "alt-key")):
                    forbidden_fill = True
                    missing.append(f"forbidden_field:{sel}")
                    continue
                match = next(
                    (e for e in (plan.get("_snapshot_elements") or []) if e.get("selector") == sel),
                    None,
                )
                if match and _forbidden_input_for_query(match, "parcel"):
                    forbidden_fill = True
                    missing.append(f"forbidden_field:{sel}")

        fill_attached = 0
        for fill in fills:
            sel = (fill.get("selector") or "").strip()
            if sel and await self._selector_attached(page, sel):
                fill_attached += 1

        ok = (
            not forbidden_fill
            and (submit_visible or plan.get("use_enter_key"))
            and (
                fill_visible > 0
                or (fill_attached > 0 and bool(plan.get("tab_selector")))
            )
        )
        return {
            "ok": ok,
            "missing": missing,
            "visible": visible,
            "fill_fields_visible": fill_visible,
            "submit_visible": submit_visible,
            "forbidden_field": forbidden_fill,
        }

    async def run_full_ai_search(
        self,
        page: Page,
        query_type: str,
        query_value: str,
        portal_url: str,
        county_name: str,
        record_type: str,
        workflow_run_id: str | None = None,
        node_id: str | None = None,
        target_county_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Open portal → full DOM + vision analysis → validate plan → execute Playwright steps.
        Falls back to imperative execute_plan if generated steps fail.
        """
        async def _log(action: str, description: str, step_idx: int, status: str = "COMPLETED") -> None:
            if not workflow_run_id:
                return
            try:
                from app.browser.browser_manager import get_browser_manager
                from app.browser.navigation import best_page_url

                bm = await get_browser_manager()
                live_url = (
                    best_page_url(page, portal_url)
                    if page and not page.is_closed()
                    else portal_url or None
                )
                await bm.log_session_activity(
                    action=action,
                    description=description,
                    node_id=node_id,
                    step_index=step_idx,
                    status=status,
                    url=live_url,
                )
            except Exception:
                pass

        from app.browser.navigation import best_page_url

        portal_url = (portal_url or "").strip()
        if workflow_run_id and portal_url:
            from app.browser.run_portal_context import set_run_portal_url

            set_run_portal_url(str(workflow_run_id), portal_url)
        if portal_url:
            from app.browser.navigation import ensure_portal_page

            portal_ready = await ensure_portal_page(
                page,
                portal_url,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                wait_for_mount=True,
            )
            if not portal_ready:
                await _log(
                    "navigate",
                    f"Could not open county portal: {portal_url}",
                    0,
                    status="FAILED",
                )
                return {
                    "success": False,
                    "plan": {},
                    "record": {
                        "search_failed": True,
                        "failure_reason": "portal_navigation_failed",
                        "portal_url": portal_url,
                        "query_value": query_value,
                    },
                    "detail_opened": False,
                    "downloaded_documents": [],
                }
            await _log("navigate", f"Opened county portal: {page.url}", 0)

        await _log("analyze_page", f"AI analyzing search portal DOM: {page.url}", 1)

        try:
            await ensure_schneider_search_workspace(page)
        except Exception:
            pass

        try:
            from app.browser.modal_detector import detect_and_dismiss_modal_ai, dismiss_blocking_overlays_heuristic
            from app.extraction.tax_collector_reports import ensure_tax_collector_search_page, is_tax_collector_portal

            await dismiss_blocking_overlays_heuristic(page)
            await detect_and_dismiss_modal_ai(page, workflow_run_id=workflow_run_id, node_id=node_id)
            if portal_url:
                from app.browser.navigation import ensure_portal_page

                await ensure_portal_page(
                    page,
                    portal_url,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                    wait_for_mount=False,
                )
            if is_tax_collector_portal(page.url) or any(
                is_tax_collector_portal(f.url or "") for f in page.frames
            ):
                await ensure_tax_collector_search_page(
                    page,
                    query_value=query_value,
                    portal_search_url=portal_url,
                )
                await dismiss_blocking_overlays_heuristic(page)
        except Exception as prep_err:
            logger.debug("Portal prep before AI snapshot: %s", prep_err)

        if portal_url:
            from app.browser.navigation import recover_blank_main_frame
            from app.browser.tax_verification_challenge import (
                attempt_county_taxes_human_verification,
                is_county_taxes_verification_page,
            )

            await recover_blank_main_frame(
                page,
                portal_url,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            if await is_county_taxes_verification_page(page):
                await _log(
                    "security_barrier",
                    "County-taxes human verification; running automated click",
                    1,
                    status="RUNNING",
                )
                await attempt_county_taxes_human_verification(page)
                await recover_blank_main_frame(
                    page,
                    portal_url,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                )

        snapshot = await self.extract_snapshot(page)
        plan_seed: dict[str, Any] = {"_tax_portal_search_url": portal_url or page.url or ""}

        sec_hint = await _page_security_hint(page)
        if sec_hint:
            snapshot["security_barrier_hint"] = sec_hint

        plan = await self.plan_search(
            snapshot,
            query_type,
            query_value,
            page=page,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
        )
        plan.update(plan_seed)
        plan.setdefault("_tax_portal_search_url", portal_url or page.url or "")

        for _prep_round in range(2):
            if not _plan_needs_ai_page_preparation(plan):
                break
            diag_prep = _sanitize_diagnostic_steps(plan.get("playwright_diagnostic_steps"))
            if not diag_prep:
                break
            await _log(
                "prepare_page",
                f"Running {len(diag_prep)} AI Playwright preparation step(s) while portal loads",
                1,
            )
            try:
                await _run_ai_page_preparation_steps(
                    page, diag_prep, workflow_run_id, node_id
                )
            except Exception as prep_err:
                logger.warning("AI page preparation steps failed: %s", prep_err)
                await _log(
                    "prepare_page",
                    f"AI preparation steps error: {prep_err}",
                    1,
                    status="WARNING",
                )
            await page.wait_for_timeout(600)
            snapshot = await self.extract_snapshot(page)
            sec_hint = await _page_security_hint(page)
            if sec_hint:
                snapshot["security_barrier_hint"] = sec_hint
            plan = await self.plan_search(
                snapshot,
                query_type,
                query_value,
                page=page,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            plan.update(plan_seed)
            plan.setdefault("_tax_portal_search_url", portal_url or page.url or "")

        if _plan_indicates_security_barrier(plan):
            diag = plan.get("playwright_diagnostic_steps") or DEFAULT_SECURITY_DIAGNOSTIC_STEPS
            barrier_type = plan.get("security_barrier_type") or "unknown"
            security_plan = dict(plan)
            from app.browser.security_detector import SecurityDetectionResult
            from app.services.security_manual_intervention import (
                is_manual_intervention_enabled_for_run,
                try_manual_security_resolution,
            )

            sec_result = SecurityDetectionResult(
                is_detected=True,
                event_type=barrier_type,
                url=best_page_url(page, portal_url) if portal_url else page.url,
                reason=plan.get("reasoning") or f"Security barrier ({barrier_type})",
            )
            barrier_cleared = False
            if await is_manual_intervention_enabled_for_run(workflow_run_id):
                await _log(
                    "security_barrier",
                    f"Security barrier ({barrier_type}); waiting for manual verification",
                    2,
                    status="RUNNING",
                )
                barrier_cleared = await try_manual_security_resolution(
                    page,
                    sec_result,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                    portal_url=portal_url,
                )
            else:
                await _log(
                    "security_barrier",
                    f"Security barrier ({barrier_type}); running Playwright challenge steps",
                    2,
                    status="RUNNING",
                )
                try:
                    await _run_security_diagnostic_steps(
                        page, diag, workflow_run_id, node_id, portal_url=portal_url
                    )
                except Exception as diag_err:
                    logger.warning("Security barrier steps error: %s", diag_err)
                await page.wait_for_timeout(1200)
                if portal_url:
                    from app.browser.navigation import recover_blank_main_frame

                    await recover_blank_main_frame(
                        page,
                        portal_url,
                        workflow_run_id=workflow_run_id,
                        node_id=node_id,
                    )
                barrier_cleared = not bool(await _page_security_hint(page))

            if not barrier_cleared:
                await _log(
                    "security_barrier",
                    f"Security barrier still present ({barrier_type})",
                    2,
                    status="FAILED",
                )
                return {
                    "success": False,
                    "plan": security_plan,
                    "record": {
                        "search_failed": True,
                        "failure_reason": "security_barrier",
                        "security_barrier_type": barrier_type,
                        "query_value": query_value,
                    },
                    "playwright_diagnostic_steps": _sanitize_diagnostic_steps(diag),
                    "detail_opened": False,
                    "downloaded_documents": [],
                }
            await _log(
                "security_barrier",
                "Security barrier cleared; re-planning county search",
                2,
                status="COMPLETED",
            )
            try:
                from app.browser.modal_detector import (
                    detect_and_dismiss_modal_ai,
                    dismiss_blocking_overlays_heuristic,
                )

                await dismiss_blocking_overlays_heuristic(page)
                await detect_and_dismiss_modal_ai(page, workflow_run_id=workflow_run_id, node_id=node_id)
            except Exception:
                pass
            snapshot = await self.extract_snapshot(page)
            plan = await self.plan_search(
                snapshot,
                query_type,
                query_value,
                page=page,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            plan.update(plan_seed)
            plan["security_barrier_recovery"] = security_plan
            plan.setdefault("_tax_portal_search_url", portal_url or page.url or "")
            if _plan_indicates_security_barrier(plan):
                await _log(
                    "security_barrier",
                    "Search portal still behind a security barrier after recovery",
                    2,
                    status="FAILED",
                )
                return {
                    "success": False,
                    "plan": plan,
                    "record": {
                        "search_failed": True,
                        "failure_reason": "security_barrier",
                        "security_barrier_type": plan.get("security_barrier_type") or barrier_type,
                        "query_value": query_value,
                    },
                    "playwright_diagnostic_steps": _sanitize_diagnostic_steps(diag),
                    "detail_opened": False,
                    "downloaded_documents": [],
                }

        plan["_snapshot_elements"] = snapshot.get("elements", [])
        qt_plan = plan.get("query_type", query_type)
        if await self._activate_query_mode_tab(page, plan, qt_plan):
            snapshot = await self.extract_snapshot(page)
            plan = refine_plan_from_snapshot(plan, snapshot, qt_plan, query_value)
            plan["_snapshot_elements"] = snapshot.get("elements", [])
            remap_plan_fill_selectors(plan, snapshot.get("elements", []))

        validation = await self.validate_plan_on_page(page, plan)
        plan["validation"] = validation
        await _log(
            "validate_plan",
            (
                "Validated Playwright plan selectors on live page"
                if validation.get("ok")
                else f"Plan validation issues: {', '.join(validation.get('missing', [])[:5])}"
            ),
            2,
            status="COMPLETED" if validation.get("ok") else "WARNING",
        )

        if not validation.get("ok"):
            snapshot = await self.extract_snapshot(page)
            plan = refine_plan_from_snapshot(plan, snapshot, plan.get("query_type", query_type), query_value)
            plan["_snapshot_elements"] = snapshot.get("elements", [])
            remap_plan_fill_selectors(plan, snapshot.get("elements", []))
            validation = await self.validate_plan_on_page(page, plan)
            plan["validation"] = validation

        from app.browser.navigation import page_needs_portal_navigation

        portal_target = portal_url or page.url or ""
        include_navigate = page_needs_portal_navigation(page, portal_target)
        runtime_steps = build_runtime_steps_from_plan(
            plan,
            include_navigate=include_navigate,
            portal_url=portal_target,
        )
        plan["runtime_steps"] = runtime_steps
        plan["portal_url"] = portal_url
        plan.setdefault("_tax_portal_search_url", portal_url or page.url or "")
        await _log(
            "generate_steps",
            f"Generated {len(runtime_steps)} Playwright instruction(s) for {plan.get('query_type', query_type)} search",
            3,
        )

        qt = plan.get("query_type", query_type)
        parsed_address = _parse_street_address(query_value) if qt == "address" else {}
        context_vars = {
            "search_url": portal_url or page.url,
            "active_query_type": qt,
            "input": {
                "parcel_id": query_value if qt == "parcel" else "",
                "address": query_value if qt == "address" else "",
                "owner_name": query_value if qt == "owner" else "",
                "address_street_number": parsed_address.get("street_number", ""),
                "address_direction": parsed_address.get("direction", ""),
                "address_street_name": parsed_address.get("street_name", ""),
                "address_suffix": parsed_address.get("suffix", ""),
            },
        }

        playwright_executed = False
        if validation.get("ok") and runtime_steps:
            try:
                from app.browser.step_runner import PlaywrightStepRunner, SecurityChallengeException

                runner = PlaywrightStepRunner(page, str(workflow_run_id or ""), str(node_id or ""))
                await runner.execute_steps(runtime_steps, context_vars)
                playwright_executed = True
                await _log("execute_steps", "Executed AI-generated Playwright search instructions", 4)
            except SecurityChallengeException as sec_exc:
                from app.services.security_manual_intervention import try_manual_security_resolution

                logger.warning("Playwright steps hit security barrier: %s", sec_exc.result.reason)
                cleared = await try_manual_security_resolution(
                    page,
                    sec_exc.result,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                    portal_url=portal_url,
                )
                if cleared:
                    try:
                        runner = PlaywrightStepRunner(page, str(workflow_run_id or ""), str(node_id or ""))
                        await runner.execute_steps(runtime_steps, context_vars)
                        playwright_executed = True
                        await _log("execute_steps", "Resumed Playwright steps after manual verification", 4)
                    except SecurityChallengeException as sec_again:
                        await _log(
                            "security_barrier",
                            sec_again.result.reason or "Security barrier after manual verification",
                            4,
                            status="FAILED",
                        )
                        return {
                            "success": False,
                            "plan": plan,
                            "record": {
                                "search_failed": True,
                                "failure_reason": "security_barrier",
                                "query_value": query_value,
                            },
                            "detail_opened": False,
                            "downloaded_documents": [],
                        }
                else:
                    await _log(
                        "security_barrier",
                        sec_exc.result.reason or "Security barrier",
                        4,
                        status="FAILED",
                    )
                    return {
                        "success": False,
                        "plan": plan,
                        "record": {
                            "search_failed": True,
                            "failure_reason": "security_barrier",
                            "query_value": query_value,
                        },
                        "detail_opened": False,
                        "downloaded_documents": [],
                    }
            except Exception as pw_err:
                logger.warning("AI Playwright step execution failed (%s). Falling back to direct plan executor.", pw_err)
                await _log("execute_steps", f"Playwright steps failed, using direct executor: {pw_err}", 4, status="WARNING")

        ai_res: dict[str, Any]
        fill_in_plan = bool(plan.get("fills") or plan.get("input_selector"))
        pw_fill_steps = any(
            (s.get("action") or "").lower() in ("fill", "type", "fill_if_visible")
            for s in (runtime_steps or [])
        )
        skip_repeat_search = playwright_executed and validation.get("ok") and (fill_in_plan or pw_fill_steps)
        if skip_repeat_search and not page.is_closed():
            try:
                if await self._search_form_still_needs_submit(page, query_value):
                    skip_repeat_search = False
                    logger.info(
                        "Playwright steps ran but in-form Search still visible; running submit in plan executor."
                    )
            except Exception as need_err:
                logger.debug("Search-form submit check: %s", need_err)
        if skip_repeat_search and not page.is_closed():
            try:
                from app.extraction.tax_collector_reports import (
                    is_tax_collector_portal,
                    tax_portal_body_matches_query,
                    ensure_tax_collector_search_page,
                )

                if is_tax_collector_portal(page.url) or any(
                    is_tax_collector_portal(f.url or "") for f in page.frames
                ):
                    if not await tax_portal_body_matches_query(page, query_value):
                        logger.info(
                            "Tax portal shows a different record than %s; forcing fresh search.",
                            query_value[:80],
                        )
                        await ensure_tax_collector_search_page(
                            page,
                            query_value=query_value,
                            portal_search_url=plan.get("_tax_portal_search_url") or portal_url,
                        )
                        skip_repeat_search = False
            except Exception as tax_mismatch_err:
                logger.debug("Tax parcel mismatch check: %s", tax_mismatch_err)
        if skip_repeat_search:
            ai_res = await self.execute_plan(
                page,
                plan,
                query_value,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                snapshot=snapshot,
                skip_fill_and_submit=True,
            )
        else:
            ai_res = await self.execute_plan(
                page,
                plan,
                query_value,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                snapshot=snapshot,
            )

        if self.db and target_county_id:
            try:
                from app.services.portal_search_orchestrator import validate_property_matches_query

                record_for_validation = (ai_res or {}).get("record") if isinstance(ai_res, dict) else {}
                if not record_for_validation:
                    record_for_validation = getattr(self, "_last_extracted_record", {}) or {}
                if isinstance(record_for_validation, dict):
                    record_for_validation = dict(record_for_validation)
                    record_for_validation.setdefault(
                        "_source_url",
                        page.url if (page and not page.is_closed()) else (portal_url or ""),
                    )

                live_url = page.url if (page and not page.is_closed()) else (portal_url or "")
                prop_val = validate_property_matches_query(
                    record_for_validation, qt, query_value, current_url=live_url
                )
                search_executed = bool(playwright_executed or (ai_res or {}).get("success"))
                recipe_steps_ok = bool(validation.get("ok") or runtime_steps)
                if search_executed and (prop_val.valid or (recipe_steps_ok and (ai_res or {}).get("success"))):
                    logger.info(
                        "recipe_save_approved mode=verified_discovery county=%s steps=%d",
                        target_county_id,
                        len(runtime_steps or []),
                    )
                    await self.save_discovered_recipe(
                        county_source_id=uuid.UUID(str(target_county_id)),
                        record_type=record_type,
                        county_name=county_name,
                        portal_url=portal_url or page.url,
                        dom_snapshot=snapshot,
                        user_id=None,
                        plan=plan,
                    )
                else:
                    logger.warning(
                        "recipe_save_skipped reason=property_validation_failed identity=%s match=%s reasons=%s",
                        prop_val.identity_ok,
                        prop_val.match_ok,
                        prop_val.reasons,
                    )
            except Exception as save_err:
                logger.warning("Could not persist AI Playwright recipe: %s", save_err)

        ai_res["plan"] = plan
        ai_res["playwright_steps"] = runtime_steps
        ai_res["validation"] = validation
        return ai_res

    async def plan_search(
        self,
        snapshot: dict[str, Any],
        query_type: str,
        query_value: str,
        page: Page | None = None,
        workflow_run_id: str | None = None,
        node_id: str | None = None,
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

        if snapshot.get("security_barrier_hint") and not page:
            return _security_barrier_plan(
                barrier_type="cloudflare",
                reason=str(snapshot["security_barrier_hint"]),
                query_type=qt,
            )

        if page:
            sec_hint = snapshot.get("security_barrier_hint") or await _page_security_hint(page)
            if sec_hint:
                snapshot["security_barrier_hint"] = sec_hint
                if not llm_configured():
                    return _security_barrier_plan(
                        barrier_type="cloudflare",
                        reason=sec_hint,
                        query_type=qt,
                    )

        if page and llm_configured():
            try:
                vision_plan = await self._plan_search_with_vision(
                    page, snapshot, qt, query_value, workflow_run_id, node_id
                )
                if vision_plan:
                    return refine_plan_from_snapshot(vision_plan, snapshot, qt, query_value)
            except Exception as vision_err:
                logger.warning("Vision search planning failed (%s). Falling back to DOM/text LLM.", vision_err)

        # 1. Primary: Dynamic AI LLM Planning on live page snapshot
        if llm_configured():
            try:
                user_payload = {
                    "query_type": qt,
                    "query_value": query_value,
                    "page": snapshot,
                }
                if snapshot.get("security_barrier_hint"):
                    user_payload["security_barrier_hint"] = snapshot["security_barrier_hint"]
                resp, provider = await chat_completions_create(
                    create_kwargs={
                        "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
                        "messages": [
                            {"role": "system", "content": _ai_system_prompt()},
                            {"role": "user", "content": json.dumps(user_payload)},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                        "response_format": {"type": "json_object"},
                    }
                )
                plan = json.loads(resp.choices[0].message.content or "{}")
                if _plan_indicates_security_barrier(plan):
                    plan["query_type"] = qt
                    plan["playwright_diagnostic_steps"] = _sanitize_diagnostic_steps(
                        plan.get("playwright_diagnostic_steps")
                    )
                    logger.warning("LLM plan: security barrier (%s)", plan.get("security_barrier_type"))
                    return plan
                if plan.get("input_selector") or plan.get("fills"):
                    plan["query_type"] = qt
                    logger.info("AI LLM dynamically planned search via %s: %s", provider, plan.get("reasoning"))
                    return refine_plan_from_snapshot(plan, snapshot, qt, query_value)
            except Exception as llm_err:
                logger.warning("LLM search planning failed: %s. Using heuristic/deterministic planner.", llm_err)

        # 2. Dynamic Heuristic Matching on DOM Elements
        elements = snapshot.get("elements", [])
        tabs = [e for e in elements if e.get("kind") == "tab"]
        radios = [e for e in elements if e.get("kind") == "radio"]
        inputs = [e for e in elements if e.get("kind") == "input"]
        buttons = _collect_submit_candidates_from_elements(elements)

        target_tab_sel: str | None = None
        target_tab_panel: str | None = None
        target_radio_sel: str | None = None
        target_input_sel: str | None = None
        target_submit_sel: str | None = None
        stacked = _is_stacked_qpublic_portal(snapshot)

        if not stacked:
            if qt == "parcel":
                for r in radios:
                    lbl = (r.get("label") or "").lower()
                    val = (r.get("value") or "").lower()
                    if "folio" in lbl or "folio" in val or "parcel" in lbl or "pin" in lbl:
                        target_radio_sel = r.get("selector")
                        break
                for t in tabs:
                    if _is_portal_main_nav_tab(t):
                        continue
                    text = (t.get("text") or "").lower()
                    href = (t.get("href") or "").lower()
                    if any(k in text or k in href for k in ["parcel", "folio", "pin", "strap", "apn"]):
                        target_tab_sel = t.get("selector")
                        break
            elif qt == "address":
                for t in tabs:
                    if _is_portal_main_nav_tab(t):
                        continue
                    text = (t.get("text") or "").lower()
                    href = (t.get("href") or "").lower()
                    if any(k in text or k in href for k in ["address", "site", "street", "location"]):
                        if "search" in text or "property" in text:
                            target_tab_sel = t.get("selector")
                            break
            else:
                for t in tabs:
                    if _is_portal_main_nav_tab(t):
                        continue
                    text = (t.get("text") or "").lower()
                    if "owner" in text and "search" in text:
                        target_tab_sel = t.get("selector")
                        break

        heuristic_fills = build_search_fills(snapshot, qt, query_value)
        base_plan: dict[str, Any] = {
            "query_type": qt,
            "tab_selector": None if stacked else target_tab_sel,
            "tab_panel": target_tab_panel,
            "radio_selector": target_radio_sel,
            "input_selector": None,
            "submit_selector": None,
            "confidence": "medium",
            "reasoning": "DOM snapshot scored inputs by query type and panel section (Beacon/qPublic accordion).",
        }
        if heuristic_fills:
            for fill in heuristic_fills:
                hit = next((e for e in inputs if e.get("selector") == fill.get("selector")), None)
                if hit:
                    fill["selector"] = _canonical_element_selector(hit)
            base_plan["fills"] = heuristic_fills
            base_plan["input_selector"] = heuristic_fills[0]["selector"]
        else:
            allowed = _filter_inputs_for_query(inputs, qt)
            min_s = 40.0 if qt == "address" else 30.0 if qt == "parcel" else 20.0
            best_input = _pick_best_element(allowed, lambda e: _score_input_for_query(e, qt), min_score=min_s)
            if best_input:
                target_input_sel = _canonical_element_selector(best_input)
                base_plan["input_selector"] = target_input_sel
                base_plan["fills"] = [
                    {"selector": target_input_sel, "value": _normalize_query_value(qt, query_value)}
                ]

        paired_submit = _pick_submit_for_search_context(buttons, base_plan, elements, qt)
        if paired_submit:
            base_plan["submit_selector"] = _canonical_element_selector(paired_submit)
        return refine_plan_from_snapshot(base_plan, snapshot, qt, query_value)

    async def _select_option_on_page(self, page: Page, selector: str, value: str) -> bool:
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for s in [selector, *selector.split(",")]:
                s_clean = s.strip()
                if not s_clean:
                    continue
                try:
                    loc = target_scope.locator(s_clean).first
                    if await loc.count() == 0:
                        continue
                    try:
                        await loc.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    if value == "__auto_tax_year__":
                        meta = await target_scope.evaluate(PICK_TAX_YEAR_JS)
                        preferred = int(meta.get("preferred") or meta.get("nowYear") or 2026)
                        ok = await loc.evaluate(
                            """(el, preferredYear) => {
                                const opts = Array.from(el.options || []);
                                if (!opts.length) return false;
                                let best = null;
                                let bestY = -1;
                                for (const o of opts) {
                                    const y = parseInt((o.value || o.textContent || '').trim(), 10);
                                    if (!y || Number.isNaN(y)) continue;
                                    if (y > preferredYear) continue;
                                    if (y > bestY) { bestY = y; best = o; }
                                }
                                if (!best && opts.length) best = opts[0];
                                if (!best) return false;
                                el.value = best.value;
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }""",
                            preferred,
                        )
                        return bool(ok)
                    for pick in [dict(label=value), dict(value=value)]:
                        try:
                            await loc.select_option(**pick, timeout=2000)
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
        return False

    async def _apply_fill_entry(self, page: Page, fill: dict[str, Any], query_value: str) -> bool:
        sel = (fill.get("selector") or "").strip()
        val = (fill.get("value") or "").strip()
        if not sel:
            return False
        control = (fill.get("control") or "input").lower()
        role = fill.get("field_role") or ""
        if role == "street_prefix_dir" and val:
            val = val.upper()
        if control == "select" or role == "tax_year":
            return await self._select_option_on_page(page, sel, val or "__auto_tax_year__")
        if not val or (isinstance(val, str) and val.startswith("{{")):
            parsed = _parse_street_address(query_value)
            role_to_key = {
                "street_number": "street_number",
                "street_prefix_dir": "direction",
                "street_name": "street_name",
                "suffix": "suffix",
            }
            key = role_to_key.get(role or "")
            if key and parsed.get(key):
                val = parsed[key]
            elif role == "mailing_line":
                val = _full_mailing_line(query_value)
        return await self._fill_selector_on_page(page, sel, val or "")

    _STRUCTURED_ADDRESS_ROLES = frozenset({"street_number", "street_name", "street_prefix_dir", "suffix"})

    async def _clear_fill_field(self, page: Page, fill: dict[str, Any]) -> None:
        sel = (fill.get("selector") or "").strip()
        if not sel:
            return
        clear_js = """(el) => {
            const tag = el.tagName.toUpperCase();
            if (tag === 'SELECT') {
                const opts = Array.from(el.options || []);
                const blank = opts.find(o => !(o.value || '').trim()) || opts[0];
                if (blank) {
                    el.value = blank.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return;
            }
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for s in [sel, *sel.split(",")]:
                s_clean = s.strip()
                if not s_clean:
                    continue
                try:
                    loc = target_scope.locator(s_clean).first
                    if await loc.count() > 0:
                        await loc.evaluate(clear_js)
                        return
                except Exception:
                    continue

    async def _page_has_no_results(self, page: Page) -> bool:
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                if await target_scope.evaluate(NO_RESULTS_JS):
                    return True
            except Exception:
                continue
        return False

    async def _retry_address_search_if_empty(
        self,
        page: Page,
        plan: dict[str, Any],
        query_value: str,
        snapshot: dict[str, Any] | None,
        submit_sel: str | None,
        _log_act,
    ) -> bool:
        if plan.get("query_type") != "address":
            return False

        snap_retry = snapshot or await self.extract_snapshot(page)
        alternate = list(plan.get("alternate_fills") or [])
        if not alternate:
            alternate = build_mailing_line_fills(snap_retry, query_value)

        if alternate:
            await _log_act(
                "retry_search",
                "No records found; clearing split street fields and searching full Mailing Address line",
                5,
            )
            for fill in plan.get("fills") or []:
                if fill.get("field_role") in self._STRUCTURED_ADDRESS_ROLES:
                    await self._clear_fill_field(page, fill)
                    await page.wait_for_timeout(150)
            for fill in alternate:
                await self._apply_fill_entry(page, fill, query_value)
                await page.wait_for_timeout(300)
            if submit_sel:
                await self._click_submit_for_plan(page, plan, submit_sel, snap_retry)
            await page.wait_for_timeout(2500)
            if not await self._page_has_no_results(page):
                plan["fills"] = alternate
                plan["address_search_mode"] = "mailing_line"
                return True

        fresh = snap_retry
        retry_fills = build_search_fills(fresh, "address", query_value)
        if not retry_fills:
            return False
        for fill in retry_fills:
            await self._apply_fill_entry(page, fill, query_value)
            await page.wait_for_timeout(300)
        if submit_sel:
            await self._click_submit_for_plan(page, plan, submit_sel, fresh)
        await page.wait_for_timeout(2500)
        await _log_act("retry_search", "Retried address search with direction/suffix/tax year fields", 5)
        return not await self._page_has_no_results(page)

    async def _ensure_search_panel_open(
        self,
        page: Page,
        plan: dict[str, Any],
    ) -> bool:
        """Open Schneider/qPublic accordion panel once — never toggle a panel that is already open."""
        toggle = (plan.get("tab_selector") or "").strip()
        fill_sel = (
            (plan.get("fills") or [{}])[0].get("selector")
            or plan.get("input_selector")
            or ""
        ).strip()
        target = toggle if "btnheadertoggle" in toggle.lower() else fill_sel or toggle
        if not target:
            return False
        return await ensure_search_panel_for_selector(page, target)

    async def _retry_fill_with_ai_dom_recovery(
        self,
        page: Page,
        plan: dict[str, Any],
        query_value: str,
        workflow_run_id: str | None,
        node_id: str | None,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        """Re-open panel, rescan DOM, and re-plan with vision/LLM when the first fill attempt failed."""
        qt = (plan.get("query_type") or "parcel").lower()
        await self._activate_query_mode_tab(page, plan, qt)
        await self._ensure_search_panel_open(page, plan)
        await page.wait_for_timeout(400)
        fresh_snapshot = await self.extract_snapshot(page)
        plan = refine_plan_from_snapshot(plan, fresh_snapshot, qt, query_value)
        fills = list(plan.get("fills") or []) or build_search_fills(fresh_snapshot, qt, query_value)
        remap_plan_fill_selectors(plan, fresh_snapshot.get("elements", []))
        filled_labels: list[str] = []
        filled = False
        for fill in fills:
            val = (fill.get("value") or "").strip()
            if not fill.get("selector"):
                continue
            if not val and fill.get("field_role") != "tax_year":
                continue
            if await self._apply_fill_entry(page, fill, query_value):
                filled = True
                filled_labels.append(f"{fill.get('selector')}={val[:48]}")
                await page.wait_for_timeout(400)
        if filled:
            plan["fills"] = fills
            return filled, filled_labels, plan
        if llm_configured():
            try:
                vision_plan = await self._plan_search_with_vision(
                    page, fresh_snapshot, qt, query_value, workflow_run_id, node_id
                )
                if vision_plan:
                    plan = refine_plan_from_snapshot(vision_plan, fresh_snapshot, qt, query_value)
                    fills = list(plan.get("fills") or []) or build_search_fills(fresh_snapshot, qt, query_value)
                    remap_plan_fill_selectors(plan, fresh_snapshot.get("elements", []))
                    for fill in fills:
                        val = (fill.get("value") or "").strip()
                        if not fill.get("selector"):
                            continue
                        if not val and fill.get("field_role") != "tax_year":
                            continue
                        if await self._apply_fill_entry(page, fill, query_value):
                            filled = True
                            filled_labels.append(f"{fill.get('selector')}={val[:48]}")
                            await page.wait_for_timeout(400)
                    if filled:
                        plan["fills"] = fills
            except Exception as vision_err:
                logger.debug("Vision fill recovery note: %s", vision_err)
        if not filled and qt == "address":
            ok, sel = await self._try_fill_address_by_label(page, query_value)
            if ok:
                fills = [
                    {
                        "selector": sel,
                        "value": _normalize_query_value("address", query_value),
                        "field_role": "mailing_line",
                    }
                ]
                plan["fills"] = fills
                plan["input_selector"] = sel
                filled = True
                filled_labels.append(f"{sel}={query_value[:48]}")
        return filled, filled_labels, plan

    async def _try_fill_address_by_label(self, page: Page, query_value: str) -> tuple[bool, str]:
        val = _normalize_query_value("address", query_value)
        for scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                loc = scope.get_by_label(re.compile(r"^\s*address\s*:?\s*", re.I)).first
                if await loc.count() == 0 or not await loc.is_visible(timeout=1500):
                    continue
                await loc.scroll_into_view_if_needed(timeout=2500)
                await loc.fill(val, timeout=3000)
                try:
                    await loc.dispatch_event("input")
                    await loc.dispatch_event("change")
                except Exception:
                    pass
                sel = await loc.evaluate(
                    """(el) => {
                        if (el.id && !/^\\d/.test(el.id)) return '#' + CSS.escape(el.id);
                        const ph = el.getAttribute('placeholder');
                        if (ph) {
                            const esc = ph.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
                            return el.tagName.toLowerCase() + '[placeholder="' + esc + '"]';
                        }
                        const nm = el.getAttribute('name');
                        if (nm) return el.tagName.toLowerCase() + '[name="' + nm.replace(/"/g, '\\\\"') + '"]';
                        return '';
                    }"""
                )
                current = (await loc.input_value(timeout=1500)).strip()
                if self._value_matches_query(current, query_value, "address"):
                    logger.info("Filled address search via Address label (%s)", sel or "label")
                    return True, (sel or "").strip()
            except Exception:
                continue
        return False, ""

    def _value_matches_query(self, current: str, query_value: str, qt: str) -> bool:
        cur = (current or "").strip()
        if not cur:
            return False
        qv = (query_value or "").strip()
        if qt == "parcel":
            qd = re.sub(r"\D", "", qv)
            cd = re.sub(r"\D", "", cur)
            if qd and cd:
                if qd == cd or qd in cd or cd in qd:
                    return True
            return qv.upper().replace(" ", "") in cur.upper().replace(" ", "")
        if qt == "address":
            from app.browser.result_selector_ai import _query_match_tokens

            tokens = _query_match_tokens(qv, "address")
            blob = cur.upper()
            if not tokens:
                return qv.upper() in blob
            hits = sum(1 for t in tokens if t.upper() in blob)
            return hits >= min(2, max(1, len(tokens)))
        blob = cur.upper()
        return qv.upper() in blob or any(p in blob for p in qv.upper().split() if len(p) > 2)

    async def _read_input_value_for_selector(self, page: Page, selector: str) -> str:
        if not selector:
            return ""
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for s in [selector, *selector.split(",")]:
                s_clean = s.strip()
                if not s_clean:
                    continue
                try:
                    loc = target_scope.locator(s_clean).first
                    if await loc.count() > 0:
                        return (await loc.input_value(timeout=1500)).strip()
                except Exception:
                    try:
                        return await target_scope.evaluate(
                            """(sel) => {
                                try {
                                    const el = document.querySelector(sel);
                                    return el ? String(el.value || el.textContent || '').trim() : '';
                                } catch (_) { return ''; }
                            }""",
                            s_clean,
                        )
                    except Exception:
                        continue
        return ""

    async def _ensure_query_value_committed(
        self,
        page: Page,
        plan: dict[str, Any],
        query_value: str,
        qt: str,
    ) -> bool:
        """Verify the live input still holds the query (typeahead/autocomplete can clear it)."""
        primary_sel = (
            (plan.get("fills") or [{}])[0].get("selector")
            or plan.get("input_selector")
            or ""
        ).strip()
        if not primary_sel:
            return False
        check_selectors = [primary_sel]
        for fill in plan.get("fills") or []:
            s = (fill.get("selector") or "").strip()
            if s and s not in check_selectors:
                check_selectors.append(s)
        for attempt in range(3):
            current = ""
            for sel in check_selectors:
                current = await self._read_input_value_for_selector(page, sel)
                if self._value_matches_query(current, query_value, qt):
                    if sel != primary_sel:
                        plan["input_selector"] = sel
                        if plan.get("fills"):
                            plan["fills"][0]["selector"] = sel
                    return True
            logger.info(
                "Search field lost query value (attempt %d, saw %r); re-applying fill for %s",
                attempt + 1,
                (current or "")[:60],
                query_value,
            )
            fills = list(plan.get("fills") or [])
            if not fills and primary_sel:
                fills = [{"selector": primary_sel, "value": query_value}]
            for fill in fills:
                await self._apply_fill_entry(page, fill, query_value)
            await page.wait_for_timeout(450)
        if qt == "address":
            ok, sel = await self._try_fill_address_by_label(page, query_value)
            if ok:
                plan.setdefault("fills", [{"selector": sel, "value": query_value}])
                plan["fills"][0]["selector"] = sel
                plan["input_selector"] = sel
                return True
        current = await self._read_input_value_for_selector(page, primary_sel)
        return self._value_matches_query(current, query_value, qt)

    async def _bulk_unfiltered_results_page(self, page: Page) -> bool:
        try:
            body = (await page.locator("body").inner_text(timeout=3500) or "")
        except Exception:
            return False
        low = body.lower()
        m = re.search(r"returned\s+([\d,]+)\s+records", body, re.I)
        if m:
            try:
                if int(m.group(1).replace(",", "")) > 100:
                    return True
            except ValueError:
                pass
        if "property search results" in low and "export all records" in low:
            if "next 50" in low or "next fifty" in low:
                return True
        return False

    async def _fill_selector_on_page(
        self,
        page: Page,
        selector: str,
        value: str,
    ) -> bool:
        await ensure_search_panel_for_selector(page, selector)
        await page.wait_for_timeout(400)
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for s in [selector, *selector.split(",")]:
                s_clean = s.strip()
                if not s_clean:
                    continue
                try:
                    inp_el = target_scope.locator(s_clean).first
                    if await inp_el.count() > 0 and await inp_el.is_visible(timeout=1500):
                        try:
                            await inp_el.scroll_into_view_if_needed(timeout=2500)
                        except Exception:
                            pass
                        await inp_el.evaluate(FILL_INPUT_JS, value)
                        try:
                            await inp_el.click(force=True, timeout=1000)
                            await inp_el.fill(value, force=True, timeout=2500)
                        except Exception:
                            pass
                        try:
                            current = (await inp_el.input_value(timeout=1500)).strip()
                            if current == str(value).strip():
                                return True
                        except Exception:
                            pass
                        try:
                            ok = await target_scope.evaluate(
                                """(sel, val) => {
                                    const pick = (s) => {
                                        try { return document.querySelector(s); } catch (_) { return null; }
                                    };
                                    let el = pick(sel);
                                    if (!el) return false;
                                    return String(el.value || '').trim() === String(val).trim();
                                }""",
                                s_clean,
                                value,
                            )
                            if ok:
                                return True
                        except Exception:
                            pass
                except Exception:
                    continue
        return False

    async def _click_submit_for_plan(
        self,
        page: Page,
        plan: dict[str, Any],
        submit_sel: str | None,
        snapshot: dict[str, Any] | None,
    ) -> bool:
        fills = plan.get("fills") or []
        input_sel = (fills[0].get("selector") if fills else "") or plan.get("input_selector") or ""
        submit_sel = (submit_sel or "").strip()
        if submit_sel:
            if await self._click_selector_on_page(page, submit_sel):
                return True
        if input_sel:
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    clicked = await target_scope.evaluate(PAIR_SUBMIT_CLICK_JS, input_sel)
                    if clicked:
                        return True
                except Exception:
                    continue
        snap = snapshot or await self.extract_snapshot(page)
        buttons = [
            e for e in snap.get("elements", []) if e.get("kind") in ("search_button", "button")
        ]
        qt = (plan.get("query_type") or "parcel").lower()
        best = _pick_submit_for_search_context(buttons, plan, snap.get("elements", []), qt)
        if best and best.get("selector"):
            return await self._click_selector_on_page(page, best["selector"])
        if submit_sel:
            return await self._click_selector_on_page(page, submit_sel)
        try:
            from app.extraction.tax_collector_reports import TAX_UNIFIED_SUBMIT_JS

            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    pick = await target_scope.evaluate(TAX_UNIFIED_SUBMIT_JS)
                except Exception:
                    pick = None
                if pick and pick.get("selector"):
                    if await self._click_selector_on_page(page, pick["selector"]):
                        return True
        except Exception as dyn_err:
            logger.debug("Dynamic unified Search click: %s", dyn_err)
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                if await target_scope.evaluate(INVOKE_PORTAL_SUBMIT_JS):
                    return True
            except Exception:
                continue
        return False

    async def _click_selector_on_page(self, page: Page, selector: str) -> bool:
        sel_lower = selector.lower()
        if any(k in sel_lower for k in ["facebook", "twitter", "linkedin", "tabrow", "homepage", "shareicon"]):
            return False
        if "parcel search" in sel_lower or "property search" in sel_lower:
            return False
        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
            for s in [selector, *selector.split(",")]:
                s_clean = s.strip()
                if not s_clean:
                    continue
                try:
                    btn_el = target_scope.locator(s_clean).first
                    if await btn_el.count() > 0 and await btn_el.is_visible(timeout=1500):
                        is_safe = await btn_el.evaluate(
                            """(el) => {
                                const tag = el.tagName.toUpperCase();
                                const role = (el.getAttribute('role') || '').toLowerCase();
                                const id = (el.id || '').toLowerCase();
                                const onc = (el.getAttribute('onclick') || '').toLowerCase();
                                if (['NAV', 'HEADER', 'FOOTER', 'BODY', 'FORM'].includes(tag)) return false;
                                if (tag === 'DIV') {
                                    if (role === 'button' || id === 'search' || /submitform/.test(onc)) return true;
                                    return false;
                                }
                                const href = (el.getAttribute('href') || '').toLowerCase();
                                if (href.includes('facebook.com') || href.includes('twitter.com') || href.includes('x.com') || href.includes('linkedin.com') || href.includes('instagram.com')) return false;
                                if (id.includes('facebook') || id.includes('twitter') || id.includes('tabrow') || id.includes('homepage')) return false;
                                return true;
                            }"""
                        )
                        if not is_safe:
                            continue
                        await btn_el.click(force=True, timeout=3000)
                        try:
                            await btn_el.evaluate(
                                """(el) => {
                                    if (typeof submitForm === 'function') {
                                        try { submitForm(0); return; } catch (_) {}
                                    }
                                    const onclick = el.getAttribute('onclick');
                                    if (onclick) {
                                        try { el.click(); } catch (_) {}
                                    }
                                }"""
                            )
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue
        return False

    async def execute_plan(
        self,
        page: Page,
        plan: dict[str, Any],
        query_value: str,
        workflow_run_id: str | None = None,
        node_id: str | None = None,
        snapshot: dict[str, Any] | None = None,
        skip_fill_and_submit: bool = False,
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

        portal_for_log = (plan.get("_tax_portal_search_url") or plan.get("portal_url") or "").strip()

        async def _log_act(action: str, description: str, step_idx: int, status: str = "COMPLETED", err: str | None = None):
            if bm and workflow_run_id:
                try:
                    from app.browser.navigation import best_page_url

                    live = (
                        best_page_url(page, portal_for_log)
                        if page and not page.is_closed()
                        else portal_for_log or None
                    )
                    await bm.log_session_activity(
                        action=action,
                        description=description,
                        node_id=node_id,
                        step_index=step_idx,
                        status=status,
                        url=live,
                        error=err,
                    )
                except Exception as log_err:
                    logger.debug("Failed to log activity: %s", log_err)

        tax_submit_via_unified = False
        try:
            from app.browser.modal_detector import dismiss_blocking_overlays_heuristic
            from app.extraction.tax_collector_reports import ensure_tax_collector_search_page, is_tax_collector_portal

            await dismiss_blocking_overlays_heuristic(page)
            if is_tax_collector_portal(page.url) or any(
                is_tax_collector_portal(f.url or "") for f in page.frames
            ):
                await ensure_tax_collector_search_page(
                    page,
                    query_value=query_value,
                    portal_search_url=plan.get("_tax_portal_search_url") or plan.get("portal_url"),
                )
                await dismiss_blocking_overlays_heuristic(page)
        except Exception as prep_err:
            logger.debug("Tax portal prep: %s", prep_err)

        portal_target = (
            (plan.get("_tax_portal_search_url") or plan.get("portal_url") or "").strip()
        )
        if portal_target:
            from app.browser.navigation import ensure_portal_page

            if not await ensure_portal_page(
                page,
                portal_target,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            ):
                await _log_act(
                    "navigate",
                    f"Could not open portal before search: {portal_target}",
                    0,
                    status="FAILED",
                )
                return {
                    "success": False,
                    "plan": plan,
                    "record": {
                        "search_failed": True,
                        "failure_reason": "portal_navigation_failed",
                        "portal_url": portal_target,
                        "query_value": query_value,
                    },
                    "detail_opened": False,
                    "downloaded_documents": [],
                }

        # 0. Initial page navigation milestone
        await _log_act("navigate", f"Connected to portal: {page.url}", 0)

        if not skip_fill_and_submit:
            try:
                if await ensure_schneider_search_workspace(page):
                    await _log_act("navigate", "On Real Property Search workspace", 0)
            except Exception as nav_err:
                logger.debug("Schneider search workspace note: %s", nav_err)

        filled_input = skip_fill_and_submit
        submitted = False
        detail_from_submit = False
        qt_early = (plan.get("query_type") or "parcel").lower()
        already_on_detail = False
        needs_submit_after_playwright = False
        if skip_fill_and_submit:
            try:
                from app.extraction.tax_collector_reports import (
                    is_tax_collector_portal,
                    tax_portal_body_matches_query,
                    ensure_tax_collector_search_page,
                )

                if is_tax_collector_portal(page.url) or any(
                    is_tax_collector_portal(f.url or "") for f in page.frames
                ):
                    if not await tax_portal_body_matches_query(page, query_value):
                        await _log_act(
                            "navigate",
                            f"Stale tax record on page — opening search for {query_value[:60]}",
                            1,
                        )
                        await ensure_tax_collector_search_page(
                            page,
                            query_value=query_value,
                            portal_search_url=plan.get("_tax_portal_search_url"),
                        )
                        skip_fill_and_submit = False
                        filled_input = False
            except Exception as stale_tax_err:
                logger.debug("Stale tax portal check: %s", stale_tax_err)

        if skip_fill_and_submit:
            try:
                needs_submit_after_playwright = await self._search_form_still_needs_submit(page, query_value)
            except Exception:
                needs_submit_after_playwright = False
            if needs_submit_after_playwright:
                await _log_act(
                    "submit",
                    "Search form still active after Playwright steps — clicking in-form Search",
                    5,
                )
            else:
                try:
                    already_on_detail = await self._page_shows_property_detail(page, query_value, qt_early)
                except Exception:
                    already_on_detail = False
                if already_on_detail:
                    detail_from_submit = True
                    submitted = True
                    await _log_act(
                        "view_record",
                        "Property detail already open — skipping repeat search",
                        5,
                    )
                else:
                    from app.browser.property_capture_prep import (
                        page_is_search_results_listing,
                        wait_for_property_detail_ready,
                    )

                    if await page_is_search_results_listing(page):
                        submitted = True
                        already_on_detail = False
                        await _log_act(
                            "view_record",
                            "Search results table open — will open matching property record",
                            5,
                        )
                    elif await wait_for_property_detail_ready(page, query_value, timeout_ms=3500):
                        already_on_detail = True
                        detail_from_submit = True
                        submitted = True
                        await _log_act(
                            "view_record",
                            "Property detail detected after Playwright steps — skipping repeat search",
                            5,
                        )
                    else:
                        submitted = True

        if not skip_fill_and_submit:
            # 0.5. Navigate from portal home to search form only when not already on it
            on_search_form = await self._page_has_property_search_form(page)
            if not on_search_form:
                for f in page.frames:
                    try:
                        search_nav = f.locator(
                            "a:has-text('Property Search'), button:has-text('Property Search')"
                        ).first
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

        if portal_target:
            from app.browser.navigation import page_needs_portal_navigation

            if page_needs_portal_navigation(page, portal_target):
                from app.browser.navigation import ensure_portal_page

                restored = await ensure_portal_page(
                    page,
                    portal_target,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                    wait_for_mount=False,
                )
                if restored:
                    await _log_act(
                        "navigate",
                        f"Restored county portal after modal/navigation drift: {page.url}",
                        2,
                    )
                else:
                    await _log_act(
                        "navigate",
                        f"Portal lost (page on {page.url or 'blank'}) — could not restore {portal_target}",
                        2,
                        status="WARNING",
                    )

        # Check if the target input field is already visible and interactable
        input_already_visible = skip_fill_and_submit
        if not skip_fill_and_submit and input_sel:
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

        if tab_sel and "btnheadertoggle" not in str(tab_sel).lower():
            fill_sel = (
                (plan.get("fills") or [{}])[0].get("selector")
                or plan.get("input_selector")
                or ""
            ).lstrip("#")
            m = re.match(r"(ctlBodyPane_ctl\d+)", fill_sel, re.I)
            if m:
                tab_sel = f"#{m.group(1)}_btnHeaderToggle"
                plan["tab_selector"] = tab_sel
            else:
                tab_sel = None

        # 2. Select and Click Tab (jQuery UI only — Schneider accordions use ensure-panel in step 3)
        accordion_tab = tab_sel and "btnheadertoggle" in str(tab_sel).lower()
        if not skip_fill_and_submit and tab_sel and not input_already_visible and not accordion_tab:
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
        if not skip_fill_and_submit and radio_sel:
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

        # 3. Locate and Fill Input Field(s)
        qt = plan.get("query_type", "parcel")
        if not skip_fill_and_submit:
            await self._activate_query_mode_tab(page, plan, qt)
            panel_ready = await self._ensure_search_panel_open(page, plan)
            if panel_ready and accordion_tab and tab_sel:
                await _log_act("click_tab", f"Expanded search panel ({tab_sel})", 3)
            elif accordion_tab and tab_sel:
                await _log_act(
                    "click_tab",
                    f"Selected {qt.capitalize()} Search Tab ({tab_sel})",
                    3,
                )

            fills: list[dict[str, str]] = list(plan.get("fills") or [])
            if not fills and snapshot:
                fills = build_search_fills(snapshot, qt, query_value)
            if not fills and input_sel:
                fills = [{"selector": input_sel, "value": _normalize_query_value(qt, query_value)}]

            def _weak_fill_selectors(fl: list[dict]) -> bool:
                return any(
                    "placeholder=" in (f.get("selector") or "").lower()
                    and "#" not in (f.get("selector") or "")
                    for f in fl
                )

            snap_for_fills = snapshot or await self.extract_snapshot(page)
            if fills and _weak_fill_selectors(fills):
                canonical = build_search_fills(snap_for_fills, qt, query_value)
                if canonical:
                    fills = canonical
                    plan["fills"] = fills
                    plan["input_selector"] = canonical[0].get("selector") or plan.get("input_selector")
            elif qt == "address" and not fills:
                canonical = build_search_fills(snap_for_fills, qt, query_value)
                if canonical:
                    fills = canonical
                    plan["fills"] = fills
                    plan["input_selector"] = canonical[0].get("selector")

            elements_snap = snap_for_fills.get("elements", [])
            for fill in fills:
                sel = (fill.get("selector") or "").strip()
                hit = next(
                    (
                        e
                        for e in elements_snap
                        if e.get("selector") == sel
                        or (e.get("id") and sel.lstrip("#") == e.get("id"))
                    ),
                    None,
                )
                if hit:
                    fill["selector"] = _canonical_element_selector(hit)
                elif sel:
                    fill["selector"] = match_snapshot_input_selector(sel, elements_snap)

            filled_input = False
            filled_labels: list[str] = []
            for fill in fills:
                val = (fill.get("value") or "").strip()
                if not fill.get("selector"):
                    continue
                if not val and fill.get("field_role") != "tax_year":
                    continue
                if await self._apply_fill_entry(page, fill, query_value):
                    filled_input = True
                    filled_labels.append(f"{fill.get('selector')}={val[:48]}")
                    await page.wait_for_timeout(400)

            if filled_input:
                await _log_act(
                    "fill",
                    f"Entered {plan.get('query_type', 'query').capitalize()}: {', '.join(filled_labels) or query_value}",
                    4,
                )
                await page.wait_for_timeout(400)

            if not filled_input:
                logger.info(
                    "Planned selectors did not match visible inputs for %s (%s). Re-scanning DOM with AI recovery.",
                    query_value,
                    input_sel,
                )
                recovered, recovered_labels, plan = await self._retry_fill_with_ai_dom_recovery(
                    page, plan, query_value, workflow_run_id, node_id
                )
                if recovered:
                    filled_input = True
                    filled_labels = recovered_labels
                    await _log_act(
                        "fill",
                        f"Entered {plan.get('query_type', 'query').capitalize()} after AI DOM recovery: {', '.join(filled_labels)}",
                        4,
                    )
            if not filled_input and qt == "address":
                ok, sel = await self._try_fill_address_by_label(page, query_value)
                if ok:
                    plan["fills"] = [
                        {
                            "selector": sel,
                            "value": _normalize_query_value("address", query_value),
                            "field_role": "mailing_line",
                        }
                    ]
                    plan["input_selector"] = sel
                    filled_input = True
                    await _log_act(
                        "fill",
                        f"Entered Address via labeled field: {query_value[:80]}",
                        4,
                    )

            if not filled_input:
                try:
                    from app.extraction.tax_collector_reports import (
                        is_tax_collector_portal,
                        run_tax_collector_unified_search,
                    )

                    if is_tax_collector_portal(page.url) or any(
                        is_tax_collector_portal(f.url or "") for f in page.frames
                    ):
                        await ensure_tax_collector_search_page(
                            page,
                            query_value=query_value,
                            portal_search_url=plan.get("_tax_portal_search_url"),
                        )
                        tax_res = await run_tax_collector_unified_search(page, query_value)
                        if tax_res.get("filled"):
                            filled_input = True
                            plan["fills"] = tax_res.get("fills") or plan.get("fills")
                            plan["input_selector"] = tax_res.get("input_selector") or plan.get("input_selector")
                            if tax_res.get("submit_selector"):
                                plan["submit_selector"] = tax_res["submit_selector"]
                            if tax_res.get("submitted"):
                                tax_submit_via_unified = True
                                submitted = True
                            await _log_act(
                                "fill",
                                f"Entered tax portal unified search: {query_value[:80]}",
                                4,
                            )
                except Exception as tax_fill_err:
                    logger.debug("Tax collector unified search fallback: %s", tax_fill_err)

        # 3.5. Dynamic Autocomplete / Suggestion Dropdown Handling
        try:
            from app.browser.autocomplete_handler import detect_and_select_autocomplete

            already_committed = False
            if filled_input:
                already_committed = await self._ensure_query_value_committed(
                    page, plan, query_value, qt
                )
            if already_committed:
                ac_res: dict[str, Any] = {"detected": False, "skipped": "value_already_in_field"}
            else:
                ac_res = await detect_and_select_autocomplete(
                    page,
                    expected_value=query_value,
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                )
            if ac_res.get("detected"):
                self._last_autocomplete_action = ac_res
                logger.info("Handled autocomplete dropdown selection: %s", ac_res.get("selected_text"))
                await page.wait_for_timeout(400)
        except Exception as ac_err:
            logger.debug("Autocomplete handler note: %s", ac_err)

        if (not skip_fill_and_submit or needs_submit_after_playwright) and filled_input:
            await self._prepare_single_field_search(page, plan)
            committed = await self._ensure_query_value_committed(
                page, plan, query_value, qt
            )
            if not committed:
                logger.warning(
                    "Query value not present in search field before submit; blocking search for %s",
                    query_value,
                )
                filled_input = False
            else:
                await _log_act(
                    "fill",
                    f"Verified search field contains query before submit",
                    4,
                )

        # 4. Click Submit Button
        before_submit_url = page.url or ""
        if not skip_fill_and_submit or needs_submit_after_playwright:
            if not tax_submit_via_unified:
                submitted = False
            snap_for_submit = snapshot or await self.extract_snapshot(page)
            if tax_submit_via_unified:
                await _log_act("click", "Submitted tax portal unified search", 5)
            elif filled_input and (submit_sel or plan.get("fills")):
                if submit_sel:
                    if await self._click_selector_on_page(page, submit_sel):
                        submitted = True
                        await _log_act("click", f"Clicked Submit Search ({submit_sel})", 5)
                        await page.wait_for_timeout(2500)
                if not submitted:
                    submitted = await self._click_submit_for_plan(page, plan, submit_sel, snap_for_submit)
                    if submitted:
                        used = submit_sel or "(paired panel Search)"
                        await _log_act("click", f"Clicked Submit Search ({used})", 5)
                        await page.wait_for_timeout(2500)

            if filled_input and not submitted:
                buttons = [
                    e for e in snap_for_submit.get("elements", []) if e.get("kind") in ("search_button", "button")
                ]
                qt_submit = (plan.get("query_type") or "parcel").lower()
                primary = _primary_input_element(plan, snap_for_submit.get("elements", []))
                ranked = sorted(
                    (b for b in buttons if not _is_navigation_search_link(b)),
                    key=lambda b: _score_submit_for_context(b, primary, qt_submit),
                    reverse=True,
                )
                for btn in ranked:
                    sel = btn.get("selector")
                    if not sel or _score_submit_for_context(btn, primary, qt_submit) < 40:
                        break
                    btn_text = _norm_blob(btn.get("text", ""))
                    if btn_text in ("go", "go!") or 'has-text("go")' in sel.lower():
                        continue
                    if await self._click_selector_on_page(page, sel):
                        submitted = True
                        await _log_act("click", f"Clicked Search button ({sel})", 5)
                        logger.info("Clicked submit from ranked DOM snapshot: %s", sel)
                        await page.wait_for_timeout(2500)
                    break

            if filled_input and not submitted:
                for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                    try:
                        if await target_scope.evaluate(INVOKE_PORTAL_SUBMIT_JS):
                            submitted = True
                            await _log_act("click", "Activated portal Search via submitForm / #search", 5)
                            await page.wait_for_timeout(2500)
                            break
                    except Exception:
                        continue
            if filled_input and not submitted:
                try:
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(2000)
                    if not await self._stale_search_portal_page(page):
                        submitted = True
                        await _log_act("submit", "Pressed Enter key to submit search", 5)
                    else:
                        for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                            try:
                                if await target_scope.evaluate(INVOKE_PORTAL_SUBMIT_JS):
                                    submitted = True
                                    await _log_act("click", "Search activated after Enter via submitForm", 5)
                                    break
                            except Exception:
                                continue
                    if submitted:
                        await page.wait_for_timeout(2500)
                except Exception:
                    pass

        if not skip_fill_and_submit and filled_input and not submitted:
            try:
                from app.extraction.tax_collector_reports import (
                    is_tax_collector_portal,
                    page_shows_tax_account_summary,
                )

                if is_tax_collector_portal(page.url) or any(
                    is_tax_collector_portal(f.url or "") for f in page.frames
                ):
                    await page.wait_for_timeout(1200)
                    if await page_shows_tax_account_summary(page, query_value):
                        submitted = True
                        detail_from_submit = True
                        already_on_detail = True
                        await _log_act(
                            "view_record",
                            "Tax collector account summary open after search",
                            5,
                        )
            except Exception as tax_detail_err:
                logger.debug("Tax account summary detection: %s", tax_detail_err)

        if not skip_fill_and_submit and submitted:
            detail_from_submit = await self._wait_for_property_detail_view(
                page, query_value, qt, before_submit_url
            )
            bulk_listing = await self._bulk_unfiltered_results_page(page)
            if bulk_listing:
                logger.warning(
                    "Bulk unfiltered results after submit (empty field search suspected) for %s",
                    query_value,
                )
                await self._activate_query_mode_tab(page, plan, qt)
                await self._ensure_query_value_committed(page, plan, query_value, qt)
                if await self._retry_panel_search_submit(page, plan):
                    await page.wait_for_timeout(2500)
                    detail_from_submit = await self._wait_for_property_detail_view(
                        page, query_value, qt, before_submit_url
                    )
                    bulk_listing = await self._bulk_unfiltered_results_page(page)

            if (
                not detail_from_submit
                and bulk_listing
            ):
                await _log_act(
                    "submit",
                    "Search returned county-wide listing; parcel field was empty at submit",
                    5,
                    status="FAILED",
                )
                return {
                    "success": False,
                    "plan": plan,
                    "record": {
                        "search_failed": True,
                        "failure_reason": "empty_field_bulk_results",
                        "query_value": query_value,
                    },
                    "detail_opened": False,
                    "downloaded_documents": [],
                }

            if not detail_from_submit and await self._stale_search_portal_page(page):
                logger.info(
                    "Search form still visible after submit; retrying paired panel Search for %s",
                    query_value,
                )
                await _log_act(
                    "retry_search",
                    "Retry Search using panel button beside the filled field",
                    5,
                    status="WARNING",
                )
                if await self._retry_panel_search_submit(page, plan):
                    await page.wait_for_timeout(2000)
                    detail_from_submit = await self._wait_for_property_detail_view(
                        page, query_value, qt, before_submit_url
                    )

        if (
            not skip_fill_and_submit
            and submitted
            and not detail_from_submit
            and await self._stale_search_portal_page(page)
            and not await self._page_shows_tax_bill_result_list(page, query_value)
        ):
            await _log_act(
                "submit",
                f"Search did not open property detail for {query_value[:80]}",
                5,
                status="FAILED",
            )
            return {
                "success": False,
                "plan": plan,
                "record": {
                    "search_failed": True,
                    "failure_reason": "search_did_not_reach_property_detail",
                    "query_value": query_value,
                },
                "detail_opened": False,
                "downloaded_documents": [],
            }

        if not skip_fill_and_submit and not filled_input:
            await _log_act(
                "fill",
                f"Could not enter search value; submit blocked for {query_value[:80]}",
                4,
                status="FAILED",
            )
            return {
                "success": False,
                "plan": plan,
                "record": {
                    "search_failed": True,
                    "failure_reason": "could_not_fill_search_field",
                    "query_value": query_value,
                },
                "detail_opened": False,
                "downloaded_documents": [],
            }

        if not skip_fill_and_submit and not submitted:
            try:
                from app.extraction.tax_collector_reports import (
                    is_tax_collector_portal,
                    page_shows_tax_account_summary,
                )

                if is_tax_collector_portal(page.url) or any(
                    is_tax_collector_portal(f.url or "") for f in page.frames
                ):
                    if await page_shows_tax_account_summary(page, query_value):
                        submitted = True
                        detail_from_submit = True
                        already_on_detail = True
            except Exception:
                pass

        if not skip_fill_and_submit and not submitted:
            await _log_act(
                "submit",
                f"Search submit failed for {query_value[:80]}",
                5,
                status="FAILED",
            )
            return {
                "success": False,
                "plan": plan,
                "record": {
                    "search_failed": True,
                    "failure_reason": "search_not_submitted",
                    "query_value": query_value,
                },
                "detail_opened": False,
                "downloaded_documents": [],
            }

        if (
            plan.get("query_type") == "address"
            and submitted
            and not skip_fill_and_submit
            and not already_on_detail
        ):
            try:
                if await self._page_has_no_results(page):
                    await _log_act(
                        "retry_search",
                        "No records found; will try mailing-address line or adjust street fields",
                        5,
                        status="WARNING",
                    )
                    snap_retry = snapshot or await self.extract_snapshot(page)
                    recovered = await self._retry_address_search_if_empty(
                        page, plan, query_value, snap_retry, submit_sel, _log_act
                    )
                    if recovered:
                        submitted = True
            except Exception as retry_err:
                logger.debug("Address search retry note: %s", retry_err)

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
        detail_opened = bool(detail_from_submit or already_on_detail)
        curr_url = page.url or ""
        clean_target_digits = re.sub(r"\D", "", query_value)

        try:
            await page.wait_for_selector(
                "table.ui-jqgrid-btable tbody tr, table tbody tr.jqgrow, "
                "table tbody tr td, td:has-text('Site Address'), td:has-text('Owner Name')",
                timeout=5000,
            )
        except Exception:
            pass

        res_click_info: dict[str, Any] = {}
        from app.extraction.tax_collector_reports import (
            sanitize_tax_results_list_url,
            wait_for_tax_search_results_listing,
            capture_all_tax_search_result_records,
            is_tax_collector_portal,
        )

        listing_probe: dict[str, Any] = {"ready": False, "link_count": 0, "results_url": ""}
        if is_tax_collector_portal(page.url) or any(
            is_tax_collector_portal(f.url or "") for f in page.frames
        ):
            listing_probe = await wait_for_tax_search_results_listing(page, query_value, timeout_s=14)

        multi_tax_results_list = bool(
            await self._page_shows_tax_bill_result_list(page, query_value)
            or (listing_probe.get("ready") and int(listing_probe.get("link_count") or 0) >= 2)
        )
        if not multi_tax_results_list:
            listing_probe = await wait_for_tax_search_results_listing(page, query_value, timeout_s=10)
            multi_tax_results_list = bool(
                await self._page_shows_tax_bill_result_list(page, query_value)
                or (listing_probe.get("ready") and int(listing_probe.get("link_count") or 0) >= 2)
            )
        if multi_tax_results_list:
            from app.extraction.tax_collector_reports import tax_search_results_same_document_state

            plan["_tax_results_same_document"] = tax_search_results_same_document_state(page.url)
            plan["_tax_search_results_url"] = sanitize_tax_results_list_url(
                listing_probe.get("results_url") or page.url
            )
            plan["_tax_portal_search_url"] = (
                plan.get("_tax_portal_search_url") or (page.url or "").split("#")[0]
            )
            if not plan.get("_tax_multi_captured"):
                search_in = (
                    (plan.get("fills") or [{}])[0].get("selector")
                    or plan.get("input_selector")
                    or ""
                ).strip() or None
                search_sub = (plan.get("submit_selector") or "").strip() or None
                portal_search_url = (
                    plan.get("_tax_portal_search_url") or plan["_tax_search_results_url"] or page.url
                ).strip()
                multi_docs, multi_meta = await capture_all_tax_search_result_records(
                    page,
                    query_value.strip(),
                    results_list_url=plan["_tax_search_results_url"],
                    portal_search_url=portal_search_url,
                    search_input_selector=search_in,
                    search_submit_selector=search_sub,
                    workflow_run_id=str(workflow_run_id or ""),
                )
                plan["_tax_multi_docs"] = multi_docs
                plan["_tax_multi_meta"] = multi_meta
                plan["_tax_multi_captured"] = True
                await _log_act(
                    "extract",
                    (
                        f"Captured search results index + "
                        f"{multi_meta.get('tax_search_results_count', 0)} recent tax bill year(s)"
                    ),
                    6,
                )
                already_on_detail = True
                detail_opened = True
        if already_on_detail:
            detail_opened = True
        try:
            from app.browser.property_capture_prep import page_is_search_results_listing

            on_results_grid = await page_is_search_results_listing(page)
            if on_results_grid and not multi_tax_results_list:
                already_on_detail = False
                detail_opened = False
        except Exception:
            on_results_grid = False
        try:
            from app.browser.result_selector_ai import detect_and_click_result_link_ai
            if (not already_on_detail or on_results_grid) and not multi_tax_results_list:
                res_click_info = await detect_and_click_result_link_ai(
                    page,
                    query_value=query_value,
                    query_type=plan.get("query_type", "parcel"),
                    workflow_run_id=workflow_run_id,
                    node_id=node_id,
                )
            if res_click_info.get("is_already_detail"):
                detail_opened = True
                await _log_act(
                    "view_record",
                    res_click_info.get("reasoning")
                    or "Property detail page already open after search",
                    6,
                )
            elif res_click_info.get("is_results_page") and res_click_info.get("clicked"):
                self._last_result_click_action = res_click_info
                detail_opened = True
                await _log_act(
                    "view_record",
                    res_click_info.get("description") or f"Clicked parcel number {query_value} to open property details",
                    6,
                )
        except Exception as res_err:
            logger.warning("Dynamic AI result link detection error: %s", res_err)

        no_matching = bool(res_click_info.get("no_matching_results"))
        if no_matching:
            logger.info(
                "No matching search result for %s; skipping blind row/detail clicks.",
                query_value,
            )

        # Dynamic multi-county search results table/cell handling (td.link, Knockout goToPin, links)
        if (
            not already_on_detail
            and not detail_opened
            and not no_matching
            and not res_click_info.get("is_already_detail")
            and not multi_tax_results_list
            and submitted
        ):
            from app.browser.result_selector_ai import _query_match_tokens, _score_row_against_query

            query_val_space = query_value.replace("-", " ")
            query_digits = re.sub(r"\D", "", query_value)
            qt_res = (plan.get("query_type") or "parcel").lower()
            match_tokens = _query_match_tokens(query_value, qt_res)
            min_row_score = 0.5 if qt_res == "address" else 0.34
            generic_selectors = [
                f'a:has-text("{query_value}")',
                f'a:has-text("{query_val_space}")',
                f'td.link:has-text("{query_value}")',
                f'td.link:has-text("{query_val_space}")',
                f'td.link:has-text("{query_digits}")',
                f'td[data-bind*="goTo"]:has-text("{query_value}")',
                f'td[data-bind*="goTo"]:has-text("{query_val_space}")',
                f'td[data-bind*="goTo"]:has-text("{query_digits}")',
            ]
            for target_scope in [page, *[f for f in page.frames if f != page.main_frame]]:
                for cand_sel in generic_selectors:
                    try:
                        result_link = target_scope.locator(cand_sel).first
                        if await result_link.count() > 0 and await result_link.is_visible(timeout=1500):
                            cell_text = (await result_link.inner_text()).strip()
                            from app.browser.property_capture_prep import is_plausible_parcel_id

                            try:
                                row_text = await result_link.evaluate(
                                    """(el) => {
                                        const tr = el.closest('tr');
                                        return tr ? tr.innerText.replace(/\\s+/g, ' ').trim() : (el.innerText || '');
                                    }"""
                                )
                            except Exception:
                                row_text = cell_text
                            blob = f"{row_text} {cell_text}".strip()
                            if match_tokens and _score_row_against_query(blob, match_tokens) < min_row_score:
                                if not (
                                    query_digits
                                    and query_digits in re.sub(r"\D", "", blob)
                                ) and not is_plausible_parcel_id(cell_text, query_value):
                                    continue

                            if query_value and query_value not in cell_text:
                                blob_digits = re.sub(r"\D", "", blob)
                                if not (query_digits and len(query_digits) >= 10 and query_digits in blob_digits):
                                    continue

                            low_cell = cell_text.lower()
                            if (
                                query_value
                                and query_value not in cell_text
                                and re.sub(r"\D", "", query_value) not in re.sub(r"\D", "", cell_text)
                                and not is_plausible_parcel_id(cell_text, query_value)
                                and any(k in low_cell for k in ("grid", "layer", "scalebar", "toolbar"))
                            ):
                                continue
                            if not blob.strip():
                                continue
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
        downloaded_docs: list[dict[str, Any]] = list(plan.get("_tax_multi_docs") or [])

        # 6A. Dynamic Full-Page Universal DOM Extraction
        try:
            skip_listing_dom = multi_tax_results_list or bool(plan.get("_tax_multi_captured"))
            if skip_listing_dom:
                raise RuntimeError("skip_dom_on_tax_year_listing")
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
            if str(dyn_err) != "skip_dom_on_tax_year_listing":
                logger.warning("Dynamic full-page extraction error: %s", dyn_err)

        # 6B. Mistral Document AI OCR Webpage Extraction (detail panel / full page)
        try:
            from app.browser.property_capture_prep import prepare_property_detail_view, sanitize_extracted_record
            skip_tax_listing_ocr = multi_tax_results_list or bool(plan.get("_tax_multi_captured"))
            if not skip_tax_listing_ocr:
                await prepare_property_detail_view(
                    page, workflow_run_id=workflow_run_id, node_id=node_id, query_value=query_value
                )
            from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
            ocr_srv = get_mistral_webpage_ocr()
            if ocr_srv.is_enabled() and not skip_tax_listing_ocr:
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
        if not multi_tax_results_list and not plan.get("_tax_multi_captured"):
            try:
                from app.extraction.assessor_reports import download_universal_assessor_reports
                from app.browser.property_capture_prep import is_plausible_parcel_id

                target_p = final_record.get("parcel_number") or query_value
                if not is_plausible_parcel_id(str(target_p), query_value):
                    target_p = query_value
                reports = await download_universal_assessor_reports(
                    page, str(target_p), query_value=query_value
                )
                if reports:
                    downloaded_docs.extend(reports)
            except Exception as r_err:
                logger.debug("Dynamic assessor report download note: %s", r_err)

        # 6D. General Florida & Fallback Extraction
        if (
            not plan.get("_tax_multi_captured")
            and (not final_record.get("owner_name") or final_record.get("owner_name") == "OWNER OF RECORD")
        ):
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
                capture_tax_collector_document_suite,
                capture_all_tax_search_result_records,
                discover_tax_search_result_links,
                download_official_tax_bill,
                sanitize_tax_results_list_url,
            )
            if is_tax_collector_portal(page.url) or any("tax" in (f.url or "").lower() for f in page.frames):
                logger.info("Extracting structured tax data from tax collector portal...")
                qt_plan = (plan.get("query_type") or "parcel").lower()
                parcel_key = (
                    query_value.strip()
                    if qt_plan == "parcel" and query_value.strip()
                    else (final_record.get("parcel_number") or query_value)
                )
                results_url = sanitize_tax_results_list_url(plan.get("_tax_search_results_url") or "")
                search_in = (
                    (plan.get("fills") or [{}])[0].get("selector")
                    or plan.get("input_selector")
                    or ""
                ).strip() or None
                search_sub = (plan.get("submit_selector") or "").strip() or None
                portal_search_url = (plan.get("_tax_portal_search_url") or results_url or page.url or "").strip()
                from app.extraction.tax_collector_reports import tax_search_results_same_document_state

                if results_url and not (
                    plan.get("_tax_results_same_document")
                    or tax_search_results_same_document_state(results_url)
                ):
                    try:
                        await page.goto(results_url, wait_until="domcontentloaded", timeout=25000)
                        await page.wait_for_timeout(1000)
                    except Exception as list_nav_err:
                        logger.debug("Return to tax results list: %s", list_nav_err)
                from app.extraction.tax_collector_reports import tax_search_result_link_matches_query

                if plan.get("_tax_multi_captured"):
                    multi_meta = plan.get("_tax_multi_meta") or {}
                    for y, rec in (multi_meta.get("tax_search_results_by_year") or {}).items():
                        if not isinstance(rec, dict):
                            continue
                        for k, v in rec.items():
                            if v is not None and (
                                k not in final_record or not final_record[k] or final_record[k] == "OWNER OF RECORD"
                            ):
                                final_record[k] = v
                    final_record["tax_search_results_years"] = multi_meta.get("tax_search_results_years") or []
                    final_record["tax_search_results_by_year"] = multi_meta.get("tax_search_results_by_year") or {}
                    detail_opened = True
                else:
                    discovery = await discover_tax_search_result_links(page, parcel_key)
                    matched_links = [
                        l
                        for l in (discovery.get("links") or [])
                        if tax_search_result_link_matches_query(str(l.get("text") or ""), parcel_key)
                    ]
                    use_multi_year_capture = (
                        multi_tax_results_list
                        or len(matched_links) >= 2
                        or (discovery.get("listing") and len(matched_links) >= 1)
                    )
                    if use_multi_year_capture:
                        if not results_url and await self._page_shows_tax_bill_result_list(page, query_value):
                            results_url = page.url
                            try:
                                await page.goto(results_url, wait_until="domcontentloaded", timeout=20000)
                                await page.wait_for_timeout(1000)
                            except Exception:
                                pass
                        multi_docs, multi_meta = await capture_all_tax_search_result_records(
                            page,
                            parcel_key,
                            results_list_url=results_url or page.url,
                            portal_search_url=portal_search_url,
                            search_input_selector=search_in,
                            search_submit_selector=search_sub,
                            workflow_run_id=str(workflow_run_id or ""),
                        )
                        if multi_docs:
                            downloaded_docs.extend(multi_docs)
                            detail_opened = True
                            await _log_act(
                                "extract",
                                (
                                    f"Captured search results index + "
                                    f"{multi_meta.get('tax_search_results_count', 0)} recent tax bill year(s)"
                                ),
                                7,
                            )
                        for y, rec in (multi_meta.get("tax_search_results_by_year") or {}).items():
                            if not isinstance(rec, dict):
                                continue
                            for k, v in rec.items():
                                if v is not None and (
                                    k not in final_record or not final_record[k] or final_record[k] == "OWNER OF RECORD"
                                ):
                                    final_record[k] = v
                        final_record["tax_search_results_years"] = multi_meta.get("tax_search_results_years") or []
                        final_record["tax_search_results_by_year"] = multi_meta.get("tax_search_results_by_year") or {}
                    elif not multi_tax_results_list and not results_url:
                        tax_rec = await extract_tax_collector_record(page, query_value=query_value)
                        if tax_rec.get("owner_name") or tax_rec.get("total_amount_due") is not None or tax_rec.get("account_number"):
                            for k, v in tax_rec.items():
                                if v is not None and (k not in final_record or not final_record[k] or final_record[k] == "OWNER OF RECORD"):
                                    final_record[k] = v

                        suite_docs, suite_meta = await capture_tax_collector_document_suite(
                            page,
                            parcel_key,
                            account_history=tax_rec.get("account_history"),
                        )
                        downloaded_docs.extend(suite_docs)
                        final_record["tax_screenshot_index"] = suite_meta.get("tax_screenshot_index") or []
                        final_record["tax_years_indexed"] = suite_meta.get("tax_years_indexed") or []
                        final_record["tax_recent_year_screenshots"] = (
                            suite_meta.get("tax_recent_year_screenshots") or []
                        )

                        bill_doc = await download_official_tax_bill(page, parcel_key)
                        if bill_doc:
                            downloaded_docs.append(bill_doc)
        except Exception as tax_ex_err:
            logger.warning("Tax collector extraction note: %s", tax_ex_err)

        from app.browser.property_capture_prep import sanitize_extracted_record

        qt_finalize = (plan.get("query_type") or "parcel").lower()
        final_record = sanitize_extracted_record(
            final_record,
            query_value=query_value,
            query_type=qt_finalize,
        )
        if detail_opened:
            if not final_record.get("parcel_number") and qt_finalize != "address":
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
        from app.browser.property_capture_prep import record_has_assessor_identity

        owner_disp = final_record.get("owner_name") or "—"
        apn_disp = final_record.get("parcel_number") or query_value[:48]
        await _log_act(
            "extract",
            f"Extracted Property: {owner_disp} | APN: {apn_disp}",
            7,
        )

        try:
            from app.extraction.tax_collector_reports import record_has_tax_collector_identity
        except ImportError:
            record_has_tax_collector_identity = lambda _data: False  # type: ignore[assignment]

        tax_identity = record_has_tax_collector_identity(final_record)
        search_ok = bool(
            skip_fill_and_submit
            and (
                detail_opened
                or detail_from_submit
                or record_has_assessor_identity(final_record)
                or tax_identity
            )
            or (
                filled_input
                and submitted
                and (detail_opened or detail_from_submit or tax_identity)
            )
            or (detail_opened and tax_identity)
        )
        if isinstance(final_record, dict):
            final_record.setdefault("_source_url", page.url if (page and not page.is_closed()) else "")

        return {
            "success": search_ok,
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
