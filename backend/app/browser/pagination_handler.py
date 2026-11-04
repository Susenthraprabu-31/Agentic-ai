"""Pagination handler supporting next-page buttons, page numbering, and infinite scroll."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

from app.browser.evidence_collector import EvidenceCollector
from app.browser.result_extractor import ResultExtractor
from app.browser.security_detector import SecurityDetector
from app.browser.step_runner import SecurityChallengeException
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PaginationStats:
    """Statistics tracked during pagination."""
    pages_visited: int = 1
    records_extracted: int = 0
    duplicate_records: int = 0
    pagination_errors: list[str] = field(default_factory=list)


class PaginationHandler:
    """
    Manages multi-page result set traversal.
    Supports next buttons, page numbers, and infinite scrolling.
    Includes loop and duplicate detection to prevent runaway pagination.
    """

    def __init__(self, page: Page, run_id: str, node_id: str) -> None:
        self.page = page
        self.run_id = run_id
        self.node_id = node_id

    def _compute_record_signature(self, record: dict[str, Any], key_field: str = "parcel_number") -> str:
        """Computes a unique signature for deduplication."""
        val = record.get(key_field)
        if val is not None and str(val).strip():
            return f"key:{str(val).strip().lower()}"
        # Fall back to hash of non-metadata fields
        clean_copy = {k: v for k, v in record.items() if not k.startswith("_")}
        dumped = json.dumps(clean_copy, sort_keys=True, default=str)
        return hashlib.md5(dumped.encode("utf-8")).hexdigest()

    async def paginate_and_collect(
        self,
        extractor: ResultExtractor,
        table_config: dict[str, Any],
        field_mappings: dict[str, Any],
        pagination_config: dict[str, Any],
        unique_key_field: str = "parcel_number",
    ) -> tuple[list[dict[str, Any]], PaginationStats]:
        """
        Traverses pages and aggregates structured property records.
        """
        stats = PaginationStats()
        max_pages = int(pagination_config.get("max_pages") or getattr(settings, "PLAYWRIGHT_MAX_PAGES", 5))
        max_records = int(pagination_config.get("max_records") or getattr(settings, "PLAYWRIGHT_MAX_RECORDS", 100))
        next_btn_sel = pagination_config.get("next_button_selector")
        is_infinite_scroll = bool(pagination_config.get("infinite_scroll", False))

        all_records: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()

        # Page 1 extraction
        initial_records = await extractor.extract_table_results(table_config, field_mappings, max_records=max_records)
        for rec in initial_records:
            sig = self._compute_record_signature(rec, unique_key_field)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                all_records.append(rec)
            else:
                stats.duplicate_records += 1

        stats.records_extracted = len(all_records)

        # If pagination is not configured or max_pages == 1, return immediately
        if max_pages <= 1 or (not next_btn_sel and not is_infinite_scroll):
            return all_records, stats

        current_page = 1
        while current_page < max_pages and len(all_records) < max_records:
            logger.info("Advancing pagination to page %d / %d", current_page + 1, max_pages)

            try:
                # 1. Handle Infinite Scroll
                if is_infinite_scroll:
                    scroll_delay = int(pagination_config.get("scroll_delay_ms", 1500))
                    prev_height = await self.page.evaluate("document.body.scrollHeight")
                    await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await self.page.wait_for_timeout(scroll_delay)
                    new_height = await self.page.evaluate("document.body.scrollHeight")
                    if new_height == prev_height:
                        logger.info("Infinite scroll reached end of content.")
                        break

                # 2. Handle Next Button Navigation
                elif next_btn_sel:
                    next_btn = self.page.locator(next_btn_sel).first
                    if not await next_btn.is_visible(timeout=2000):
                        logger.info("Pagination next button '%s' no longer visible.", next_btn_sel)
                        break

                    # Check disabled state
                    is_disabled = await next_btn.is_disabled()
                    btn_classes = (await next_btn.get_attribute("class") or "").lower()
                    aria_disabled = await next_btn.get_attribute("aria-disabled")

                    if is_disabled or "disabled" in btn_classes or aria_disabled == "true":
                        logger.info("Pagination next button is disabled. Traversal complete.")
                        break

                    # Click next page
                    await next_btn.click()
                    wait_delay = int(pagination_config.get("wait_after_click_ms", 1500))
                    await self.page.wait_for_timeout(wait_delay)

                # Continuous security check on new page
                sec_dom = await SecurityDetector.inspect_page(self.page)
                if sec_dom.is_detected:
                    evidence = await EvidenceCollector.capture_evidence(
                        self.page, self.run_id, self.node_id, reason=f"Security challenge during pagination: {sec_dom.reason}"
                    )
                    raise SecurityChallengeException(sec_dom)

                # Extract records from new page
                page_records = await extractor.extract_table_results(
                    table_config, field_mappings, max_records=max_records - len(all_records)
                )

                if not page_records:
                    logger.info("No records found on page %d. Stopping pagination.", current_page + 1)
                    break

                # Duplicate detection across pages
                new_records_count = 0
                for rec in page_records:
                    sig = self._compute_record_signature(rec, unique_key_field)
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        all_records.append(rec)
                        new_records_count += 1
                    else:
                        stats.duplicate_records += 1

                current_page += 1
                stats.pages_visited = current_page
                stats.records_extracted = len(all_records)

                # If an entire page produced only duplicates, break out of loop
                if new_records_count == 0:
                    logger.warning("Pagination loop detected on page %d: 0 new unique records.", current_page)
                    stats.pagination_errors.append("Loop detected: 100% duplicate records on page.")
                    break

            except SecurityChallengeException:
                raise
            except Exception as e:
                logger.warning("Pagination step failed on page %d: %s", current_page + 1, e)
                stats.pagination_errors.append(f"Page {current_page + 1} navigation error: {str(e)}")
                break

        return all_records, stats

