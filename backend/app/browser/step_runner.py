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
            from app.browser.modal_detector import detect_and_dismiss_modal_ai
            await detect_and_dismiss_modal_ai(self.page, workflow_run_id=self.run_id, node_id=self.node_id)
        except Exception as e:
            logger.debug("Modal dismissal pre-check note: %s", e)

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context_vars: dict[str, Any],
        extract_schema: dict[str, Any] | None = None,
        table_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Executes a sequence of recipe steps with continuous security checks.
        Supports standard forms, dropdowns, SPAs, and GET/POST searches.
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

            # Continuous pre-action security inspection
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
                elif action == "fill":
                    raw_val = step.get("value", "")
                    val = self._interpolate_value(raw_val, context_vars)
                    target = await self._resolve_target_scope(selector)
                    try:
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
                        await self.page.wait_for_timeout(800)
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
                        fallbacks.extend(['input[type="text"]:visible', 'input[type="search"]:visible'])

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
                    is_tab = "tab" in sel_lower or "ui-id-" in sel_lower or "tab" in desc_lower

                    if is_tab:
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
                    else:
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
                                fallback_btns = [
                                    'button:has-text("Search")', 'button[type="submit"]', 'input[type="submit"]',
                                    '[aria-label*="search" i]', 'button.btn-primary', 'a:has-text("Search")'
                                ]
                                clicked_fb = False
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
                    for scope in [target, self.page, *self.page.frames]:
                        try:
                            for s in [selector, *selector.split(",")]:
                                s_clean = s.strip()
                                if not s_clean:
                                    continue
                                loc = scope.locator(s_clean).first
                                if await loc.count() > 0 and await loc.is_visible(timeout=min(1200, timeout_ms)):
                                    try:
                                        await loc.evaluate("el => { el.click(); if (typeof el.onclick === 'function') { el.onclick(); } }")
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
                    if "value" in step:
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
                        await self.page.wait_for_selector(raw_candidates[0] if raw_candidates else selector, state=target_state, timeout=1000)

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
        query_val = context_vars.get("input", {}).get("parcel_id") or context_vars.get("input", {}).get("address") or ""
        owner_val = str(extracted_results.get("owner_name") or "").strip().upper()
        dummy_owners = {"", "NONE", "NULL", "N/A", "UNKNOWN", "OWNER OF RECORD", "OWNER", "AND", "OR"}
        has_genuine_owner = bool(owner_val and owner_val not in dummy_owners)

        if not has_genuine_owner or not extracted_results.get("property_address"):
            try:
                from app.browser.result_selector_ai import detect_and_click_result_link_ai
                res_click = await detect_and_click_result_link_ai(
                    self.page,
                    query_value=query_val,
                    workflow_run_id=str(self.run_id),
                    node_id=str(self.node_id),
                )
                if res_click.get("clicked"):
                    await self.page.wait_for_timeout(2000)
            except Exception as res_err:
                logger.debug("Step runner result selector note: %s", res_err)

            # Direct fallback for table cell links (Knockout goToPin, td.link, etc.)
            for scope in [self.page] + [f for f in self.page.frames if f != self.page.main_frame]:
                try:
                    detail_link = scope.locator(
                        'td.link, td[data-bind*="goTo"], td[data-bind*="goToPin"], '
                        'table tbody tr:first-child td.link, table tbody tr td.pointer, '
                        'a[href*="RecordDetail"], a[href*="FolioID"], a[href*="parceldetail"], #flex1 tr.jqgrow a'
                    ).first
                    if await detail_link.count() > 0 and await detail_link.is_visible(timeout=1500):
                        logger.info("Auto-following property detail link or cell...")
                        try:
                            await detail_link.click(force=True, timeout=3000)
                        except Exception:
                            await detail_link.evaluate("el => el.click()")
                        await self.page.wait_for_timeout(2500)
                        break
                except Exception:
                    continue

        # Universal county property DOM evaluation if primary fields still missing
        if not extracted_results.get("owner_name") or not extracted_results.get("property_address"):
            for scope in [self.page] + [f for f in self.page.frames if f != self.page.main_frame]:
                try:
                    from app.browser.page_search_ai import PROPERTY_SCRAPE_JS
                    dom_data = await scope.evaluate(PROPERTY_SCRAPE_JS)
                    if dom_data and isinstance(dom_data, dict):
                        if not extracted_results.get("owner_name") and dom_data.get("owner name"):
                            extracted_results["owner_name"] = dom_data["owner name"]
                        if not extracted_results.get("property_address") and dom_data.get("site address"):
                            extracted_results["property_address"] = dom_data["site address"]
                        if not extracted_results.get("parcel_number") and dom_data.get("parcel id"):
                            extracted_results["parcel_number"] = dom_data["parcel id"]
                        if not extracted_results.get("assessed_value") and (dom_data.get("assessed value") or dom_data.get("market value")):
                            extracted_results["assessed_value"] = dom_data.get("assessed value") or dom_data.get("market value")
                        if not extracted_results.get("legal_description") and dom_data.get("legal description"):
                            extracted_results["legal_description"] = dom_data["legal description"]
                except Exception:
                    continue

        # Mistral Document AI OCR visual extraction if fields still missing
        if not extracted_results.get("owner_name") or not extracted_results.get("property_address"):
            try:
                from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
                ocr_srv = get_mistral_webpage_ocr()
                if ocr_srv.is_enabled():
                    ocr_res = await ocr_srv.extract_from_page(self.page)
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

        return extracted_results
