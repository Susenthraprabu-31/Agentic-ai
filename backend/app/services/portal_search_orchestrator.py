"""
Centralized first-time AI discovery vs saved-recipe execution decisions,
recipe compatibility validation, and property-to-query validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from app.browser.recipe_loader import ResolvedRecipe
from app.core.logging import get_logger

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    FIRST_TIME_AI_DISCOVERY = "FIRST_TIME_AI_DISCOVERY"
    SAVED_RECIPE_EXECUTION = "SAVED_RECIPE_EXECUTION"
    AI_RECOVERY = "AI_RECOVERY"


@dataclass
class RecipeCompatibilityResult:
    compatible: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class PropertyValidationResult:
    valid: bool
    identity_ok: bool
    match_ok: bool
    reasons: list[str] = field(default_factory=list)


def _normalize_host(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return (parsed.hostname or "").lower().strip()
    except Exception:
        return ""


def classify_portal_type(url: str) -> str:
    low = (url or "").lower()
    host = _normalize_host(url)
    if "schneidercorp.com" in host or "qpublic.net" in low:
        return "qpublic_schneider"
    if "/gis" in low or host.endswith("pa.com") and "/gis" in low:
        return "gis_map"
    if "commonsearch" in low:
        return "commonsearch"
    return "custom"


def _recipe_navigate_hosts(steps: list[dict[str, Any]]) -> set[str]:
    hosts: set[str] = set()
    for step in steps or []:
        if str(step.get("action", "")).lower() != "navigate":
            continue
        host = _normalize_host(str(step.get("url") or ""))
        if host:
            hosts.add(host)
    return hosts


def _recipe_portal_type_from_definition(definition: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    meta = definition.get("metadata") or {}
    pt = meta.get("portal_type")
    if pt:
        return str(pt)
    nav_url = meta.get("portal_url") or ""
    if not nav_url and steps:
        for step in steps:
            if step.get("action") == "navigate" and step.get("url"):
                nav_url = step["url"]
                break
    return classify_portal_type(nav_url)


def _step_applies_to_query(step: dict[str, Any], query_type: str) -> bool:
    cond = step.get("condition")
    if not cond:
        return True
    needed = {
        "parcel": "input.parcel_id",
        "address": "input.address",
        "owner": "input.owner_name",
    }.get(query_type, "input.parcel_id")
    return needed in str(cond)


async def assess_recipe_compatibility(
    page,
    resolved: ResolvedRecipe | None,
    *,
    portal_url: str,
    county_source_id: str | None,
    query_type: str,
    record_type: str = "assessor",
) -> RecipeCompatibilityResult:
    """Validate that a stored recipe matches county, host, portal type, and live page structure."""
    reasons: list[str] = []

    if not resolved or not resolved.steps:
        return RecipeCompatibilityResult(False, ["no_recipe_steps"])

    if not getattr(resolved, "is_published", True):
        reasons.append("recipe_not_published")

    recipe_cs = getattr(resolved, "county_source_id", None)
    if county_source_id and recipe_cs and str(recipe_cs) != str(county_source_id):
        reasons.append("county_source_mismatch")

    target_host = _normalize_host(portal_url or "")
    live_host = _normalize_host(page.url or "")
    active_host = live_host or target_host

    recipe_hosts = set()
    if getattr(resolved, "portal_hostname", None):
        recipe_hosts.add(str(resolved.portal_hostname).lower())
    if resolved.website_url:
        recipe_hosts.add(_normalize_host(resolved.website_url))
    if resolved.search_page_url:
        recipe_hosts.add(_normalize_host(resolved.search_page_url))
    recipe_hosts.update(_recipe_navigate_hosts(resolved.steps))

    recipe_hosts.discard("")
    if recipe_hosts and active_host and active_host not in recipe_hosts:
        reasons.append(f"hostname_mismatch:{active_host}!={','.join(sorted(recipe_hosts))}")

    definition = getattr(resolved, "definition", None) or {}
    recipe_portal_type = _recipe_portal_type_from_definition(definition, resolved.steps)
    live_portal_type = classify_portal_type(portal_url or page.url or "")
    if recipe_portal_type != live_portal_type and recipe_portal_type != "custom" and live_portal_type != "custom":
        reasons.append(f"portal_type_mismatch:{live_portal_type}!={recipe_portal_type}")

    generic_selectors = ('input[type="text"]', "input[type='text']", "input[type=\"search\"]")
    selector_found = False
    for step in resolved.steps:
        if step.get("action") != "fill" or not _step_applies_to_query(step, query_type):
            continue
        selector = step.get("selector") or (step.get("locator") or {}).get("value")
        if not selector or str(selector).startswith("{{"):
            continue
        sel_lower = str(selector).lower()
        if query_type == "address" and any(g in sel_lower for g in generic_selectors):
            reasons.append("generic_address_selector")
            continue
        if query_type in ("address", "parcel") and ("altkey" in sel_lower or "alt_key" in sel_lower):
            reasons.append("forbidden_altkey_selector")
            continue
        try:
            if await page.locator(selector).count() > 0:
                selector_found = True
                break
            for frame in page.frames:
                if await frame.locator(selector).count() > 0:
                    selector_found = True
                    break
            if selector_found:
                break
        except Exception:
            continue

    if not selector_found:
        reasons.append("query_fill_selector_not_on_page")

    compatible = len(reasons) == 0
    if compatible:
        logger.info(
            "recipe_compatibility_ok county=%s host=%s portal_type=%s query=%s recipe=%s",
            county_source_id,
            active_host,
            live_portal_type,
            query_type,
            resolved.recipe_name,
        )
    else:
        logger.warning(
            "recipe_compatibility_failed recipe=%s reasons=%s",
            resolved.recipe_name,
            reasons,
        )
    return RecipeCompatibilityResult(compatible=compatible, reasons=reasons)


def is_genuine_property_identity(data: dict[str, Any] | None, current_url: str = "") -> bool:
    """Heuristic: extracted row looks like a real property record, not form chrome."""
    if not data or not isinstance(data, dict):
        return False

    url_lower = (current_url or str(data.get("_source_url") or data.get("source_url") or "")).lower()
    is_search_page = any(
        term in url_lower
        for term in ["/nav/basic", "basic%20search", "#/nav/", "/search.html", "/search.aspx", "/search/basic"]
    ) and not any(term in url_lower for term in ["/parcel/", "/detail/", "account", "propertydetails", "folioid", "recorddetail", "pagetypeid=4", "property-tax"])

    on_gis_hub = "/gis" in url_lower and "pagetypeid=4" not in url_lower and "keyvalue=" not in url_lower

    owner = str(data.get("owner_name") or "").strip().upper()
    parcel = str(data.get("parcel_number") or "").strip().upper()
    address = str(data.get("property_address") or "").strip().upper()

    dummy_owners = {
        "", "NONE", "NULL", "N/A", "UNKNOWN", "OWNER OF RECORD", "OWNER",
        "AND", "OR", "PROPERTY SEARCH", "BASIC SEARCH", "BASIC PROPERTY SEARCH",
    }
    dummy_parcels = {
        "", "NONE", "NULL", "N/A", "UNKNOWN", "01-002-0034",
        "(ONLY AVAILABLE WITH FOLIO OR PARCEL NUMBER SEARCH)",
        "ONLY AVAILABLE WITH FOLIO OR PARCEL NUMBER SEARCH",
    }
    dummy_addresses = {
        "", "NONE", "NULL", "N/A", "UNKNOWN", "OR", "AND", "SITE ADDRESS",
        "STREET ADDRESS", "PROPERTY ADDRESS", "SITE LOCATION", "LOCATION",
    }

    valid_owner = bool(
        owner and owner not in dummy_owners and len(owner) >= 3 and not owner.startswith("ONLY AVAILABLE")
    )
    valid_parcel = bool(
        parcel
        and parcel not in dummy_parcels
        and len(re.sub(r"\D", "", parcel)) >= 3
        and not parcel.startswith("ONLY AVAILABLE")
        and not parcel.startswith("(")
    )
    valid_address = bool(
        address
        and address not in dummy_addresses
        and len(address) >= 5
        and any(char.isdigit() for char in address)
    )

    if on_gis_hub and not any(term in url_lower for term in ("recorddetail", "propertydetails", "keyvalue=")):
        return False

    tax_portal = any(
        term in url_lower
        for term in ("county-taxes", "govhub", "floridatax", "taxsys", "property-tax")
    ) or bool(data.get("account_history")) or data.get("tax_screenshot_index") is not None
    tax_acct = str(data.get("account_number") or data.get("parcel_number") or "").strip()
    if tax_portal and valid_owner and (valid_parcel or valid_address or tax_acct):
        return True
    if tax_portal and tax_acct and len(re.sub(r"\D", "", tax_acct)) >= 6:
        if valid_owner or valid_address or data.get("account_history"):
            return True

    if is_search_page and not (valid_owner and (valid_parcel or valid_address)):
        return False

    return bool(valid_owner or (valid_address and valid_parcel))


def _query_tokens(query_value: str, query_type: str) -> list[str]:
    raw = re.sub(r"\s+", " ", (query_value or "").strip()).upper()
    stop = {"RD", "DR", "ST", "AVE", "BLVD", "LN", "CT", "FL", "THE", "AND", "OR"}
    if query_type == "address":
        parts = [p.rstrip(".") for p in raw.replace(",", " ").split() if p]
        return [p for p in parts if len(p) > 1 and p not in stop]
    if query_type == "parcel":
        digits = re.sub(r"\D", "", raw)
        return [digits[-6:]] if len(digits) >= 6 else ([digits] if digits else [])
    parts = [p for p in raw.split() if len(p) > 2]
    return parts[:6]


def validate_property_matches_query(
    extracted: dict[str, Any] | None,
    query_type: str,
    query_value: str,
    *,
    current_url: str = "",
) -> PropertyValidationResult:
    """Verify extracted property aligns with the user's search input."""
    if not extracted:
        return PropertyValidationResult(False, False, False, ["no_extracted_data"])

    source_url = (current_url or str(extracted.get("_source_url") or extracted.get("source_url") or "")).strip()
    identity_ok = is_genuine_property_identity(extracted, source_url)
    if not identity_ok:
        try:
            from app.extraction.tax_collector_reports import (
                is_tax_collector_portal,
                record_has_tax_collector_identity,
            )

            if record_has_tax_collector_identity(extracted):
                identity_ok = True
            elif is_tax_collector_portal(source_url) or extracted.get("account_history"):
                acct = str(extracted.get("account_number") or extracted.get("parcel_number") or "").strip()
                q_digits = re.sub(r"\D", "", query_value or "")
                a_digits = re.sub(r"\D", "", acct)
                has_tax_payload = bool(
                    extracted.get("owner_name")
                    or extracted.get("property_address")
                    or extracted.get("situs_address")
                    or extracted.get("account_history")
                    or extracted.get("tax_status") not in (None, "", "Unknown")
                )
                if q_digits and a_digits and len(a_digits) >= 6 and has_tax_payload:
                    if q_digits in a_digits or a_digits in q_digits:
                        identity_ok = True
        except Exception:
            pass
    if not identity_ok:
        return PropertyValidationResult(False, False, False, ["identity_not_genuine"])

    tokens = _query_tokens(query_value, query_type)
    reasons: list[str] = []
    match_ok = True

    blob = " ".join(
        str(extracted.get(k) or "")
        for k in ("owner_name", "property_address", "mailing_address", "parcel_number")
    ).upper()

    if query_type == "address":
        hits = sum(1 for t in tokens if t in blob)
        need = min(2, max(1, len(tokens)))
        if hits < need:
            match_ok = False
            reasons.append("address_query_token_mismatch")
    elif query_type == "parcel":
        q_digits = re.sub(r"\D", "", query_value)
        p_digits = re.sub(r"\D", "", str(extracted.get("parcel_number") or ""))
        url = str(extracted.get("_source_url") or extracted.get("source_url") or "").strip()
        low_url = url.lower()
        if "/gis" in low_url and "keyvalue=" not in low_url and q_digits:
            rec_digits = ""
            for rec in extracted.get("_records") or []:
                if isinstance(rec, dict):
                    rec_digits += " ".join(str(v) for v in rec.values())
            combined_digits = re.sub(r"\D", "", blob + rec_digits)
            on_hub_with_query = q_digits in combined_digits
            if not p_digits or (q_digits not in p_digits and p_digits not in q_digits):
                if not on_hub_with_query:
                    match_ok = False
                    reasons.append("gis_hub_without_matching_parcel")
        if q_digits and p_digits and q_digits != p_digits:
            if q_digits not in p_digits and p_digits not in q_digits:
                if len(q_digits) >= 6:
                    match_ok = False
                    reasons.append("parcel_digit_mismatch")
    elif query_type == "owner":
        owner_u = str(extracted.get("owner_name") or "").upper()
        if tokens and not any(t in owner_u for t in tokens[:3]):
            match_ok = False
            reasons.append("owner_token_mismatch")

    valid = identity_ok and match_ok
    if not valid:
        logger.warning(
            "property_validation_failed query_type=%s query=%s reasons=%s",
            query_type,
            query_value[:80],
            reasons,
        )
    return PropertyValidationResult(valid=valid, identity_ok=identity_ok, match_ok=match_ok, reasons=reasons)


def decide_execution_mode(
    *,
    resolved: ResolvedRecipe | None,
    compat: RecipeCompatibilityResult,
    recipe_executed: bool,
    recipe_execution_success: bool,
    property_validation: PropertyValidationResult,
    security_blocked: bool = False,
) -> tuple[ExecutionMode, str]:
    if security_blocked:
        return ExecutionMode.AI_RECOVERY, "security_challenge_blocked"

    if not resolved or not resolved.steps:
        return ExecutionMode.FIRST_TIME_AI_DISCOVERY, "no_saved_recipe"

    if not compat.compatible:
        return ExecutionMode.AI_RECOVERY, f"recipe_incompatible:{','.join(compat.reasons[:3])}"

    if recipe_executed and recipe_execution_success and property_validation.valid:
        return ExecutionMode.SAVED_RECIPE_EXECUTION, "recipe_ok_and_property_validated"

    if recipe_executed and not recipe_execution_success:
        return ExecutionMode.AI_RECOVERY, "recipe_execution_failed"

    if recipe_executed and not property_validation.valid:
        return ExecutionMode.AI_RECOVERY, f"property_validation_failed:{','.join(property_validation.reasons[:3])}"

    return ExecutionMode.AI_RECOVERY, "fallback_to_ai"


def build_recipe_metadata(
    *,
    portal_url: str,
    county_source_id: str | None,
    record_type: str,
    query_types: list[str] | None = None,
) -> dict[str, Any]:
    host = _normalize_host(portal_url)
    return {
        "portal_url": portal_url,
        "portal_hostname": host,
        "portal_type": classify_portal_type(portal_url),
        "county_source_id": str(county_source_id) if county_source_id else None,
        "record_type": record_type,
        "search_types": query_types or ["parcel", "address", "owner"],
        "validation_status": "verified",
    }


class PortalSearchOrchestrator:
    """Facade used by workflow node executor."""

    assess_recipe_compatibility = staticmethod(assess_recipe_compatibility)
    validate_property_matches_query = staticmethod(validate_property_matches_query)
    decide_execution_mode = staticmethod(decide_execution_mode)
    is_genuine_property_identity = staticmethod(is_genuine_property_identity)
    classify_portal_type = staticmethod(classify_portal_type)
    build_recipe_metadata = staticmethod(build_recipe_metadata)
