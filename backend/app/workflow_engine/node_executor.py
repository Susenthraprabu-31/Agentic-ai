"""Pluggable Node Executor interface and adapters (Phase 3A Playwright Automation & Mock Fallback)."""

import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select

from app.browser.browser_manager import get_browser_context
from app.browser.page_search_ai import PageSearchAI
from app.services.portal_search_orchestrator import (
    PortalSearchOrchestrator,
    ExecutionMode,
    assess_recipe_compatibility,
    decide_execution_mode,
    validate_property_matches_query,
)
from app.browser.pagination_handler import PaginationHandler
from app.browser.recipe_loader import RecipeLoader, RecipeLoadException
from app.browser.step_runner import (
    PlaywrightStepRunner,
    SecurityChallengeException,
    StepExecutionException,
)
from app.browser.url_validator import SSRFProtectionException
from app.core.config import settings
from app.core.logging import get_logger
from app.documents.document_discovery import DocumentDiscovery
from app.documents.document_downloader import DocumentDownloader
from app.documents.document_service import DocumentService
from app.models.county_source import CountySource
from app.workflow_engine.execution_context import ExecutionContext, NodeExecutionResult

logger = get_logger(__name__)


def _query_condition(query_type: str) -> str:
    mapping = {
        "parcel": "input.parcel_id",
        "address": "input.address",
        "owner": "input.owner_name",
    }
    return mapping.get(query_type, "input.parcel_id")


def _step_applies_to_query(step: dict[str, Any], query_type: str) -> bool:
    cond = step.get("condition")
    if not cond:
        return True
    needed = _query_condition(query_type)
    return needed in str(cond)


async def _recipe_compatible_with_query(
    page,
    steps: list[dict[str, Any]],
    query_type: str,
    resolved: Any | None = None,
    portal_url: str = "",
    county_source_id: str | None = None,
) -> bool:
    """Backward-compatible wrapper; prefer assess_recipe_compatibility on ResolvedRecipe."""
    if resolved and getattr(resolved, "steps", None):
        result = await assess_recipe_compatibility(
            page,
            resolved,
            portal_url=portal_url,
            county_source_id=county_source_id,
            query_type=query_type,
        )
        return result.compatible
    if not steps:
        return False
    generic_selectors = ('input[type="text"]', "input[type='text']", "input[type=\"search\"]")
    for step in steps:
        if step.get("action") != "fill" or not _step_applies_to_query(step, query_type):
            continue
        selector = step.get("selector") or step.get("locator", {}).get("value")
        if not selector or str(selector).startswith("{{"):
            continue
        sel_lower = str(selector).lower()
        if query_type == "address" and any(g in sel_lower for g in generic_selectors):
            continue
        if query_type == "address" and ("altkey" in sel_lower or "alt_key" in sel_lower):
            continue
        try:
            if await page.locator(selector).count() > 0:
                return True
            for frame in page.frames:
                if await frame.locator(selector).count() > 0:
                    return True
        except Exception:
            continue
    return False


class BaseNodeExecutor(ABC):
    """Abstract base class for all node execution adapters."""

    @abstractmethod
    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        """Execute node logic and return NodeExecutionResult."""
        pass


class InputNodeExecutor(BaseNodeExecutor):
    """
    Input node executor.
    Validates required search criteria (parcel_id, address, owner_name)
    and packages them for downstream nodes.
    """

    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        start_time = time.time()
        node_data = node.get("data", {})
        required_params = node_data.get("required_params", ["parcel_id"])

        provided_input = dict(context.initial_input)
        raw_parcel = str(provided_input.get("parcel_id", "")).strip()
        raw_address = str(provided_input.get("address", "")).strip()
        raw_owner = str(provided_input.get("owner_name", "")).strip()

        has_any_criteria = bool(raw_parcel or raw_address or raw_owner)

        if not has_any_criteria:
            duration = int((time.time() - start_time) * 1000)
            hinted = ", ".join(required_params) if required_params else "parcel_id, address, owner_name"
            return NodeExecutionResult(
                status="FAILED",
                error_message=(
                    "Provide at least one search criterion "
                    f"(parcel_id, address, or owner_name). Expected one of: {hinted}"
                ),
                duration_ms=duration,
            )

        output = {
            "search_params": provided_input,
            "parcel_id": raw_parcel,
            "address": raw_address,
            "owner_name": raw_owner,
        }

        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="COMPLETED",
            output_data=output,
            duration_ms=duration,
        )


def _is_genuine_property_identity(data: dict[str, Any] | None, current_url: str = "") -> bool:
    return PortalSearchOrchestrator.is_genuine_property_identity(data, current_url)


async def _execute_browser_search(
    node_data: dict[str, Any],
    node_id: str,
    record_type: str,
    context: ExecutionContext,
    upstream: dict[str, Any],
    parcel_id: str,
    start_time: float,
) -> NodeExecutionResult:
    """Executes a real recipe-driven search using Playwright, with AI page analysis and auto-save fallback."""
    raw_parcel = str(upstream.get("parcel_id") or context.initial_input.get("parcel_id", "") or parcel_id).strip()
    raw_address = str(upstream.get("address") or context.initial_input.get("address", "")).strip()
    raw_owner = str(upstream.get("owner_name") or context.initial_input.get("owner_name", "")).strip()

    # Determine primary query type and query value
    if raw_parcel and raw_parcel != "01-002-0034":
        active_query_type = "parcel"
        active_query_val = raw_parcel
        clean_parcel = raw_parcel
    elif raw_address:
        active_query_type = "address"
        active_query_val = raw_address
        clean_parcel = ""
    elif raw_owner:
        active_query_type = "owner"
        active_query_val = raw_owner
        clean_parcel = ""
    else:
        active_query_type = "parcel"
        active_query_val = raw_parcel or "01-002-0034"
        clean_parcel = active_query_val

    # 1. Attempt to resolve county source & website URL
    target_county_id = (
        context.initial_input.get("county_source_id")
        or context.county_source_id
        or node_data.get("county_source_id")
    )
    portal_url: str | None = None
    county_name: str = "County"

    if context.db_session:
        try:
            if target_county_id:
                cs_uuid = uuid.UUID(str(target_county_id))
                cs_res = await context.db_session.execute(select(CountySource).where(CountySource.id == cs_uuid))
                cs = cs_res.scalar_one_or_none()
                if cs:
                    county_name = cs.name or cs.county
                    portal_url = cs.assessor_url if record_type == "assessor" else (cs.tax_url or cs.assessor_url)
            if not portal_url:
                query_county = (
                    upstream.get("county")
                    or context.initial_input.get("county")
                    or node_data.get("county")
                    or ""
                ).strip().lower()

                cs_all = (await context.db_session.execute(select(CountySource))).scalars().all()
                if query_county:
                    for c in cs_all:
                        if query_county in (c.name or "").lower() or query_county in (c.county or "").lower():
                            portal_url = c.assessor_url if record_type == "assessor" else (c.tax_url or c.assessor_url)
                            county_name = c.name or c.county
                            target_county_id = str(c.id)
                            break
                if not portal_url and cs_all:
                    portal_url = cs_all[0].assessor_url if record_type == "assessor" else (cs_all[0].tax_url or cs_all[0].assessor_url)
                    county_name = cs_all[0].name
                    target_county_id = str(cs_all[0].id)
        except Exception as cs_err:
            logger.debug("CountySource lookup note: %s", cs_err)

    if not portal_url:
        portal_url = node_data.get("website_url") or node_data.get("search_page_url") or None

    # 2. Attempt loading published recipe version
    resolved = None
    try:
        loader = RecipeLoader(context.db_session)
        resolved = await loader.load_recipe_for_execution(
            node_data,
            record_type=record_type,
            county_source_id=target_county_id,
        )
    except Exception as load_exc:
        logger.info("Pre-existing recipe not found for node %s (%s). Will use AI page search: %s", node_id, record_type, load_exc)

    try:
        async with get_browser_context(workflow_run_id=str(context.run_id)) as (_, page):
            extracted: dict[str, Any] = {}
            executed_standard_steps = False

            # Ensure page navigates to target portal first
            recipe_nav_url = None
            if resolved and resolved.steps:
                first_step = resolved.steps[0]
                if first_step.get("action") == "navigate" and first_step.get("url"):
                    recipe_nav_url = first_step.get("url")

            from app.browser.navigation import navigate_portal_step_by_step

            target_url = recipe_nav_url or (resolved.search_page_url if resolved else None) or (resolved.website_url if resolved else None) or portal_url
            curr_url = page.url or ""
            # Only navigate here if page is uninitialized or not already loaded at the target portal
            if not curr_url or curr_url.startswith("about:"):
                if target_url:
                    logger.info("Navigating to portal for node %s: %s", node_id, target_url)
                    await navigate_portal_step_by_step(
                        page=page,
                        url=target_url,
                        workflow_run_id=str(context.run_id),
                        node_id=str(node_id),
                        wait_for_mount=True,
                    )

            modal_res: dict[str, Any] = {}
            # Dynamically detect and dismiss disclaimer / modal using AI before step execution
            if not recipe_nav_url or not str(recipe_nav_url).startswith("data:"):
                try:
                    from app.browser.modal_detector import detect_and_dismiss_modal_ai
                    modal_res = await detect_and_dismiss_modal_ai(page, workflow_run_id=str(context.run_id), node_id=str(node_id))
                    if modal_res.get("has_disclaimer"):
                        logger.info("AI Modal Detector dismissed disclaimer before step execution: %s", modal_res.get("description"))
                except Exception as d_err:
                    logger.debug("Modal detector pre-check note: %s", d_err)

            logger.info(
                "recipe_lookup county_source=%s recipe_version=%s recipe_name=%s steps=%d",
                target_county_id,
                getattr(resolved, "recipe_version_id", None) if resolved else None,
                getattr(resolved, "recipe_name", None) if resolved else None,
                len(resolved.steps) if resolved and resolved.steps else 0,
            )

            compat = await assess_recipe_compatibility(
                page,
                resolved,
                portal_url=portal_url or target_url or page.url,
                county_source_id=str(target_county_id) if target_county_id else None,
                query_type=active_query_type,
                record_type=record_type,
            )
            can_run_recipe = compat.compatible

            recipe_execution_success = False
            if can_run_recipe and resolved and resolved.steps:
                try:
                    runner = PlaywrightStepRunner(page, str(context.run_id), str(node_id))
                    context_vars = {
                        "search_url": target_url,
                        "active_query_type": active_query_type,
                        "input": {
                            "parcel_id": clean_parcel if active_query_type == "parcel" else "",
                            "address": raw_address if active_query_type == "address" else "",
                            "owner_name": raw_owner if active_query_type == "owner" else "",
                        },
                    }
                    extracted = await runner.execute_steps(
                        resolved.steps,
                        context_vars,
                        extract_schema=resolved.extract_schema,
                        table_settings=resolved.table_settings,
                    )
                    extracted["_source_url"] = page.url or ""
                    executed_standard_steps = True
                    recipe_execution_success = True
                    logger.info("playwright_recipe_execution_completed mode=%s", ExecutionMode.SAVED_RECIPE_EXECUTION.value)
                    if record_type == "assessor" and active_query_val:
                        try:
                            from app.browser.property_capture_prep import ensure_property_detail_workspace

                            await ensure_property_detail_workspace(page, active_query_val)
                        except Exception as detail_prep_err:
                            logger.debug("Post-recipe property detail prep: %s", detail_prep_err)
                except (SecurityChallengeException, SSRFProtectionException, RecipeLoadException):
                    raise
                except Exception as step_exc:
                    logger.warning("Step runner error: %s. Falling back to inline AI Page Search.", step_exc)
                    extracted = {}
                    recipe_execution_success = False

            prop_validation = validate_property_matches_query(
                extracted,
                active_query_type,
                active_query_val,
            )
            execution_mode, mode_reason = decide_execution_mode(
                resolved=resolved,
                compat=compat,
                recipe_executed=executed_standard_steps,
                recipe_execution_success=recipe_execution_success,
                property_validation=prop_validation,
            )
            logger.info(
                "execution_mode_selected mode=%s reason=%s property_identity=%s property_match=%s",
                execution_mode.value,
                mode_reason,
                prop_validation.identity_ok,
                prop_validation.match_ok,
            )

            if execution_mode in (ExecutionMode.FIRST_TIME_AI_DISCOVERY, ExecutionMode.AI_RECOVERY):
                logger.info(
                    "ai_fallback_start mode=%s node=%s query_type=%s query=%s",
                    execution_mode.value,
                    node_id,
                    active_query_type,
                    active_query_val,
                )
                ai_search = PageSearchAI(db=context.db_session)
                if modal_res and modal_res.get("has_disclaimer"):
                    ai_search._last_disclaimer_action = modal_res

                from app.services.portal_search_orchestrator import _normalize_host

                curr_url = page.url or ""
                need_portal_nav = (
                    not curr_url
                    or curr_url.startswith("about:")
                    or (
                        execution_mode == ExecutionMode.AI_RECOVERY
                        and portal_url
                        and _normalize_host(portal_url)
                        and _normalize_host(curr_url) != _normalize_host(portal_url)
                    )
                )
                if need_portal_nav and portal_url:
                    await navigate_portal_step_by_step(
                        page=page,
                        url=portal_url,
                        workflow_run_id=str(context.run_id),
                        node_id=str(node_id),
                        wait_for_mount=True,
                    )

                try:
                    from app.extraction.tax_collector_reports import (
                        ensure_tax_collector_search_page,
                        is_tax_collector_portal,
                    )

                    if record_type == "tax" and (
                        is_tax_collector_portal(page.url or "")
                        or is_tax_collector_portal(portal_url or target_url or "")
                    ):
                        await ensure_tax_collector_search_page(
                            page,
                            query_value=active_query_val,
                            portal_search_url=portal_url or target_url,
                        )
                except Exception as tax_prep_err:
                    logger.debug("Tax portal prep before AI recovery: %s", tax_prep_err)

                ai_res = await ai_search.run_full_ai_search(
                    page=page,
                    query_type=active_query_type,
                    query_value=active_query_val,
                    portal_url=portal_url or (page.url or ""),
                    county_name=county_name,
                    record_type=record_type,
                    workflow_run_id=str(context.run_id),
                    node_id=str(node_id),
                    target_county_id=str(target_county_id) if target_county_id else None,
                )

                ai_rec = ai_res.get("record", {})
                for k, v in ai_rec.items():
                    if v is not None:
                        extracted[k] = v

                plan = ai_res.get("plan") or {}
                if context.db_session and target_county_id and plan.get("runtime_steps"):
                    logger.info(
                        "AI generated %d Playwright step(s); validation=%s",
                        len(plan.get("runtime_steps", [])),
                        (plan.get("validation") or {}).get("ok"),
                    )

            # Handle pagination if configured
            if resolved and resolved.pagination_settings and (
                resolved.pagination_settings.get("next_button_selector")
                or resolved.pagination_settings.get("infinite_scroll")
            ):
                paginator = PaginationHandler(page, str(context.run_id), str(node_id))
                runner = PlaywrightStepRunner(page, str(context.run_id), str(node_id))
                records, stats = await paginator.paginate_and_collect(
                    runner.extractor,
                    resolved.table_settings,
                    resolved.extract_schema,
                    resolved.pagination_settings,
                )
                extracted["_records"] = records
                extracted["_pagination"] = {
                    "pages_visited": stats.pages_visited,
                    "records_extracted": stats.records_extracted,
                    "duplicate_records": stats.duplicate_records,
                    "pagination_errors": stats.pagination_errors,
                }

            # Handle detail page navigation if configured
            if resolved and resolved.detail_page_config.get("navigate_detail") and extracted.get("_records"):
                top_rec = extracted["_records"][0]
                detail_url = top_rec.get("_metadata", {}).get("detail_url")
                if detail_url:
                    runner = PlaywrightStepRunner(page, str(context.run_id), str(node_id))
                    detail_schema = (
                        resolved.detail_page_config.get("detail_field_mappings")
                        or resolved.extract_schema
                    )
                    full_rec = await runner.extractor.follow_detail_page_and_extract(
                        detail_url, detail_schema, parent_record=top_rec
                    )
                    for k, v in full_rec.items():
                        if not k.startswith("_"):
                            extracted[k] = v

            if not extracted.get("parcel_number"):
                extracted["parcel_number"] = clean_parcel or active_query_val

            # Clean numeric assessed value if string
            if "assessed_value" in extracted and isinstance(extracted["assessed_value"], str):
                val_str = extracted["assessed_value"]
                m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", val_str)
                if m:
                    try:
                        clean_num = float(m.group(1).replace(",", ""))
                        extracted["assessed_value"] = clean_num
                    except Exception:
                        pass

            already_has_ocr = bool(
                extracted.get("ocr_markdown")
                or extracted.get("ocr_key_values")
                or (extracted.get("_documents") and len(extracted.get("_documents") or []) > 0)
            )

            # If extracted details are incomplete or not genuine, perform Mistral OCR on rendered page
            skip_assessor_node_ocr = (
                record_type == "assessor"
                and recipe_execution_success
                and bool(extracted.get("parcel_number") or clean_parcel or active_query_val)
            )
            if (
                not already_has_ocr
                and not skip_assessor_node_ocr
                and not _is_genuine_property_identity(extracted, current_url=page.url or "")
            ):
                try:
                    from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
                    ocr_srv = get_mistral_webpage_ocr()
                    if ocr_srv.is_enabled():
                        logger.info("Enriching assessor node with Mistral Document AI OCR...")
                        ocr_data = await ocr_srv.extract_from_page(page, query_value=active_query_val)
                        if ocr_data:
                            for k in [
                                "parcel_number", "owner_name", "property_address", "mailing_address",
                                "legal_description", "property_use", "taxing_district", "land_size",
                                "exemptions", "map_number", "just_value", "assessed_value", "taxable_value",
                                "subdivision", "sales_history", "valuation_history"
                            ]:
                                val = ocr_data.get(k)
                                if val is not None and (
                                    k not in extracted
                                    or not extracted[k]
                                    or extracted[k] == "OWNER OF RECORD"
                                ):
                                    extracted[k] = val
                            if ocr_data.get("raw_markdown") and not extracted.get("ocr_markdown"):
                                extracted["ocr_markdown"] = ocr_data["raw_markdown"]
                            if ocr_data.get("all_key_values") and not extracted.get("ocr_key_values"):
                                extracted["ocr_key_values"] = ocr_data["all_key_values"]
                except Exception as ocr_node_err:
                    logger.warning("Node executor Mistral OCR note: %s", ocr_node_err)

            # Auto-retrieve official Assessor PDF report/card for Florida counties
            if record_type == "assessor" and not context.is_cancelled:
                try:
                    from app.documents.document_service import DocumentService
                    from app.extraction.assessor_reports import (
                        download_universal_assessor_reports,
                        register_downloaded_documents,
                    )
                    from app.browser.property_capture_prep import (
                        is_gis_portal_url,
                        is_plausible_parcel_id,
                        open_gis_search_result_row,
                        wait_for_property_detail_ready,
                    )

                    if is_gis_portal_url(page.url or "") and active_query_val:
                        if not await wait_for_property_detail_ready(
                            page, query_value=active_query_val, timeout_ms=2500
                        ):
                            opened = await open_gis_search_result_row(
                                page,
                                active_query_val,
                                query_type=active_query_type,
                                workflow_run_id=str(context.run_id),
                                node_id=str(node_id),
                            )
                            if opened.get("clicked"):
                                logger.info("Opened GIS parcel before document capture: %s", opened.get("description"))

                    prior_docs = extracted.get("_documents") or []
                    has_assessor_doc = prior_docs and any(
                        (d.get("document_type") or "")
                        in ("Assessor Property Card", "TRIM Notice")
                        for d in prior_docs
                    )
                    generated_docs: list[dict[str, Any]] = []
                    if has_assessor_doc:
                        logger.info(
                            "Skipping duplicate assessor download (%d doc(s) already on record)",
                            len(prior_docs),
                        )
                    else:
                        target_parcel = extracted.get("parcel_number") or clean_parcel or active_query_val
                        if not is_plausible_parcel_id(str(target_parcel), active_query_val or clean_parcel):
                            target_parcel = clean_parcel or active_query_val
                        generated_docs = await download_universal_assessor_reports(
                            page=page,
                            parcel_id=str(target_parcel),
                            county_name=county_name,
                            query_value=active_query_val or clean_parcel or "",
                        )

                    if generated_docs:
                        doc_service = DocumentService(db=context.db_session) if context.db_session else None
                        reg_meta = await register_downloaded_documents(
                            doc_service=doc_service,
                            downloaded_docs=generated_docs,
                            workflow_run_id=context.run_id,
                            source_configuration_id=resolved.source_configuration_id if resolved else None,
                        )
                        existing_docs = extracted.get("_documents") or []
                        extracted["_documents"] = existing_docs + reg_meta
                        logger.info("Registered %d official assessor documents for %s", len(reg_meta), target_parcel)
                except Exception as report_err:
                    logger.warning("Could not auto-download official assessor report: %s", report_err)

            # Auto-retrieve official Tax Collector report, screenshot, and Tax Bill PDF
            if record_type == "tax" and not context.is_cancelled:
                try:
                    from app.documents.document_service import DocumentService
                    from app.extraction.tax_collector_reports import (
                        wait_for_tax_details_loaded,
                        extract_tax_collector_record,
                        capture_and_register_tax_documents,
                    )

                    target_parcel = extracted.get("parcel_number") or clean_parcel or active_query_val
                    logger.info("Waiting dynamically for tax details and bills to fully render...")
                    await wait_for_tax_details_loaded(page, timeout_seconds=15)

                    # Extract structured tax data
                    tax_extracted = await extract_tax_collector_record(page, query_value=target_parcel)
                    for k, v in tax_extracted.items():
                        if v is not None and (k not in extracted or not extracted[k] or extracted[k] == "OWNER OF RECORD"):
                            extracted[k] = v

                    # Capture full-page screenshot & download official Tax Bill PDF
                    doc_service = DocumentService(db=context.db_session) if context.db_session else None
                    reg_meta, tax_screenshot_meta = await capture_and_register_tax_documents(
                        page=page,
                        parcel_id=target_parcel,
                        doc_service=doc_service,
                        workflow_run_id=context.run_id,
                        source_configuration_id=resolved.source_configuration_id if resolved else None,
                        account_history=tax_extracted.get("account_history"),
                    )
                    if tax_screenshot_meta:
                        extracted["tax_screenshot_index"] = tax_screenshot_meta.get("tax_screenshot_index") or []
                        extracted["tax_years_indexed"] = tax_screenshot_meta.get("tax_years_indexed") or []
                        extracted["tax_recent_year_screenshots"] = (
                            tax_screenshot_meta.get("tax_recent_year_screenshots") or []
                        )
                    if reg_meta:
                        existing_docs = extracted.get("_documents") or []
                        extracted["_documents"] = existing_docs + reg_meta
                        logger.info(
                            "Registered %d official tax collector documents for %s (years indexed: %s, recent: %s)",
                            len(reg_meta),
                            target_parcel,
                            extracted.get("tax_years_indexed"),
                            extracted.get("tax_recent_year_screenshots"),
                        )
                except Exception as tax_err:
                    logger.warning("Could not complete tax collector extraction/document capture: %s", tax_err)

            # Handle Document Discovery & Retrieval if enabled on node
            retrieve_docs = node_data.get("retrieve_documents") or bool(node_data.get("document_discovery"))
            if retrieve_docs and not context.is_cancelled:
                doc_rules = node_data.get("document_discovery") or {
                    "strategy": "css",
                    "selector": "a[href*='.pdf'], a.document-link, a.deed-link",
                }
                discovery = DocumentDiscovery(page)
                discovered_docs = await discovery.discover_documents(
                    doc_rules,
                    property_context={
                        "parcel_number": clean_parcel or active_query_val,
                        "property_record_id": None,
                    },
                )
                if discovered_docs:
                    downloader = DocumentDownloader(page, run_id=str(context.run_id), node_id=node_id)
                    doc_service = DocumentService(db=context.db_session)
                    retrieved_docs_meta = []
                    for doc in discovered_docs:
                        if context.is_cancelled:
                            break
                        artifact = await doc_service.process_document_retrieval(
                            discovered=doc,
                            downloader=downloader,
                            workflow_run_id=context.run_id,
                            node_run_id=None,
                            property_record_id=None,
                            source_configuration_id=resolved.source_configuration_id if resolved else None,
                        )
                        retrieved_docs_meta.append({
                            "id": str(artifact.id),
                            "filename": artifact.original_filename,
                            "document_type": artifact.document_type,
                            "status": artifact.status,
                            "file_size": artifact.file_size,
                            "page_count": artifact.page_count,
                            "is_duplicate": artifact.is_duplicate,
                            "error_message": artifact.error_message,
                        })
                    extracted["_documents"] = retrieved_docs_meta

            if record_type == "assessor":
                from app.browser.property_capture_prep import finalize_assessor_extraction

                extracted = finalize_assessor_extraction(
                    extracted,
                    query_value=active_query_val or clean_parcel or "",
                    query_type=active_query_type,
                )

            duration = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                status="COMPLETED",
                output_data={record_type: extracted},
                duration_ms=duration,
            )
    except SecurityChallengeException as sec_exc:
        duration = int((time.time() - start_time) * 1000)
        sec_event = {
            "event_type": sec_exc.result.event_type,
            "url": sec_exc.result.url,
            "details": sec_exc.result.details,
        }
        return NodeExecutionResult(
            status="FAILED",
            error_message=f"Security Challenge Detected: {sec_exc.result.reason}. Stopped for authorized verification.",
            duration_ms=duration,
            evidence={"security_barrier": sec_exc.result.reason},
            security_event=sec_event,
        )
    except SSRFProtectionException as ssrf_exc:
        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="FAILED",
            error_message=f"Security Violation: {str(ssrf_exc)}",
            duration_ms=duration,
            security_event={
                "event_type": "blocked",
                "url": node_data.get("website_url"),
                "details": {"reason": str(ssrf_exc)},
            },
        )
    except RecipeLoadException as load_exc:
        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="FAILED",
            error_message=f"Recipe load error: {str(load_exc)}",
            duration_ms=duration,
        )
    except StepExecutionException as step_exc:
        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="FAILED",
            error_message=step_exc.message,
            duration_ms=duration,
            evidence=step_exc.evidence,
        )
    except Exception as exc:
        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="FAILED",
            error_message=f"Browser automation error: {str(exc)}",
            duration_ms=duration,
        )


class AssessorNodeExecutor(BaseNodeExecutor):
    """
    Assessor Node Executor.
    Executes approved, published Playwright recipe steps when configured,
    or falls back gracefully to the predictable test fixture adapter during early phases.
    """

    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        start_time = time.time()
        node_data = node.get("data", {})
        node_id = str(node.get("id", "assessor"))
        upstream = context.get_upstream_outputs(node_id)

        raw_parcel = str(upstream.get("parcel_id") or context.initial_input.get("parcel_id", "")).strip()
        raw_address = str(upstream.get("address") or context.initial_input.get("address", "")).strip()
        raw_owner = str(upstream.get("owner_name") or context.initial_input.get("owner_name", "")).strip()

        # In live visible browser mode, execute real browser automation (with AI page analysis fallback)
        if settings.PLAYWRIGHT_ENABLED and not context.initial_input.get("force_mock"):
            logger.info("Executing real browser automation with AI analyzer for Assessor node %s", node_id)
            return await _execute_browser_search(
                node_data=node_data,
                node_id=node_id,
                record_type="assessor",
                context=context,
                upstream=upstream,
                parcel_id=raw_parcel,
                start_time=start_time,
            )

        parcel_id = raw_parcel or "01-002-0034"

        # 2. Mock Adapter Fallback for testing
        force_fail = (
            context.initial_input.get("fail_assessor") is True
            or "FAIL_ASSESSOR" in str(parcel_id).upper()
        )

        if force_fail:
            duration = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                status="FAILED",
                error_message="Simulated Assessor website timeout or selector not found (Test Fixture).",
                duration_ms=duration,
            )

        mock_assessor = {
            "_adapter": "MockAssessorAdapter (Phase 2E/3A/3B Test Fixture)",
            "_mock": True,
            "parcel_number": parcel_id,
            "owner_name": upstream.get("owner_name") or "DOE JOHN & JANE",
            "property_address": upstream.get("address") or "123 MAIN ST, ORLANDO, FL 32801",
            "legal_description": "LOT 4 BLK B SUNSHINE ACRES PB 12 PG 34",
            "assessed_value": 285000.00,
            "land_value": 65000.00,
            "improvement_value": 220000.00,
            "tax_year": 2025,
            "use_code": "0100 - SINGLE FAMILY RESIDENTIAL",
            "raw_data": {
                "source": "County Assessor Portal",
                "parcel": parcel_id,
                "verified": True,
            },
        }

        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="COMPLETED",
            output_data={"assessor": mock_assessor},
            duration_ms=duration,
        )


class TaxNodeExecutor(BaseNodeExecutor):
    """
    Tax Node Executor.
    Executes approved, published Playwright recipe steps when configured,
    or falls back gracefully to the predictable test fixture adapter during early phases.
    """

    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        start_time = time.time()
        node_data = node.get("data", {})
        node_id = str(node.get("id", "tax"))
        upstream = context.get_upstream_outputs(node_id)
        raw_parcel = str(upstream.get("parcel_id") or context.initial_input.get("parcel_id", "")).strip()
        raw_address = str(upstream.get("address") or context.initial_input.get("address", "")).strip()
        raw_owner = str(upstream.get("owner_name") or context.initial_input.get("owner_name", "")).strip()
        parcel_id = raw_parcel or "01-002-0034"

        # 1. Real Playwright Recipe Step Execution if recipe or source config is defined
        has_recipe_config = bool(
            node_data.get("recipe_steps")
            or node_data.get("steps")
            or node_data.get("recipe_version_id")
            or node_data.get("source_configuration_id")
            or node_data.get("recipe_id")
            or node_data.get("county_source_id")
            or context.definition.get("county_source_id")
            or getattr(context, "county_source_id", None)
        )

        if settings.PLAYWRIGHT_ENABLED and not context.initial_input.get("force_mock"):
            logger.info("Executing real browser automation with AI analyzer for Tax node %s", node_id)
            return await _execute_browser_search(
                node_data=node_data,
                node_id=node_id,
                record_type="tax",
                context=context,
                upstream=upstream,
                parcel_id=str(raw_parcel),
                start_time=start_time,
            )

        # 2. Mock Adapter Fallback for testing
        force_fail = (
            context.initial_input.get("fail_tax") is True
            or "FAIL_TAX" in str(parcel_id).upper()
        )

        if force_fail:
            duration = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                status="FAILED",
                error_message="Simulated Tax Collector site block or maintenance page (Test Fixture).",
                duration_ms=duration,
            )

        is_delinquent = context.initial_input.get("simulate_delinquent") is True

        mock_tax = {
            "_adapter": "MockTaxAdapter (Phase 2E/3A Test Fixture)",
            "_mock": True,
            "parcel_number": parcel_id,
            "tax_year": 2025,
            "amount_due": 3450.75 if is_delinquent else 0.00,
            "amount_paid": 0.00 if is_delinquent else 3450.75,
            "status": "DELINQUENT" if is_delinquent else "PAID",
            "due_date": "2025-11-30",
            "raw_data": {
                "source": "County Tax Collector",
                "parcel": parcel_id,
                "delinquent": is_delinquent,
            },
        }

        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="COMPLETED",
            output_data={"tax": mock_tax},
            duration_ms=duration,
        )


class NormalizeNodeExecutor(BaseNodeExecutor):
    """
    Normalizes extracted data from Assessor and Tax into a unified schema.
    Supports partial results when one branch fails and records missing sources.
    """

    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        start_time = time.time()
        upstream = context.get_upstream_outputs(node.get("id", ""))

        assessor_data = upstream.get("assessor")
        tax_data = upstream.get("tax")

        missing_sources: list[str] = []
        if context.has_assessor and not assessor_data:
            missing_sources.append("assessor")
        if context.has_tax and not tax_data:
            missing_sources.append("tax")

        # If both required sources were configured and both failed, fail normalize
        if (context.has_assessor or context.has_tax) and len(missing_sources) >= (
            (1 if context.has_assessor else 0) + (1 if context.has_tax else 0)
        ):
            duration = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                status="FAILED",
                error_message=f"All search branches failed ({', '.join(missing_sources)}). Cannot normalize empty data.",
                duration_ms=duration,
            )

        parcel_id = None
        if assessor_data and assessor_data.get("parcel_number"):
            parcel_id = assessor_data["parcel_number"]
        elif tax_data and (tax_data.get("parcel_number") or tax_data.get("account_number")):
            parcel_id = tax_data.get("parcel_number") or tax_data.get("account_number")
        else:
            parcel_id = upstream.get("parcel_id") or context.initial_input.get("parcel_id")

        owner_name = None
        if assessor_data and assessor_data.get("owner_name") and assessor_data["owner_name"] != "OWNER OF RECORD":
            owner_name = assessor_data["owner_name"]
        elif tax_data and tax_data.get("owner_name") and tax_data["owner_name"] != "OWNER OF RECORD":
            owner_name = tax_data["owner_name"]
        else:
            owner_name = upstream.get("owner_name") or context.initial_input.get("owner_name")

        property_address = None
        if assessor_data and assessor_data.get("property_address"):
            property_address = assessor_data["property_address"]
        elif tax_data and (tax_data.get("property_address") or tax_data.get("situs_address")):
            property_address = tax_data.get("property_address") or tax_data.get("situs_address")
        else:
            property_address = upstream.get("address") or context.initial_input.get("address")

        raw_land_size = assessor_data.get("land_size") if assessor_data else None
        if isinstance(raw_land_size, dict):
            val_num = raw_land_size.get("value", "")
            val_unit = raw_land_size.get("unit", raw_land_size.get("units", ""))
            land_size_val = f"{val_num} {val_unit}".strip() if (val_num or val_unit) else str(raw_land_size)
        else:
            land_size_val = raw_land_size

        raw_exemptions = assessor_data.get("exemptions") if assessor_data else None
        if isinstance(raw_exemptions, dict):
            non_zero = [f"{ek}: {ev}" for ek, ev in raw_exemptions.items() if ev and ev != 0 and ev != "0" and ev != "None"]
            exemptions_val = ", ".join(non_zero) if non_zero else ", ".join(str(ek) for ek in raw_exemptions.keys())
        elif isinstance(raw_exemptions, list):
            exemptions_val = ", ".join(str(e) for e in raw_exemptions)
        else:
            exemptions_val = raw_exemptions

        normalized_property = {
            "parcel_id": parcel_id,
            "owner_name": owner_name,
            "property_address": property_address,
            "mailing_address": assessor_data.get("mailing_address") if assessor_data else None,
            "property_use": assessor_data.get("property_use") if assessor_data else None,
            "taxing_district": assessor_data.get("taxing_district") if assessor_data else None,
            "land_size": land_size_val,
            "exemptions": exemptions_val,
            "map_number": assessor_data.get("map_number") if assessor_data else None,
            "legal_description": assessor_data.get("legal_description") if assessor_data else None,
            "total_assessed_value": assessor_data.get("assessed_value") if assessor_data else None,
            "just_value": assessor_data.get("just_value") if assessor_data else None,
            "land_value": assessor_data.get("land_value") if assessor_data else None,
            "improvement_value": assessor_data.get("improvement_value") if assessor_data else None,
            "year_built": assessor_data.get("year_built") if assessor_data else None,
            "living_area": assessor_data.get("living_area") if assessor_data else None,
            "bedrooms": assessor_data.get("bedrooms") if assessor_data else None,
            "bathrooms": assessor_data.get("bathrooms") if assessor_data else None,
            "sales_history": assessor_data.get("sales_history") if assessor_data else [],
            "assessment_history": assessor_data.get("assessment_history") or assessor_data.get("values_history") if assessor_data else [],
            "buildings": assessor_data.get("buildings") if assessor_data else [],
            "land_lines": assessor_data.get("land_lines") if assessor_data else [],
            "extra_features": assessor_data.get("extra_features") if assessor_data else [],
            "tax_status": (tax_data.get("status") or tax_data.get("tax_status")) if tax_data else "UNKNOWN",
            "tax_amount_due": (tax_data.get("amount_due") if tax_data.get("amount_due") is not None else tax_data.get("total_amount_due")) if tax_data else None,
            "is_delinquent": tax_data.get("is_delinquent", (tax_data.get("status") == "DELINQUENT")) if tax_data else False,
            "is_partial": len(missing_sources) > 0,
            "missing_sources": missing_sources,
            "source_errors": dict(context.node_errors),
        }

        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="COMPLETED",
            output_data={"normalized": normalized_property},
            duration_ms=duration,
        )


class OutputNodeExecutor(BaseNodeExecutor):
    """
    Final delivery node.
    Aggregates overall result, branch statuses, partial success indicators, and timing.
    """

    async def execute(self, node: dict[str, Any], context: ExecutionContext) -> NodeExecutionResult:
        start_time = time.time()
        upstream = context.get_upstream_outputs(node.get("id", ""))
        node_data = node.get("data", {})
        fmt = node_data.get("output_format", "json")

        branch_statuses = {}
        for nid, nstatus in context.node_statuses.items():
            branch_statuses[nid] = nstatus

        has_failure = any(st == "FAILED" for st in context.node_statuses.values())
        is_partial = has_failure and context.supports_partial_success

        final_output = {
            "format": fmt,
            "workflow_run_id": str(context.run_id),
            "workflow_id": str(context.workflow_id),
            "version_id": str(context.version_id),
            "branch_statuses": branch_statuses,
            "is_partial_success": is_partial,
            "assessor_data": upstream.get("assessor"),
            "tax_data": upstream.get("tax"),
            "normalized_data": upstream.get("normalized"),
            "errors": list(context.node_errors.values()),
        }

        duration = int((time.time() - start_time) * 1000)
        return NodeExecutionResult(
            status="COMPLETED",
            output_data={"result": final_output},
            duration_ms=duration,
        )


def get_node_executor(node_type: str) -> BaseNodeExecutor:
    """Factory to retrieve executor instance for a node type."""
    t = str(node_type).lower().strip()
    if t == "input":
        return InputNodeExecutor()
    elif t == "assessor":
        return AssessorNodeExecutor()
    elif t == "tax":
        return TaxNodeExecutor()
    elif t == "normalize":
        return NormalizeNodeExecutor()
    elif t == "output":
        return OutputNodeExecutor()
    raise ValueError(f"Unknown or unsupported node type '{node_type}'.")
