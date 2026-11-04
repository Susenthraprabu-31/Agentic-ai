"""Structured search result extraction for tables, detail pages, and property fields."""

import re
import time
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page

from app.browser.url_validator import URLValidator
from app.core.logging import get_logger

logger = get_logger(__name__)

STANDARD_PROPERTY_FIELDS = {
    "parcel_number",
    "owner_name",
    "property_address",
    "legal_description",
    "property_type",
    "assessed_value",
    "market_value",
    "land_value",
    "improvement_value",
    "tax_year",
    "amount_due",
    "amount_paid",
    "status",
}


def clean_currency_or_number(val: Any) -> float | int | str | None:
    """Helper to convert currency or numeric strings into numbers without hallucinating."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Strip currency symbol and commas
    cleaned = re.sub(r"[$,]", "", s).strip()
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return s


class ResultExtractor:
    """
    Extracts structured property search results from web pages.
    Handles single-page fields, result tables, and detail link navigation.
    Never invents missing fields.
    """

    def __init__(self, page: Page, run_id: str, node_id: str) -> None:
        self.page = page
        self.run_id = run_id
        self.node_id = node_id

    async def extract_fields_from_dom(
        self,
        schema: dict[str, Any],
        container_locator=None,
    ) -> dict[str, Any]:
        """
        Extracts mapped fields from the current page DOM or within a container locator.
        Traverses frames and supports standard government property card detail structures.
        """
        context = container_locator or self.page
        results: dict[str, Any] = {}

        scopes = [context]
        if hasattr(self.page, "frames"):
            scopes.extend([f for f in self.page.frames if f != self.page.main_frame])

        for field_name, rule in schema.items():
            if isinstance(rule, dict):
                sel = rule.get("selector") or rule.get("locator", {}).get("value")
                attr = str(rule.get("attribute", "text")).lower()
                is_numeric = rule.get("is_numeric", False)
            else:
                sel = str(rule)
                attr = "text"
                is_numeric = False

            val = None

            # 1. Try provided selector(s)
            candidates = [s.strip() for s in sel.split(",") if s.strip()] if sel else []

            # 2. Add fallback label-adjacent selectors for government detail pages
            if field_name in ("parcel_number", "parcel_id"):
                candidates.extend([
                    "td:has-text('Parcel ID') + td",
                    "th:has-text('Parcel ID') + td",
                    ":has-text('Parcel ID:') + span",
                    "#ParcelID",
                ])
            elif field_name == "owner_name":
                candidates.extend([
                    "td:has-text('Name / Address') + td",
                    "td:has-text('Owner Name') + td",
                    "th:has-text('Owner Name') + td",
                    "td:has-text('Owner:') + td",
                ])
            elif field_name in ("property_address", "site_address", "address"):
                candidates.extend([
                    "td:has-text('Site Location') + td",
                    "td:has-text('Physical Address') + td",
                    "th:has-text('Property Address') + td",
                    "td:has-text('Address:') + td",
                ])
            elif field_name in ("assessed_value", "just_value", "market_value"):
                candidates.extend([
                    "td:has-text('Assessed Value') + td",
                    "td:has-text('Just Value') + td",
                    "td:has-text('Total Assessed') + td",
                ])

            for scope in scopes:
                for cand in candidates:
                    try:
                        el = scope.locator(cand).first
                        if await el.count() > 0:
                            if attr == "value":
                                raw = await el.input_value(timeout=1000)
                            elif attr == "text":
                                raw = await el.inner_text(timeout=1000)
                            else:
                                raw = await el.get_attribute(attr, timeout=1000)

                            if raw and raw.strip():
                                # Clean up owner text if it has line breaks (e.g. name + address)
                                if field_name == "owner_name" and "\n" in raw:
                                    raw = raw.split("\n")[0].strip()
                                val = raw.strip()
                                break
                    except Exception:
                        continue
                if val:
                    break

            if val is not None:
                if is_numeric:
                    val = clean_currency_or_number(val)
                results[field_name] = val if val != "" else None
            else:
                results[field_name] = None

        return results

    async def extract_table_results(
        self,
        table_config: dict[str, Any],
        field_mappings: dict[str, Any],
        max_records: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Extracts structured records from a table or list of rows.
        Supports column index mappings or per-row selector queries.
        """
        table_sel = table_config.get("table_selector") or "table"
        row_sel = table_config.get("row_selector") or "tbody tr"
        detail_link_sel = table_config.get("detail_link_selector")

        records: list[dict[str, Any]] = []

        scope = self.page
        found_table = False
        try:
            table_el = self.page.locator(table_sel).first
            if await table_el.is_visible(timeout=2000):
                found_table = True
        except Exception:
            pass

        if not found_table:
            for f in self.page.frames:
                if f == self.page.main_frame:
                    continue
                try:
                    f_table = f.locator(table_sel).first
                    if await f_table.is_visible(timeout=1000):
                        scope = f
                        found_table = True
                        break
                except Exception:
                    continue

        if not found_table:
            logger.info("Result table '%s' not visible or empty.", table_sel)
            return records

        rows = scope.locator(f"{table_sel} {row_sel}")
        count = await rows.count()
        logger.info("Found %d rows in result table '%s'", count, table_sel)

        for i in range(min(count, max_records)):
            row = rows.nth(i)
            row_data: dict[str, Any] = {}

            # 1. Extract columns by index or sub-selector
            for field_name, map_rule in field_mappings.items():
                if isinstance(map_rule, dict):
                    col_idx = map_rule.get("column_index")
                    sub_sel = map_rule.get("selector")
                    is_numeric = map_rule.get("is_numeric", False)
                elif isinstance(map_rule, int):
                    col_idx = map_rule
                    sub_sel = None
                    is_numeric = False
                else:
                    col_idx = None
                    sub_sel = str(map_rule)
                    is_numeric = False

                val = None
                try:
                    if col_idx is not None:
                        cell = row.locator("td").nth(col_idx)
                        if await cell.is_visible(timeout=1000):
                            val = await cell.inner_text()
                    elif sub_sel:
                        cell = row.locator(sub_sel).first
                        if await cell.is_visible(timeout=1000):
                            val = await cell.inner_text()

                    if val is not None:
                        val = val.strip()
                        if is_numeric:
                            val = clean_currency_or_number(val)
                except Exception:
                    val = None

                row_data[field_name] = val

            # 2. Extract detail link if configured
            detail_url = None
            if detail_link_sel:
                try:
                    link_el = row.locator(detail_link_sel).first
                    if await link_el.is_visible(timeout=1000):
                        href = await link_el.get_attribute("href")
                        if href:
                            detail_url = href.strip()
                except Exception:
                    pass

            # Metadata preservation
            row_data["_metadata"] = {
                "row_index": i,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "detail_url": detail_url,
                "source_page_url": self.page.url,
            }

            records.append(row_data)

        return records

    async def follow_detail_page_and_extract(
        self,
        detail_url: str,
        detail_schema: dict[str, Any],
        parent_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Navigates to a detail page, extracts full properties, and merges with parent record.
        """
        if not detail_url:
            return parent_record or {}

        # Resolve relative URLs
        full_url = detail_url
        if not detail_url.startswith("http://") and not detail_url.startswith("https://") and not detail_url.startswith("data:"):
            from urllib.parse import urljoin
            full_url = urljoin(self.page.url, detail_url)

        URLValidator.validate_url(full_url)

        logger.info("Following property detail page link: %s", full_url)
        await self.page.goto(full_url, wait_until="domcontentloaded", timeout=15000)

        detail_data = await self.extract_fields_from_dom(detail_schema)

        merged = dict(parent_record or {})
        for k, v in detail_data.items():
            if v is not None or k not in merged:
                merged[k] = v

        if "_metadata" not in merged:
            merged["_metadata"] = {}
        merged["_metadata"]["detail_url"] = full_url
        merged["_metadata"]["detail_extracted_at"] = datetime.now(timezone.utc).isoformat()

        return merged

