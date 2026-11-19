"""Declarative recipe step runner with pre-action security verification and pattern handling."""

import asyncio
import re
import time
import uuid
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import Page

from app.browser.browser_manager import get_browser_manager
from app.browser.evidence_collector import EvidenceCollector
from app.browser.result_extractor import ResultExtractor
from app.browser.security_detector import SecurityDetectionResult, SecurityDetector
from app.browser.url_validator import URLValidator
from app.core.config import settings
from app.core.logging import get_logger
from app.resilience.retry_manager import retry_manager
from app.resilience.retry_policy import BROWSER_NAVIGATION_POLICY

logger = get_logger(__name__)


class SecurityChallengeException(Exception):
    """Raised when an anti-bot or CAPTCHA challenge is detected during step execution."""

    def __init__(self, result: SecurityDetectionResult) -> None:
        self.result = result
        super().__init__(result.reason or "Security barrier detected")


class StepExecutionException(Exception):
    """Raised when a specific recipe step fails."""

    def __init__(
        self,
        message: str,
        step_index: int,
        action: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.step_index = step_index
        self.action = action
        self.evidence = evidence or {}


class PlaywrightStepRunner:
    """Executes validated, approved recipe steps deterministically without eval or arbitrary code execution."""

    def __init__(self, page: Page, run_id: str, node_id: str) -> None:
        self.page = page
        self.run_id = run_id
        self.node_id = node_id
        self.extractor = ResultExtractor(page, run_id, node_id)
        self._last_fill_selector: str | None = None

    async def _field_contains_value(self, selector: str, expected: str) -> bool:
        needle = (expected or "").strip().lower()
        if not needle:
            return False
        head = needle[: min(12, len(needle))]
        for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
            try:
                loc = scope.locator(selector).first
                if await loc.count() == 0:
                    continue
                cur = (await loc.input_value(timeout=1500)).strip().lower()
                if head in cur or needle in cur:
                    return True
            except Exception:
                continue
        return False

    def _selector_looks_wrong_for_address(self, selector: str) -> bool:
        from app.browser.page_search_ai import selector_looks_wrong_for_address

        return selector_looks_wrong_for_address(selector)

    async def _resolve_submit_fill_selector(self, qt: str, query_val: str) -> str | None:
        """Prefer the input that actually holds the query (Address field), not the last mistaken fill."""
        needle = (query_val or "").strip()
        if not needle:
            return self._last_fill_selector
        head = needle.lower()[: min(14, len(needle))]
        candidates: list[str] = []
        if self._last_fill_selector:
            candidates.append(self._last_fill_selector)
        if qt == "address":
            candidates.extend(
                [
                    'input[aria-label*="Address" i]',
                    'label:has-text("Address") >> .. >> input',
                    'input[placeholder*="address" i]',
                    '#SiteStreetAddress',
                ]
            )
        seen: set[str] = set()
        best: str | None = None
        for sel in candidates:
            if not sel or sel in seen:
                continue
            seen.add(sel)
            if not await self._field_contains_value(sel, needle):
                continue
            if qt == "address" and self._selector_looks_wrong_for_address(sel):
                continue
            best = sel
            break
        if best:
            return best
        if qt == "address" and self._last_fill_selector and self._selector_looks_wrong_for_address(
            self._last_fill_selector
        ):
            return None
        return self._last_fill_selector

    async def _fill_address_field_fallbacks(
        self,
        val: str,
        context_vars: dict[str, Any],
    ) -> bool:
        fallbacks = [
            'input[formcontrolname*="address" i]',
            'input[placeholder*="address" i]',
            'input[placeholder*="Enter Address" i]',
            'input[aria-label*="Address" i]',
            'label:has-text("Address") + input',
            '#SiteStreetAddress',
        ]
        for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
            try:
                loc = scope.get_by_label(re.compile(r"^\s*address\s*:?\s*$", re.I)).first
                if await loc.count() > 0 and await loc.is_visible(timeout=800):
                    await loc.fill(val, timeout=2500)
                    await loc.dispatch_event("input")
                    await loc.dispatch_event("change")
                    self._last_fill_selector = 'label:has-text("Address") >> .. >> input'
                    logger.info("Filled address via get_by_label(Address)")
                    return True
            except Exception:
                continue
        for fb in fallbacks:
            for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                try:
                    loc = scope.locator(fb).first
                    if await loc.count() == 0 or not await loc.is_visible(timeout=800):
                        continue
                    await loc.fill(val, timeout=2500)
                    await loc.dispatch_event("input")
                    await loc.dispatch_event("change")
                    self._last_fill_selector = fb
                    logger.info("Filled address via fallback selector %s", fb)
                    return True
                except Exception:
                    continue
        return False

    def _interpolate_value(self, template_str: str, context_vars: dict[str, Any]) -> str:
        """Interpolates variables like {{search_url}} or {{input.parcel_id}}."""
        if not isinstance(template_str, str):
            return str(template_str)

        def replace_match(match: re.Match) -> str:
            key = match.group(1).strip()
            if key.startswith("input."):
                sub_key = key[6:]
                return str(context_vars.get("input", {}).get(sub_key, ""))
            if key.startswith("inputs."):
                sub_key = key[7:]
                return str(context_vars.get("input", {}).get(sub_key, ""))
            if key.startswith("input_data."):
                sub_key = key[11:]
                return str(context_vars.get("input", {}).get(sub_key, ""))
            if key == "search_url":
                return str(context_vars.get("search_url", ""))
            return str(context_vars.get(key, ""))

        return re.sub(r"\{\{([^}]+)\}\}", replace_match, template_str)

    def _evaluate_condition(self, condition: str, context_vars: dict[str, Any]) -> bool:
        """
        Evaluates conditional step execution.
        Supports:
          - "input.parcel_id" -> checks if input.parcel_id has a non-empty string
          - "input.address" -> checks if input.address has a non-empty string
          - "input.owner_name" -> checks if input.owner_name has a non-empty string
          - "!input.parcel_id" -> inverted condition
        """
    def _evaluate_single_condition(self, cond_str: str, context_vars: dict[str, Any]) -> bool:
        cond = cond_str.strip()
        negate = False
        if cond.startswith("!"):
            negate = True
            cond = cond[1:].strip()

        val = None
        if cond.startswith("input."):
            field = cond[6:].strip()
            val = context_vars.get("input", {}).get(field)
        elif cond.startswith("inputs."):
            field = cond[7:].strip()
            val = context_vars.get("input", {}).get(field) or context_vars.get("inputs", {}).get(field)
        elif cond.startswith("input_data."):
            field = cond[11:].strip()
            val = context_vars.get("input", {}).get(field)
        else:
            val = context_vars.get(cond)

        is_truthy = bool(val and str(val).strip())
        return not is_truthy if negate else is_truthy

    def _evaluate_condition(self, condition: Any, context_vars: dict[str, Any]) -> bool:
        """Evaluates condition string supporting '&&' (AND) and '||' (OR)."""
        if not condition or not isinstance(condition, str):
            return True

        if "||" in condition:
            parts = condition.split("||")
            return any(self._evaluate_condition(p.strip(), context_vars) for p in parts)

        if "&&" in condition:
            parts = condition.split("&&")
            return all(self._evaluate_single_condition(p.strip(), context_vars) for p in parts)

        return self._evaluate_single_condition(condition, context_vars)

    def _build_playwright_selector(self, locator: Any) -> str:
        """Converts a declarative locator strategy or string into a Playwright selector."""
        if isinstance(locator, str):
            return locator
        if not isinstance(locator, dict):
            return ""

        if locator.get("selector"):
            return str(locator["selector"])

        strategy = str(locator.get("strategy", "css")).lower().strip()
        val = str(locator.get("value", "")).strip()

        if strategy == "id":
            return f"#{val}" if not val.startswith("#") else val
        elif strategy == "css":
            return val
        elif strategy == "xpath":
            return f"xpath={val}"
        elif strategy == "name":
            return f"[name='{val}']"
        elif strategy == "placeholder":
            return f"[placeholder='{val}']"
        elif strategy == "text":
            return f"text={val}"
        elif strategy == "role":
            return f"role={val}"
        elif strategy == "test_id":
            return f"[data-testid='{val}']"
        return val

    def _is_uuid(self, val: str) -> bool:
        try:
            uuid.UUID(str(val))
            return True
        except Exception:
            return False

    async def _resolve_target_scope(self, selector: str):
        """Finds the page or child frame containing the selector."""
        if not selector:
            return self.page

        # Check main page first
        try:
            if await self.page.locator(selector).first.is_visible(timeout=800):
                return self.page
        except Exception:
            pass

        try:
            if await self.page.locator(selector).count() > 0:
                return self.page
        except Exception:
            pass

        # Check child frames (e.g. government framesets / iframes)
        for f in self.page.frames:
            if f == self.page.main_frame:
                continue
            try:
                if await f.locator(selector).first.is_visible(timeout=800):
                    return f
            except Exception:
                continue
            try:
                if await f.locator(selector).count() > 0:
                    return f
            except Exception:
                continue

        return self.page

    async def _dismiss_disclaimer_if_present(self) -> None:
        """Auto-detects and dismisses government portal disclaimer/modal dynamically using AI."""
        try:
            from app.browser.modal_detector import (
                detect_and_dismiss_modal_ai,
                dismiss_blocking_overlays_heuristic,
            )

            await dismiss_blocking_overlays_heuristic(self.page)
            await detect_and_dismiss_modal_ai(self.page, workflow_run_id=self.run_id, node_id=self.node_id)
            await dismiss_blocking_overlays_heuristic(self.page)
        except Exception as e:
            logger.debug("Modal dismissal pre-check note: %s", e)

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context_vars: dict[str, Any],
        extract_schema: dict[str, Any] | None = None,
        table_settings: dict[str, Any] | None = None,
        *,
        security_barrier_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Executes a sequence of recipe steps with continuous security checks.
        Supports standard forms, dropdowns, SPAs, and GET/POST searches.

        When ``security_barrier_mode`` is True, pre/post-action security halts are
        suppressed so Cloudflare/Turnstile/CAPTCHA recovery steps can run.
        """
        logger.info("Starting execution of %d steps for run %s node %s", len(steps), self.run_id, self.node_id)

        for idx, step in enumerate(steps):
            # Pre-flight liveness check on page
            if self.page is None or (callable(getattr(self.page, "is_closed", None)) and self.page.is_closed() is True):
                raise StepExecutionException(
                    message=f"Browser page is closed or inoperable prior to step {idx}",
                    step_index=idx,
                    action=str(step.get("action", "")),
                )

            # Evaluate conditional execution
            condition = step.get("condition")
            if condition and not self._evaluate_condition(condition, context_vars):
                logger.info(
                    "Skipping step [%d/%d] '%s': condition '%s' evaluated to false",
                    idx + 1, len(steps), step.get("description", step.get("action")), condition
                )
                continue

            action = str(step.get("action", "")).lower().strip()
            timeout_ms = int(step.get("timeout_ms") or settings.PLAYWRIGHT_ACTION_TIMEOUT_MS)
            step_desc = step.get("description") or f"{action.replace('_', ' ').capitalize()}"
            step_start = time.time()
            step_url = self.page.url if (self.page and hasattr(self.page, "url")) else None

            logger.info("Executing step [%d/%d]: %s (%s)", idx + 1, len(steps), action, step_desc)

            try:
                bm = await get_browser_manager()
                await bm.log_session_activity(
                    action=action,
                    description=step_desc,
                    node_id=self.node_id,
                    step_index=idx,
                    status="RUNNING",
                    url=step_url,
                )
            except Exception:
                pass

            # Continuous pre-action security inspection (skipped during barrier recovery)
            if not security_barrier_mode:
                sec_dom = await SecurityDetector.inspect_page(self.page)
                if sec_dom.is_detected:
                    evidence = await EvidenceCollector.capture_evidence(
                        self.page, self.run_id, self.node_id, reason=sec_dom.reason
                    )
                    raise SecurityChallengeException(sec_dom)

            loc = step.get("locator") or step.get("selector") or {}
            selector = self._build_playwright_selector(loc)

            try:
                # -------------------------------------------------------------
                # 1. Navigation & Search Actions
                # -------------------------------------------------------------
                if action == "navigate":
                    url = self._interpolate_value(step.get("url", ""), context_vars)
                    URLValidator.validate_url(url)

                    from app.browser.navigation import navigate_portal_step_by_step
                    response = await navigate_portal_step_by_step(
                        page=self.page,
                        url=url,
                        timeout_ms=timeout_ms,
                        workflow_run_id=str(self.run_id),
                        node_id=str(self.node_id),
                        wait_for_mount=True,
                    )

                    if not security_barrier_mode:
                        # Security inspection on navigation response if available
                        if response:
                            sec_resp = await SecurityDetector.inspect_response(response, url)
                            if sec_resp and sec_resp.is_detected:
                                evidence = await EvidenceCollector.capture_evidence(
                                    self.page, self.run_id, self.node_id, reason=sec_resp.reason
                                )
                                raise SecurityChallengeException(sec_resp)

                        # Security inspection on DOM post-load
                        sec_dom = await SecurityDetector.inspect_page(self.page)
                        if sec_dom.is_detected:
                            evidence = await EvidenceCollector.capture_evidence(
                                self.page, self.run_id, self.node_id, reason=sec_dom.reason
                            )
                            raise SecurityChallengeException(sec_dom)

                    # Step-by-step wait for portal stabilization
                    await self.page.wait_for_timeout(1200)

                elif action == "get_search":
                    base_url = self._interpolate_value(step.get("url", ""), context_vars)
                    raw_params = step.get("params", {})
                    query_params = {
                        k: self._interpolate_value(str(v), context_vars)
                        for k, v in raw_params.items()
                    }
                    separator = "&" if "?" in base_url else "?"
                    full_get_url = f"{base_url}{separator}{urlencode(query_params)}" if query_params else base_url
                    URLValidator.validate_url(full_get_url)

                    logger.info("Executing GET search: %s", full_get_url)

                    async def _get_nav():
                        return await self.page.goto(full_get_url, wait_until="domcontentloaded", timeout=timeout_ms)

                    response = await retry_manager.execute(
                        _get_nav,
                        policy=BROWSER_NAVIGATION_POLICY,
                        operation_name="browser_get_search",
                        operation_id=full_get_url,
                        workflow_run_id=uuid.UUID(str(self.run_id)) if self._is_uuid(self.run_id) else None,
                    )
                    sec_resp = await SecurityDetector.inspect_response(response, full_get_url)
                    if sec_resp and sec_resp.is_detected:
                        evidence = await EvidenceCollector.capture_evidence(
                            self.page, self.run_id, self.node_id, reason=sec_resp.reason
                        )
                        raise SecurityChallengeException(sec_resp)

                    await self._dismiss_disclaimer_if_present()

                # -------------------------------------------------------------
                # 2. Form Inputs & Interactions (Frame-Aware)
                # -------------------------------------------------------------
                elif action == "expand_search_section":
                    target_sel = (step.get("selector") or selector or "").strip()
                    if target_sel:
                        from app.browser.page_search_ai import ensure_search_panel_for_selector

                        if await ensure_search_panel_for_selector(self.page, target_sel):
                            logger.info("Expanded search panel for %s", target_sel)
                            await self.page.wait_for_timeout(400)

                elif action == "fill":
                    raw_val = step.get("value", "")
                    val = self._interpolate_value(raw_val, context_vars)
                    target = await self._resolve_target_scope(selector)
                    try:
                        from app.browser.page_search_ai import ensure_search_panel_for_selector

                        await ensure_search_panel_for_selector(self.page, selector)
                        await self.page.wait_for_timeout(400)
                        await target.fill(selector, val, timeout=min(timeout_ms, 4000))
                        try:
                            await target.locator(selector).first.dispatch_event("input")
                            await target.locator(selector).first.dispatch_event("change")
                            await target.locator(selector).first.dispatch_event("blur")
                            await target.locator(selector).first.evaluate(
                                """(el, v) => {
                                    if (window.jQuery) { window.jQuery(el).trigger('input').trigger('change').trigger('blur'); }
                                    if (window.ko) {
                                        try {
                                            const ctx = window.ko.contextFor(el);
                                            if (ctx && ctx.$data) {
                                                for (let k of Object.keys(ctx.$data)) {
                                                    if (typeof ctx.$data[k] === 'function' && ctx.$data[k].length <= 1) {
                                                        const b = el.getAttribute('data-bind') || '';
                                                        if (b.includes(k)) ctx.$data[k](v);
                                                    }
                                                }
                                            }
                                        } catch(e) {}
                                    }
                                }""",
                                val,
                            )
                        except Exception:
                            pass
                        self._last_fill_selector = selector
                        await self.page.wait_for_timeout(800)
                        qt = (context_vars.get("active_query_type") or "").lower()
                        wrong_field = qt == "address" and self._selector_looks_wrong_for_address(selector)
                        missing_val = qt == "address" and not await self._field_contains_value(selector, val)
                        if qt == "address" and (wrong_field or missing_val):
                            if await self._fill_address_field_fallbacks(val, context_vars):
                                pass
                    except Exception as fill_err:
                        logger.warning("Direct fill for %s failed (%s), attempting dynamic selector fallback", selector, fill_err)
                        fallbacks = []
                        sel_lower = selector.lower()
                        if "parcel" in sel_lower or "folio" in sel_lower:
                            fallbacks = [
                                'input[placeholder*="folio" i]', 'input[placeholder*="Enter Folio" i]',
                                'input[formcontrolname*="folio" i]', 'input[id*="folio" i]', 'input[name*="folio" i]',
                                'input[placeholder*="parcel" i]', 'input[placeholder*="Enter Parcel" i]',
                                'input[formcontrolname*="parcel" i]', 'input[name*="parcel" i]', '#ParcelID',
                            ]
                        elif "address" in sel_lower or "street" in sel_lower:
                            fallbacks = [
                                'input[placeholder*="address" i]', 'input[placeholder*="Enter Address" i]',
                                'input[formcontrolname*="address" i]', 'input[name*="address" i]', '#SiteStreetAddress',
                            ]
                        elif "owner" in sel_lower:
                            fallbacks = [
                                'input[placeholder*="owner" i]', 'input[placeholder*="Enter Owner" i]',
                                'input[formcontrolname*="owner" i]', 'input[name*="owner" i]', '#OwnerLine1',
                            ]
                        elif "enter address" in sel_lower or 'placeholder="enter address' in sel_lower:
                            fallbacks = [
                                'input[placeholder*="enter address" i]',
                                'input[aria-label*="Location Address" i]',
                                'input[id*="txtAddress" i]',
                                'input[name*="txtAddress" i]',
                            ]

                        filled_fb = False
                        for fb in fallbacks:
                            for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                try:
                                    if await scope.locator(fb).count() > 0 and await scope.locator(fb).first.is_visible(timeout=1000):
                                        await scope.locator(fb).first.fill(val, timeout=2000)
                                        try:
                                            await scope.locator(fb).first.dispatch_event("input")
                                            await scope.locator(fb).first.dispatch_event("change")
                                        except Exception:
                                            pass
                                        self._last_fill_selector = fb
                                        filled_fb = True
                                        logger.info("Successfully filled fallback selector %s", fb)
                                        await self.page.wait_for_timeout(800)
                                        break
                                except Exception:
                                    continue
                            if filled_fb:
                                break
                        if not filled_fb:
                            raise fill_err

                    # Dynamic Autocomplete / Suggestion Dropdown Handling
                    try:
                        from app.browser.autocomplete_handler import detect_and_select_autocomplete
                        await detect_and_select_autocomplete(
                            self.page,
                            expected_value=val,
                            workflow_run_id=str(self.run_id),
                            node_id=str(self.node_id),
                        )
                    except Exception as ac_err:
                        logger.debug("Step runner autocomplete note: %s", ac_err)

                elif action == "type":
                    raw_val = step.get("value", "")
                    val = self._interpolate_value(raw_val, context_vars)
                    delay = int(step.get("delay_ms", 30))
                    target = await self._resolve_target_scope(selector)
                    await target.type(selector, val, delay=delay, timeout=timeout_ms)

                    # Dynamic Autocomplete / Suggestion Dropdown Handling
                    try:
                        from app.browser.autocomplete_handler import detect_and_select_autocomplete
                        await detect_and_select_autocomplete(
                            self.page,
                            expected_value=val,
                            workflow_run_id=str(self.run_id),
                            node_id=str(self.node_id),
                        )
                    except Exception as ac_err:
                        logger.debug("Step runner autocomplete note: %s", ac_err)

                elif action == "click":
                    if not selector:
                        logger.debug("Skipping click action: selector is empty")
                        continue
                    target = await self._resolve_target_scope(selector)
                    desc_lower = str(step.get("description", "")).lower()
                    sel_lower = selector.lower()
                    is_search_submit = any(
                        k in desc_lower or k in sel_lower
                        for k in ("search", "submit", "btnsarch", "btnsearch")
                    )
                    is_tab = "tab" in sel_lower or "ui-id-" in sel_lower or "tab" in desc_lower

                    clicked = False
                    qt_submit = (context_vars.get("active_query_type") or "").lower()
                    query_val = (
                        (context_vars.get("input") or {}).get("address")
                        or (context_vars.get("input") or {}).get("parcel_id")
                        or (context_vars.get("input") or {}).get("owner_name")
                        or ""
                    )
                    submit_fill_sel = await self._resolve_submit_fill_selector(qt_submit, str(query_val))
                    if submit_fill_sel:
                        self._last_fill_selector = submit_fill_sel
                    if is_search_submit and self._last_fill_selector:
                        try:
                            from app.browser.page_search_ai import SINGLE_FIELD_SEARCH_PREP_JS

                            for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                try:
                                    await scope.evaluate(
                                        SINGLE_FIELD_SEARCH_PREP_JS,
                                        self._last_fill_selector,
                                    )
                                    break
                                except Exception:
                                    continue
                        except Exception as prep_err:
                            logger.debug("Single-field search prep note: %s", prep_err)

                    if is_search_submit:
                        await self._dismiss_disclaimer_if_present()
                        try:
                            btn = target.locator(selector).first
                            await btn.click(force=True, timeout=min(timeout_ms, 5000))
                            clicked = True
                            logger.info("Clicked search submit control %s", selector)
                        except Exception as click_primary_err:
                            logger.debug("Primary search click %s: %s", selector, click_primary_err)
                            try:
                                await target.locator(selector).first.evaluate(
                                    "el => { el.click(); if (typeof el.form?.requestSubmit === 'function') el.form.requestSubmit(el); }"
                                )
                                clicked = True
                            except Exception:
                                pass

                    if not clicked and is_search_submit and self._last_fill_selector:
                        try:
                            from app.browser.page_search_ai import PAIR_SUBMIT_CLICK_JS

                            for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                try:
                                    paired = await scope.evaluate(PAIR_SUBMIT_CLICK_JS, self._last_fill_selector)
                                    if paired:
                                        clicked = True
                                        logger.info(
                                            "Clicked Search in same panel as filled input %s",
                                            self._last_fill_selector,
                                        )
                                        break
                                except Exception:
                                    continue
                        except Exception as pair_err:
                            logger.debug("Paired panel Search click note: %s", pair_err)

                    if not clicked and is_tab:
                        # Extract target tab panel id if present (e.g., #TabParcel, #TabAddress, #TabOwner)
                        tab_match = re.search(r'#Tab([a-zA-Z0-9_]+)', selector, re.I)
                        target_panel_id = f"Tab{tab_match.group(1)}" if tab_match else None
                        if target_panel_id:
                            try:
                                await target.evaluate(
                                    f"""() => {{
                                        const targetId = "{target_panel_id}";
                                        const panels = ['TabOwner', 'TabAddress', 'TabParcel', 'TabSubCondo', 'TabRadius', 'TabAdvanced'];
                                        panels.forEach(id => {{
                                            const el = document.getElementById(id);
                                            if (el) {{
                                                el.style.display = (id.toLowerCase() === targetId.toLowerCase()) ? '' : 'none';
                                                el.setAttribute('aria-hidden', (id.toLowerCase() === targetId.toLowerCase()) ? 'false' : 'true');
                                            }}
                                        }});
                                        const activeTabLink = document.querySelector('a[href="#' + targetId + '"]') || document.querySelector('a[href*="' + targetId + '"]');
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
                                            const idx = panels.findIndex(p => p.toLowerCase() === targetId.toLowerCase());
                                            if (idx >= 0) window.$tabs.tabs("option", "active", idx);
                                        }}
                                    }}"""
                                )
                            except Exception:
                                pass
                        try:
                            await target.click(selector, force=True, timeout=min(timeout_ms, 3000))
                        except Exception:
                            # If on a homepage with Property Search link, click through and re-attempt
                            for f in self.page.frames:
                                try:
                                    search_nav = f.locator("a:has-text('Property Search'), button:has-text('Property Search')").first
                                    if await search_nav.is_visible(timeout=800):
                                        logger.info("Clicking Property Search link to navigate from landing page to search portal...")
                                        await search_nav.click()
                                        await self.page.wait_for_timeout(2000)
                                        target = await self._resolve_target_scope(selector)
                                        break
                                except Exception:
                                    pass
                            await target.click(selector, force=True, timeout=5000)
                        await self.page.wait_for_timeout(600)
                    elif not clicked:
                        try:
                            await target.click(selector, timeout=timeout_ms)
                        except Exception as click_err:
                            desc_lower = str(step.get("description", "")).lower()
                            sel_lower = selector.lower()
                            if any(k in sel_lower or k in desc_lower for k in ["search", "submit", "btn", "find"]):
                                logger.warning(
                                    "Click on search button %s failed (%s). Attempting fallback search buttons or Enter key.",
                                    selector, click_err
                                )
                                clicked_fb = False
                                if self._last_fill_selector:
                                    try:
                                        from app.browser.page_search_ai import PAIR_SUBMIT_CLICK_JS

                                        for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                            paired = await scope.evaluate(PAIR_SUBMIT_CLICK_JS, self._last_fill_selector)
                                            if paired:
                                                clicked_fb = True
                                                break
                                    except Exception:
                                        pass
                                fallback_btns = [
                                    '#search',
                                    'div#search[role="button"]',
                                    '[role="button"]:has-text("Search")',
                                    'input[type="submit"][value*="Search" i]',
                                    'input[type="button"][value*="Search" i]',
                                    'button[type="submit"]',
                                    'input[type="submit"]',
                                    'a[id*="btnSearch" i]',
                                    'button.btn-primary',
                                ]
                                for fb in fallback_btns:
                                    for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                        try:
                                            loc = scope.locator(fb).first
                                            if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                                                await loc.click(force=True, timeout=2500)
                                                clicked_fb = True
                                                logger.info("Clicked fallback search button %s", fb)
                                                break
                                        except Exception:
                                            continue
                                    if clicked_fb:
                                        break
                                if not clicked_fb:
                                    try:
                                        from app.extraction.tax_collector_reports import TAX_UNIFIED_SUBMIT_JS

                                        for scope in [self.page, *[f for f in self.page.frames if f != self.page.main_frame]]:
                                            pick = await scope.evaluate(TAX_UNIFIED_SUBMIT_JS)
                                            if pick and pick.get("selector"):
                                                loc = scope.locator(pick["selector"]).first
                                                if await loc.count() > 0 and await loc.is_visible(timeout=800):
                                                    await loc.click(force=True, timeout=3000)
                                                    clicked_fb = True
                                                    logger.info(
                                                        "Clicked dynamically scored Search control %s",
                                                        pick["selector"],
                                                    )
                                                    break
                                    except Exception as dyn_submit_err:
                                        logger.debug("Dynamic Search submit fallback: %s", dyn_submit_err)
                                if not clicked_fb:
                                    try:
                                        from app.browser.page_search_ai import INVOKE_PORTAL_SUBMIT_JS

                                        for scope in [
                                            self.page,
                                            *[f for f in self.page.frames if f != self.page.main_frame],
                                        ]:
                                            if await scope.evaluate(INVOKE_PORTAL_SUBMIT_JS):
                                                clicked_fb = True
                                                logger.info("Invoked portal submitForm / #search control")
                                                break
                                    except Exception as portal_submit_err:
                                        logger.debug("Portal submitForm fallback: %s", portal_submit_err)
                                if not clicked_fb:
                                    try:
                                        await self.page.keyboard.press("Enter")
                                        logger.info("Pressed Enter key to submit search as fallback")
                                    except Exception:
                                        raise click_err
                            else:
                                raise click_err

                    # Step-by-step wait after click: if search/submit, wait for results to load
                    desc_lower = str(step.get("description", "")).lower()
                    if any(term in desc_lower or term in selector.lower() for term in ["search", "submit", "find", "filter", "go"]):
                        logger.info("Search/submit button clicked. Waiting step-by-step for results to load...")
                        await self.page.wait_for_timeout(2500)
                    else:
                        await self.page.wait_for_timeout(1000)

                    if not security_barrier_mode:
                        # Post-click security check
                        sec_dom = await SecurityDetector.inspect_page(self.page)
                        if sec_dom.is_detected:
                            evidence = await EvidenceCollector.capture_evidence(
                                self.page, self.run_id, self.node_id, reason=sec_dom.reason
                            )
                            raise SecurityChallengeException(sec_dom)

                elif action == "click_if_visible":
                    target = await self._resolve_target_scope(selector)
                    clicked_civ = False
                    sel_lower_civ = (selector or "").lower()
                    if "btnheadertoggle" in sel_lower_civ:
                        from app.browser.page_search_ai import ensure_search_panel_for_selector

                        if await ensure_search_panel_for_selector(self.page, selector):
                            await self.page.wait_for_timeout(450)
                        continue
                    parcel_q = (
                        str((context_vars.get("input") or {}).get("parcel_id") or "")
                        .strip()
                    )
                    address_q = (
                        str((context_vars.get("input") or {}).get("address") or "")
                        .strip()
                    )
                    step_desc = str(step.get("description") or "").lower()
                    is_result_nav = any(
                        k in step_desc
                        for k in (
                            "result",
                            "parcel",
                            "property appraisal",
                            "matching",
                            "detail",
                            "record",
                        )
                    )

                    from app.browser.property_capture_prep import is_gis_portal_url

                    gis_parcel_open = (
                        parcel_q
                        and is_gis_portal_url(self.page.url or "")
                        and "disclaimer" not in step_desc
                        and "agree" not in step_desc
                        and (is_result_nav or "result" in selector.lower() or "tbody tr" in selector.lower())
                    )
                    if gis_parcel_open:
                        try:
                            from app.browser.property_capture_prep import open_gis_search_result_row

                            gis_click = await open_gis_search_result_row(
                                self.page,
                                parcel_q,
                                query_type=str(context_vars.get("active_query_type") or "parcel"),
                                workflow_run_id=str(self.run_id),
                                node_id=str(self.node_id),
                            )
                            if gis_click.get("clicked"):
                                clicked_civ = True
                                step["description"] = gis_click.get("description") or step.get("description")
                                step_desc = step["description"]
                        except Exception as gis_err:
                            logger.debug("GIS parcel row open note: %s", gis_err)

                    preferred: list[str] = []
                    if parcel_q:
                        preferred.extend(
                            [
                                f'a:has-text("{parcel_q}")',
                                f'td a:has-text("{parcel_q}")',
                                f'td:has-text("{parcel_q}")',
                                f'tr:has-text("{parcel_q}") a',
                            ]
                        )
                    if address_q:
                        street = address_q.split(",")[0].strip()
                        if street:
                            preferred.append(f'tr:has-text("{street}") a')
                            preferred.append(f'a:has-text("{street}")')

                    generic_parts = [p.strip() for p in selector.split(",") if p.strip()]
                    blocked_generic = (
                        "table tbody tr",
                        "table tbody tr:first-child",
                        "table tbody tr td:nth-child(2)",
                        "table tr:not(:first-child) td a",
                    )
                    generic_parts = [p for p in generic_parts if p not in blocked_generic]
                    selector_list = preferred + generic_parts if not clicked_civ else []
                    for scope in ([target, self.page, *self.page.frames] if not clicked_civ else []):
                        try:
                            for s in selector_list:
                                s_clean = s.strip()
                                if not s_clean:
                                    continue
                                loc = scope.locator(s_clean).first
                                if await loc.count() == 0 or not await loc.is_visible(timeout=min(1200, timeout_ms)):
                                    continue
                                try:
                                    cell_text = (await loc.inner_text(timeout=1200) or "").strip()
                                except Exception:
                                    cell_text = ""
                                low = cell_text.lower()
                                if parcel_q and parcel_q not in cell_text:
                                    if any(
                                        k in low
                                        for k in (
                                            "certified values",
                                            "property appraiser 20",
                                            "beta off",
                                            "record search",
                                        )
                                    ):
                                        continue
                                try:
                                    await loc.evaluate(
                                        "el => { el.click(); if (typeof el.onclick === 'function') { el.onclick(); } }"
                                    )
                                    clicked_civ = True
                                except Exception:
                                    try:
                                        await loc.click(force=True, timeout=2500)
                                        clicked_civ = True
                                    except Exception:
                                        pass
                                if clicked_civ:
                                    await self.page.wait_for_timeout(1000)
                                    break
                            if clicked_civ:
                                break
                        except Exception:
                            continue

                elif action == "double_click":
                    target = await self._resolve_target_scope(selector)
                    await target.dblclick(selector, timeout=timeout_ms)

                elif action == "check":
                    target = await self._resolve_target_scope(selector)
                    await target.check(selector, timeout=timeout_ms)

                elif action == "uncheck":
                    target = await self._resolve_target_scope(selector)
                    await target.uncheck(selector, timeout=timeout_ms)

                elif action == "select":
                    # Support value, label, or index
                    target = await self._resolve_target_scope(selector)
                    if step.get("auto_tax_year"):
                        loc = target.locator(selector).first
                        if await loc.count() > 0:
                            from app.browser.page_search_ai import PICK_TAX_YEAR_JS
                            meta = await target.evaluate(PICK_TAX_YEAR_JS)
                            preferred = int(meta.get("preferred") or meta.get("nowYear") or 2026)
                            await loc.evaluate(
                                """(el, preferredYear) => {
                                    const opts = Array.from(el.options || []);
                                    let best = null, bestY = -1;
                                    for (const o of opts) {
                                        const y = parseInt((o.value || o.textContent || '').trim(), 10);
                                        if (!y || y > preferredYear) continue;
                                        if (y > bestY) { bestY = y; best = o; }
                                    }
                                    if (!best && opts.length) best = opts[0];
                                    if (!best) return;
                                    el.value = best.value;
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                }""",
                                preferred,
                            )
                    elif "value" in step:
                        val = self._interpolate_value(step.get("value", ""), context_vars)
                        await target.select_option(selector, value=val, timeout=timeout_ms)
                    elif "label" in step:
                        lbl = self._interpolate_value(step.get("label", ""), context_vars)
                        await target.select_option(selector, label=lbl, timeout=timeout_ms)
                    elif "index" in step:
                        idx_val = int(step["index"])
                        await target.select_option(selector, index=idx_val, timeout=timeout_ms)
                    else:
                        val = self._interpolate_value(step.get("value", ""), context_vars)
                        await target.select_option(selector, val, timeout=timeout_ms)

                elif action == "submit":
                    # Submit form by locator or pressing Enter
                    target = await self._resolve_target_scope(selector)
                    if selector:
                        form = target.locator(selector).first
                        await form.press("Enter")
                    else:
                        await self.page.keyboard.press("Enter")

                # -------------------------------------------------------------
                # 3. SPA & Waiting Actions (Frame-Aware)
                # -------------------------------------------------------------
                elif action == "wait":
                    ms = step.get("duration") or step.get("timeout_ms") or 1000
                    await self.page.wait_for_timeout(ms)

                elif action == "wait_for_selector":
                    target_state = step.get("state", "visible")
                    # Poll across main page and frames
                    found = False
                    start_wait = time.time()
                    deadline = start_wait + (timeout_ms / 1000.0)
                    raw_candidates = [s.strip() for s in selector.split(",") if s.strip()]
                    while time.time() < deadline:
                        for scope in [self.page] + [f for f in self.page.frames if f != self.page.main_frame]:
                            for cand in raw_candidates:
                                try:
                                    loc = scope.locator(cand).first
                                    if target_state == "visible":
                                        if await loc.is_visible(timeout=400):
                                            found = True
                                            break
                                    else:
                                        if await loc.count() > 0:
                                            found = True
                                            break
                                except Exception:
                                    continue
                            if found:
                                break
                        if found:
                            break
                        await asyncio.sleep(0.3)
                    if not found:
                        if step.get("optional") is True:
                            logger.info(
                                "Optional wait_for_selector did not find '%s' within %sms — continuing",
                                selector,
                                timeout_ms,
                            )
                        else:
                            await self.page.wait_for_selector(
                                raw_candidates[0] if raw_candidates else selector,
                                state=target_state,
                                timeout=min(3000, timeout_ms),
                            )

                elif action == "wait_for_load_state":
                    load_state = step.get("state", "domcontentloaded")
                    await self.page.wait_for_load_state(load_state, timeout=timeout_ms)

                elif action == "screenshot":
                    await EvidenceCollector.capture_evidence(
                        self.page, self.run_id, self.node_id, reason="Step-requested checkpoint"
                    )

                # Record successful step completion
                duration_ms = int((time.time() - step_start) * 1000)
                curr_url = self.page.url if (self.page and hasattr(self.page, "url")) else step_url
                try:
                    bm = await get_browser_manager()
                    await bm.log_session_activity(
                        action=action,
                        description=step_desc,
                        node_id=self.node_id,
                        step_index=idx,
                        status="COMPLETED",
                        url=curr_url,
                        duration_ms=duration_ms,
                    )
                except Exception:
                    pass

                try:
                    from app.browser.step_verifier_ai import verify_and_recover_after_step

                    await verify_and_recover_after_step(
                        self.page,
                        action=action,
                        step_desc=step_desc,
                        step_index=idx,
                        context_vars=context_vars,
                        workflow_run_id=str(self.run_id),
                        node_id=str(self.node_id),
                        use_ai=True,
                    )
                except Exception as ver_err:
                    logger.debug("Step verify note: %s", ver_err)

                # Step-by-step deliberate pacing delay between actions so steps are stable and observable
                await self.page.wait_for_timeout(800)

            except SecurityChallengeException as sec_exc:
                duration_ms = int((time.time() - step_start) * 1000)
                try:
                    bm = await get_browser_manager()
                    await bm.log_session_activity(
                        action=action,
                        description=step_desc,
                        node_id=self.node_id,
                        step_index=idx,
                        status="FAILED",
                        error=f"Security Challenge: {sec_exc.result.reason}",
                        url=self.page.url if (self.page and hasattr(self.page, "url")) else step_url,
                        duration_ms=duration_ms,
                    )
                except Exception:
                    pass
                raise
            except Exception as exc:
                if step.get("optional") is True:
                    logger.warning("Optional step [%d/%d] '%s' failed, skipping: %s", idx + 1, len(steps), step_desc, exc)
                    continue

                duration_ms = int((time.time() - step_start) * 1000)
                try:
                    bm = await get_browser_manager()
                    await bm.log_session_activity(
                        action=action,
                        description=step_desc,
                        node_id=self.node_id,
                        step_index=idx,
                        status="FAILED",
                        error=str(exc),
                        url=self.page.url if (self.page and hasattr(self.page, "url")) else step_url,
                        duration_ms=duration_ms,
                    )
                except Exception:
                    pass

                evidence = await EvidenceCollector.capture_evidence(
                    self.page, self.run_id, self.node_id, reason=f"Step {idx} ({action}) failed: {str(exc)}"
                )
                raise StepExecutionException(
                    message=f"Browser action '{action}' failed at step {idx}: {str(exc)}",
                    step_index=idx,
                    action=action,
                    evidence=evidence,
                ) from exc

        # ---------------------------------------------------------------------
        # 4. Result Extraction
        # ---------------------------------------------------------------------
        extracted_results: dict[str, Any] = {}

        pre_query_val = (
            context_vars.get("input", {}).get("parcel_id")
            or context_vars.get("input", {}).get("address")
            or context_vars.get("input", {}).get("owner_name")
            or ""
        )
        pre_query_type = (
            context_vars.get("active_query_type")
            or ("address" if context_vars.get("input", {}).get("address") else None)
            or ("owner" if context_vars.get("input", {}).get("owner_name") else None)
            or "parcel"
        )

        from app.browser.property_capture_prep import (
            is_gis_portal_url,
            open_gis_search_result_row,
            wait_for_property_detail_ready,
        )

        if pre_query_val and is_gis_portal_url(self.page.url or ""):
            if not await wait_for_property_detail_ready(self.page, query_value=str(pre_query_val), timeout_ms=2500):
                opened = await open_gis_search_result_row(
                    self.page,
                    str(pre_query_val),
                    query_type=str(pre_query_type),
                    workflow_run_id=str(self.run_id),
                    node_id=str(self.node_id),
                )
                if opened.get("clicked"):
                    logger.info("GIS parcel detail before extract: %s", opened.get("description"))

        # If table settings are configured, extract table rows
        if table_settings and (table_settings.get("table_selector") or table_settings.get("row_selector")):
            records = await self.extractor.extract_table_results(
                table_settings,
                field_mappings=extract_schema or {},
                max_records=int(table_settings.get("max_records", 25)),
            )
            extracted_results["_records"] = records
            # If records were found, populate top record into primary fields
            if records:
                top_record = records[0]
                for k, v in top_record.items():
                    if not k.startswith("_"):
                        extracted_results[k] = v

        # If direct field mappings are configured, extract fields from DOM
        if extract_schema:
            dom_fields = await self.extractor.extract_fields_from_dom(extract_schema)
            for k, v in dom_fields.items():
                if k not in extracted_results or extracted_results[k] is None:
                    extracted_results[k] = v

        # Check if search returned a results list/table and click matching parcel number or detail link
        query_val = (
            context_vars.get("input", {}).get("parcel_id")
            or context_vars.get("input", {}).get("address")
            or context_vars.get("input", {}).get("owner_name")
            or ""
        )
        query_type = (
            context_vars.get("active_query_type")
            or ("address" if context_vars.get("input", {}).get("address") else None)
            or ("owner" if context_vars.get("input", {}).get("owner_name") else None)
            or "parcel"
        )
        owner_val = str(extracted_results.get("owner_name") or "").strip().upper()
        dummy_owners = {"", "NONE", "NULL", "N/A", "UNKNOWN", "OWNER OF RECORD", "OWNER", "AND", "OR"}
        has_genuine_owner = bool(owner_val and owner_val not in dummy_owners)

        from app.browser.property_capture_prep import (
            is_gis_portal_url,
            open_gis_search_result_row,
            wait_for_property_detail_ready,
        )

        from app.browser.property_capture_prep import page_is_search_results_listing

        on_results_listing = await page_is_search_results_listing(self.page)
        detail_ready = await wait_for_property_detail_ready(
            self.page, query_value=str(query_val or ""), timeout_ms=2500
        )
        needs_result_click = on_results_listing or (
            not detail_ready and (not has_genuine_owner or not extracted_results.get("property_address"))
        )
        if needs_result_click:
            if query_val and is_gis_portal_url(self.page.url or ""):
                gis_open = await open_gis_search_result_row(
                    self.page,
                    str(query_val),
                    query_type=query_type,
                    workflow_run_id=str(self.run_id),
                    node_id=str(self.node_id),
                )
                if gis_open.get("clicked"):
                    logger.info("GIS search result opened: %s", gis_open.get("description"))
            else:
                try:
                    from app.browser.result_selector_ai import detect_and_click_result_link_ai

                    res_click = await detect_and_click_result_link_ai(
                        self.page,
                        query_value=query_val,
                        query_type=query_type,
                        workflow_run_id=str(self.run_id),
                        node_id=str(self.node_id),
                    )
                    if res_click.get("clicked"):
                        await self.page.wait_for_timeout(2000)
                except Exception as res_err:
                    logger.debug("Step runner result selector note: %s", res_err)

        # Universal county property DOM evaluation if primary fields still missing
        if not extracted_results.get("owner_name") or not extracted_results.get("property_address"):
            for scope in [self.page] + [f for f in self.page.frames if f != self.page.main_frame]:
                try:
                    from app.browser.page_search_ai import PROPERTY_SCRAPE_JS
                    dom_data = await scope.evaluate(PROPERTY_SCRAPE_JS)
                    if dom_data and isinstance(dom_data, dict):
                        if not extracted_results.get("owner_name") and dom_data.get("owner name"):
                            extracted_results["owner_name"] = dom_data["owner name"]
                        from app.browser.property_capture_prep import is_map_ui_noise, sanitize_extracted_record

                        site = dom_data.get("site address")
                        if not extracted_results.get("property_address") and site and not is_map_ui_noise(str(site)):
                            extracted_results["property_address"] = site
                        extracted_results = sanitize_extracted_record(
                            extracted_results,
                            query_value=str(context_vars.get("input", {}).get("parcel_id") or context_vars.get("input", {}).get("address") or ""),
                            query_type=str(context_vars.get("active_query_type") or "parcel"),
                        )
                        if not extracted_results.get("parcel_number") and dom_data.get("parcel id"):
                            extracted_results["parcel_number"] = dom_data["parcel id"]
                        if not extracted_results.get("assessed_value") and (dom_data.get("assessed value") or dom_data.get("market value")):
                            extracted_results["assessed_value"] = dom_data.get("assessed value") or dom_data.get("market value")
                        if not extracted_results.get("legal_description") and dom_data.get("legal description"):
                            extracted_results["legal_description"] = dom_data["legal description"]
                except Exception:
                    continue

        # Mistral Document AI OCR visual extraction if fields still missing (once per run)
        need_ocr = (
            not extracted_results.get("ocr_markdown")
            and (
                not extracted_results.get("owner_name")
                or not extracted_results.get("property_address")
            )
        )
        if need_ocr:
            try:
                from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
                ocr_srv = get_mistral_webpage_ocr()
                if ocr_srv.is_enabled():
                    query_val = str(
                        (context_vars.get("input") or {}).get("parcel_id")
                        or (context_vars.get("input") or {}).get("address")
                        or ""
                    )
                    ocr_res = await ocr_srv.extract_from_page(self.page, query_value=query_val)
                    for k in [
                        "parcel_number", "owner_name", "property_address", "mailing_address",
                        "legal_description", "property_use", "taxing_district", "land_size",
                        "exemptions", "map_number", "just_value", "assessed_value", "taxable_value",
                    ]:
                        v = ocr_res.get(k)
                        if v and not extracted_results.get(k):
                            extracted_results[k] = v
                    if ocr_res.get("raw_markdown") and not extracted_results.get("ocr_markdown"):
                        extracted_results["ocr_markdown"] = ocr_res["raw_markdown"]
            except Exception:
                pass

        from app.browser.property_capture_prep import finalize_assessor_extraction

        return finalize_assessor_extraction(
            extracted_results,
            query_value=str(pre_query_val or ""),
            query_type=str(pre_query_type or "parcel"),
        )
