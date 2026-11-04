"""Document discovery engine scanning pages and tables for document links and download buttons."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Page

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DiscoveredDocument:
    """Represents a potential property document identified during website automation."""
    document_url: str | None
    document_title: str
    document_type: str = "deed"
    source_website: str | None = None
    source_property_record_id: str | None = None
    parcel_number: str | None = None
    recording_number: str | None = None
    document_date: str | None = None
    source_page_url: str | None = None
    discovery_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    button_selector: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentDiscovery:
    """
    Discovers document URLs and download triggers from search results,
    property detail views, and recorder listings.
    """

    def __init__(self, page: Page) -> None:
        self.page = page

    async def discover_documents(
        self,
        discovery_config: dict[str, Any] | list[dict[str, Any]],
        property_context: dict[str, Any] | None = None,
    ) -> list[DiscoveredDocument]:
        """
        Scans current page DOM based on configured discovery rules.
        """
        context = property_context or {}
        parcel_id = context.get("parcel_number") or context.get("parcel_id")
        page_url = self.page.url
        discovered: list[DiscoveredDocument] = []
        seen_targets: set[str] = set()

        # Normalize config to list of rule dicts
        rules: list[dict[str, Any]]
        if isinstance(discovery_config, list):
            rules = discovery_config
        elif isinstance(discovery_config, dict):
            rules = discovery_config.get("rules", [discovery_config])
        else:
            rules = [{"strategy": "css", "selector": "a[href*='.pdf'], a.document-link"}]

        for rule in rules:
            strategy = str(rule.get("strategy", "css")).lower().strip()
            doc_type = str(rule.get("document_type", "deed")).lower()
            url_pattern = rule.get("url_pattern")
            base_selector = rule.get("selector")

            # 1. CSS Selector Strategy
            if strategy == "css" or (base_selector and not strategy.startswith("xpath") and not strategy == "text"):
                sel = base_selector or "a[href*='.pdf']"
                elements = self.page.locator(sel)
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    if not await el.is_visible(timeout=1000):
                        continue

                    href = await el.get_attribute("href")
                    title = (await el.inner_text()).strip() or f"{doc_type.capitalize()} Document"
                    rec_no = await el.get_attribute("data-recording-number")
                    doc_date = await el.get_attribute("data-doc-date")

                    full_url = urljoin(page_url, href) if href else None
                    if full_url and url_pattern:
                        if not re.search(url_pattern, full_url, re.IGNORECASE):
                            continue

                    target_key = full_url or f"btn:{sel}:{i}"
                    if target_key in seen_targets:
                        continue
                    seen_targets.add(target_key)

                    is_button = (await el.get_attribute("type")) == "button" or el.evaluate("e => e.tagName === 'BUTTON'")
                    discovered.append(
                        DiscoveredDocument(
                            document_url=full_url if not href or not href.startswith("javascript:") else None,
                            document_title=title,
                            document_type=doc_type,
                            source_website=page_url,
                            source_property_record_id=str(context.get("property_record_id") or ""),
                            parcel_number=str(parcel_id) if parcel_id else None,
                            recording_number=rec_no,
                            document_date=doc_date,
                            source_page_url=page_url,
                            button_selector=f"{sel} >> nth={i}" if not href or href.startswith("javascript:") else None,
                            metadata={"rule_strategy": "css", "selector": sel},
                        )
                    )

            # 2. XPath Selector Strategy
            elif strategy == "xpath":
                xpath_query = rule.get("xpath") or base_selector or "//a[contains(@href, '.pdf')]"
                elements = self.page.locator(f"xpath={xpath_query}")
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    if not await el.is_visible(timeout=1000):
                        continue

                    href = await el.get_attribute("href")
                    title = (await el.inner_text()).strip() or f"{doc_type.capitalize()} Document"
                    full_url = urljoin(page_url, href) if href else None

                    target_key = full_url or f"xpath:{xpath_query}:{i}"
                    if target_key in seen_targets:
                        continue
                    seen_targets.add(target_key)

                    discovered.append(
                        DiscoveredDocument(
                            document_url=full_url,
                            document_title=title,
                            document_type=doc_type,
                            source_website=page_url,
                            source_property_record_id=str(context.get("property_record_id") or ""),
                            parcel_number=str(parcel_id) if parcel_id else None,
                            source_page_url=page_url,
                            button_selector=f"xpath={xpath_query} >> nth={i}" if not href else None,
                            metadata={"rule_strategy": "xpath"},
                        )
                    )

            # 3. Link Text Strategy
            elif strategy == "text":
                text_match = rule.get("text") or "View Document"
                elements = self.page.locator(f"text={text_match}")
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    if not await el.is_visible(timeout=1000):
                        continue
                    href = await el.get_attribute("href")
                    full_url = urljoin(page_url, href) if href else None

                    target_key = full_url or f"text:{text_match}:{i}"
                    if target_key in seen_targets:
                        continue
                    seen_targets.add(target_key)

                    discovered.append(
                        DiscoveredDocument(
                            document_url=full_url,
                            document_title=text_match,
                            document_type=doc_type,
                            source_website=page_url,
                            source_property_record_id=str(context.get("property_record_id") or ""),
                            parcel_number=str(parcel_id) if parcel_id else None,
                            source_page_url=page_url,
                            button_selector=f"text={text_match} >> nth={i}" if not href else None,
                            metadata={"rule_strategy": "text"},
                        )
                    )

            # 4. Attribute-Based Strategy (e.g. data-doc-url or download)
            elif strategy == "attribute":
                attr_name = rule.get("attribute_name", "download")
                elements = self.page.locator(f"[{attr_name}]")
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    if not await el.is_visible(timeout=1000):
                        continue
                    href = await el.get_attribute("href") or await el.get_attribute(attr_name)
                    title = (await el.inner_text()).strip() or f"{doc_type.capitalize()} Document"
                    full_url = urljoin(page_url, href) if href else None

                    target_key = full_url or f"attr:{attr_name}:{i}"
                    if target_key in seen_targets:
                        continue
                    seen_targets.add(target_key)

                    discovered.append(
                        DiscoveredDocument(
                            document_url=full_url,
                            document_title=title,
                            document_type=doc_type,
                            source_website=page_url,
                            source_property_record_id=str(context.get("property_record_id") or ""),
                            parcel_number=str(parcel_id) if parcel_id else None,
                            source_page_url=page_url,
                            metadata={"rule_strategy": "attribute"},
                        )
                    )

        logger.info("DocumentDiscovery identified %d candidate documents on %s", len(discovered), page_url)
        return discovered

