"""Evidence capture engine for screenshots and HTML dumps upon failure or security challenge."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EvidenceCollector:
    """Captures and stores visual and DOM evidence for failed or challenged browser executions."""

    @classmethod
    async def capture_evidence(
        cls,
        page: Page,
        run_id: str,
        node_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Captures full-page screenshot and HTML dump to the evidence storage directory.
        """
        timestamp = int(time.time() * 1000)
        iso_now = datetime.now(timezone.utc).isoformat()

        evidence_dir = Path(settings.PLAYWRIGHT_EVIDENCE_DIR) / str(run_id)
        evidence_dir.mkdir(parents=True, exist_ok=True)

        screenshot_filename = f"{node_id}_{timestamp}.png"
        screenshot_path = evidence_dir / screenshot_filename

        html_filename = f"{node_id}_{timestamp}.html"
        html_path = evidence_dir / html_filename

        page_url = None
        page_title = None

        try:
            page_url = page.url
            page_title = await page.title()
        except Exception:
            pass

        # 1. Capture Screenshot
        screenshot_captured = False
        try:
            if not page.is_closed():
                await page.screenshot(path=str(screenshot_path), full_page=True, timeout=5000)
                screenshot_captured = True
        except Exception as e:
            logger.warning("Failed to capture screenshot for %s: %s", node_id, e)

        # 2. Capture HTML Dump
        html_captured = False
        try:
            if not page.is_closed():
                content = await page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(content)
                html_captured = True
        except Exception as e:
            logger.warning("Failed to capture HTML content for %s: %s", node_id, e)

        evidence_info = {
            "captured_at": iso_now,
            "page_url": page_url,
            "page_title": page_title,
            "reason": reason,
            "screenshot_path": str(screenshot_path) if screenshot_captured else None,
            "html_path": str(html_path) if html_captured else None,
        }

        logger.info(
            "Evidence captured for run %s node %s: screenshot=%s, html=%s",
            run_id,
            node_id,
            screenshot_captured,
            html_captured,
        )
        return evidence_info

