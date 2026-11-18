"""
Post-step verification: heuristics + optional AI vision to confirm recipe progress.
Triggers GIS parcel recovery when the portal is on the wrong screen.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from playwright.async_api import Page

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

VERIFY_ACTIONS = frozenset(
    {
        "click",
        "click_if_visible",
        "fill",
        "wait_for_selector",
        "navigate",
    }
)


async def _page_text_blob(page: Page, limit: int = 3500) -> str:
    parts: list[str] = []
    try:
        parts.append((await page.locator("body").inner_text(timeout=3000) or "")[:limit])
    except Exception:
        pass
    for frame in page.frames:
        try:
            parts.append((await frame.locator("body").inner_text(timeout=1500) or "")[:limit])
        except Exception:
            continue
    return " ".join(parts)


def _heuristic_verify(
    page_url: str,
    body_text: str,
    *,
    action: str,
    step_desc: str,
    query_type: str,
    query_value: str,
) -> dict[str, Any]:
    """Fast checks without LLM."""
    from app.browser.property_capture_prep import (
        RESULT_ROW_NOISE,
        is_gis_portal_url,
        panel_has_property_data,
        PORTAL_SEARCH_ERROR,
    )

    low_url = (page_url or "").lower()
    low_body = (body_text or "").lower()
    issues: list[str] = []

    if PORTAL_SEARCH_ERROR.search(body_text):
        issues.append("portal_search_error_visible")

    desc_low = step_desc.lower()
    qv = (query_value or "").strip()
    if action == "click" and any(k in desc_low for k in ("submit", "search", "find")):
        if panel_has_property_data(body_text) or "pagetypeid=4" in low_url:
            return {"ok": True, "issues": [], "recovery": None, "method": "heuristic"}
    on_search_form = "pagetype=search" in low_url or ("/search" in low_url and "schneidercorp" in low_url)
    is_panel_open = any(
        k in desc_low for k in ("section", "tab", "panel", "expand", "open address", "open parcel", "open owner")
    )
    qv_strip = (query_value or "").strip()
    q_digits_body = re.sub(r"\D", "", qv_strip)
    body_digits = re.sub(r"\D", "", body_text or "")
    parcel_on_page = bool(
        q_digits_body and len(q_digits_body) >= 8 and q_digits_body in body_digits
    )
    on_results_grid = "search results" in low_body or "click parcel" in low_body
    if (
        query_value
        and RESULT_ROW_NOISE.search(body_text)
        and query_value not in body_text
        and not parcel_on_page
        and not (on_search_form and (is_panel_open or action in ("wait_for_selector", "fill")))
        and not (on_results_grid and parcel_on_page)
    ):
        issues.append("wrong_result_row_or_banner")
    if (
        action == "wait_for_selector"
        and parcel_on_page
        and on_results_grid
        and "result" in desc_low
    ):
        return {"ok": True, "issues": [], "recovery": None, "method": "heuristic"}

    q_digits = re.sub(r"\D", "", qv)

    if action in ("click", "click_if_visible") and is_gis_portal_url(page_url):
        desc = step_desc.lower()
        is_search = any(k in desc for k in ("search", "submit", "find"))
        is_result_step = any(
            k in desc for k in ("result", "parcel", "appraisal", "detail", "record", "matching")
        )
        if is_search and qv:
            if qv not in body_text and q_digits not in re.sub(r"\D", "", body_text):
                issues.append("search_submit_parcel_not_on_page")
        if is_result_step or "property appraisal" in desc:
            detail_url = "keyvalue=" in low_url or "pagetypeid=4" in low_url
            on_results_grid = "search results" in low_body and "parcel details" not in low_body
            if not detail_url and on_results_grid:
                issues.append("still_on_search_results_grid")
            elif not detail_url and not panel_has_property_data(body_text):
                issues.append("parcel_detail_not_open")

    if action == "fill" and "parcel" in step_desc.lower() and qv:
        if qv not in body_text and q_digits[:8] not in re.sub(r"\D", "", body_text):
            issues.append("fill_parcel_not_reflected")

    ok = len(issues) == 0
    recovery = None
    if "still_on_search_results_grid" in issues or "parcel_detail_not_open" in issues:
        recovery = "open_gis_search_result"
    elif "wrong_result_row_or_banner" in issues and qv:
        recovery = "open_gis_search_result"

    return {
        "ok": ok,
        "issues": issues,
        "recovery": recovery,
        "method": "heuristic",
    }


async def ai_verify_step_outcome(
    page: Page,
    *,
    action: str,
    step_desc: str,
    query_type: str,
    query_value: str,
    heuristic: dict[str, Any],
) -> dict[str, Any]:
    """Optional vision LLM when heuristics report a problem (confirms or overrides)."""
    if heuristic.get("ok"):
        return heuristic

    try:
        from app.core.llm_client import chat_completions_create, llm_configured

        if not llm_configured():
            return heuristic

        png = await page.screenshot(type="png", full_page=False)
        b64 = base64.standard_b64encode(png).decode("ascii")
        body_preview = (await _page_text_blob(page, 2000))[:1800]

        prompt = f"""You verify browser automation for a property assessor portal step.

Step action: {action}
Step description: {step_desc}
Query type: {query_type}
Query value: {query_value}

Heuristic issues flagged: {heuristic.get("issues")}

Page text preview:
{body_preview}

Decide if the automation succeeded for this step.
For GIS portals (desotopa.com/gis): after opening a parcel search result, Parcel Details should show owner/situs with the queried parcel ID — NOT only "Search Results" grid, NOT "Certified Values" banner rows, NOT an error page.
For tax collector submit steps: if the screen shows a list of tax bill links by year (e.g. "R - property-number - 2026") or text "TO VIEW OR PAY A TAX BILL", classify as search_results with step_ok true — search succeeded even if the search form is still visible on the page.

Return ONLY JSON:
{{
  "step_ok": true,
  "screen": "parcel_detail|search_results|search_form|error|other",
  "should_recover": false,
  "recovery": "open_gis_search_result|none",
  "reasoning": "one short sentence"
}}"""

        resp, model = await chat_completions_create(
            create_kwargs={
                "model": getattr(settings, "OPENAI_MODEL", "gpt-4.1") or "gpt-4.1",
                "messages": [
                    {"role": "system", "content": "You verify web automation steps. Respond in JSON only."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    },
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            }
        )
        content = resp.choices[0].message.content or "{}"
        ai = json.loads(content)
        merged = dict(heuristic)
        merged["method"] = f"heuristic+{model}"
        merged["ok"] = bool(ai.get("step_ok"))
        merged["ai_screen"] = ai.get("screen")
        merged["ai_reasoning"] = ai.get("reasoning")
        if ai.get("should_recover") and ai.get("recovery") == "open_gis_search_result":
            merged["recovery"] = "open_gis_search_result"
        if not merged["ok"] and not merged.get("recovery"):
            if ai.get("screen") in ("search_results", "error", "other"):
                merged["recovery"] = "open_gis_search_result"
        return merged
    except Exception as exc:
        logger.debug("AI step verify note: %s", exc)
        return heuristic


async def verify_and_recover_after_step(
    page: Page,
    *,
    action: str,
    step_desc: str,
    step_index: int,
    context_vars: dict[str, Any],
    workflow_run_id: str | None = None,
    node_id: str | None = None,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Run after each recipe step; attempt GIS recovery when verification fails."""
    action_l = (action or "").lower()
    if action_l not in VERIFY_ACTIONS:
        return {"verified": True, "skipped": True}

    query_type = str(context_vars.get("active_query_type") or "parcel")
    query_value = (
        str(context_vars.get("input", {}).get("parcel_id") or "")
        or str(context_vars.get("input", {}).get("address") or "")
        or str(context_vars.get("input", {}).get("owner_name") or "")
    ).strip()

    body = await _page_text_blob(page)
    result = _heuristic_verify(
        page.url or "",
        body,
        action=action_l,
        step_desc=step_desc,
        query_type=query_type,
        query_value=query_value,
    )

    if use_ai and not result.get("ok"):
        result = await ai_verify_step_outcome(
            page,
            action=action_l,
            step_desc=step_desc,
            query_type=query_type,
            query_value=query_value,
            heuristic=result,
        )

    recovered = False
    if not result.get("ok") and result.get("recovery") == "open_gis_search_result" and query_value:
        from app.browser.property_capture_prep import is_gis_portal_url, open_gis_search_result_row

        if is_gis_portal_url(page.url or ""):
            logger.warning(
                "Step %d verify failed (%s) — AI/heuristic recovery: open parcel %s",
                step_index + 1,
                result.get("issues") or result.get("ai_reasoning"),
                query_value,
            )
            open_res = await open_gis_search_result_row(
                page,
                query_value,
                query_type=query_type,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
            recovered = bool(open_res.get("clicked"))
            if recovered:
                body2 = await _page_text_blob(page)
                recheck = _heuristic_verify(
                    page.url or "",
                    body2,
                    action="click_if_visible",
                    step_desc="open parcel detail",
                    query_type=query_type,
                    query_value=query_value,
                )
                result["ok"] = recheck.get("ok", False)
                result["recovered"] = True
                result["recovery_description"] = open_res.get("description")

    if action_l in ("click", "click_if_visible", "fill") or not result.get("ok"):
        logger.info(
            "Step %d verify (%s): ok=%s method=%s issues=%s recovered=%s",
            step_index + 1,
            action_l,
            result.get("ok"),
            result.get("method"),
            result.get("issues"),
            result.get("recovered"),
        )

    try:
        from app.browser.browser_manager import get_browser_manager

        bm = await get_browser_manager()
        status = "VERIFIED" if result.get("ok") else "VERIFY_FAILED"
        if recovered:
            status = "RECOVERED" if result.get("ok") else "RECOVERY_ATTEMPTED"
        await bm.log_session_activity(
            action="verify_step",
            description=(
                f"Step {step_index + 1} verify: "
                f"{result.get('ai_reasoning') or result.get('issues') or 'ok'}"
                + (f" → {result.get('recovery_description')}" if result.get("recovery_description") else "")
            ),
            node_id=node_id,
            step_index=step_index,
            status=status,
            url=page.url,
        )
    except Exception:
        pass

    return result
