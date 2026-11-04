"""
Official Tax Collector Report and Bill PDF retrieval, full-page screenshot capture,
and dynamic tax account data extraction.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Frame, Page
from app.extraction.assessor_reports import register_downloaded_documents

logger = logging.getLogger(__name__)


def _clean_storage_key(identifier: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", (identifier or "record").strip())
    return cleaned or "tax_record"


def get_tax_storage_dir(identifier: str) -> Path:
    key = _clean_storage_key(identifier)
    digits = re.sub(r"\D", "", key) or key
    dest_dir = Path("tmp") / "downloads" / "tax" / digits
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir


def is_tax_collector_portal(url: str, title: str = "", html_sample: str = "") -> bool:
    """Check if the given URL, title, or HTML belongs to a tax collector portal."""
    combined = f"{url} {title} {html_sample}".lower()
    indicators = [
        "county-taxes.net",
        "county-taxes.com",
        "taxsys",
        "floridatax",
        "taxcollector",
        "tax-collector",
        "property-tax",
        "taxbill",
        "govhub",
        "mdctaxcollector",
    ]
    return any(ind in combined for ind in indicators)


async def find_tax_frame(page: Page) -> Frame:
    """Find the frame hosting the tax account summary and bill history."""
    for f in page.frames:
        f_url = (f.url or "").lower()
        if any(k in f_url for k in ["taxsys", "county-taxes", "govhub", "floridatax"]):
            return f
        try:
            body_txt = await f.locator("body").inner_text()
            if any(k in body_txt.lower() for k in ["real estate account", "account summary", "amount due", "notice of ad valorem"]):
                return f
        except Exception:
            continue
    return page.main_frame


async def wait_for_tax_details_loaded(page: Page, timeout_seconds: int = 15) -> Frame:
    """
    Wait dynamically for the tax portal, child frames, and bill data to fully load.
    Prevents closing the browser prematurely before bills and history render.
    """
    target_frame = page.main_frame

    # 1. Wait for navigation / child frame to appear
    for _ in range(timeout_seconds):
        await page.wait_for_timeout(1000)
        for f in page.frames:
            f_url = (f.url or "").lower()
            if any(k in f_url for k in ["taxsys", "county-taxes", "govhub", "floridatax"]):
                target_frame = f
                break
        if target_frame != page.main_frame:
            break

    # 2. Wait for loading indicators / spinners inside target frame to hide
    try:
        await target_frame.wait_for_selector(
            "text=Loading, .loading-spinner, .spinner, .loading",
            state="hidden",
            timeout=8000,
        )
    except Exception:
        pass

    # 3. Wait for the bills and account content to populate in the DOM
    for _ in range(10):
        try:
            body = await target_frame.locator("body").inner_text()
            if len(body) > 400 or any(k in body.lower() for k in ["print (pdf)", "amount due", "annual bill", "receipt #", "paid $"]):
                break
        except Exception:
            pass
        await page.wait_for_timeout(1000)

    return target_frame


async def extract_tax_collector_record(page: Page, query_value: str = "") -> dict[str, Any]:
    """
    Extract structured tax account details, owner, situs, status, amounts, and account history.
    """
    target_frame = await find_tax_frame(page)
    clean_target = query_value.strip()

    body_text = ""
    try:
        body_text = await target_frame.locator("body").inner_text()
    except Exception:
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass

    record: dict[str, Any] = {
        "parcel_number": clean_target,
        "account_number": "",
        "owner_name": "",
        "property_address": "",
        "situs_address": "",
        "tax_year": 2026,
        "tax_status": "Unknown",
        "status": "Unknown",
        "amount_due": 0.0,
        "total_amount_due": 0.0,
        "amount_paid": 0.0,
        "due_date": "",
        "current_bill_year": "",
        "current_bill_amount": 0.0,
        "is_delinquent": False,
        "account_history": [],
    }

    if not body_text:
        return record

    # 1. Account / Parcel Number
    m_acct = re.search(r"Account\s*#?\s*([0-9\-_]+)", body_text, re.I)
    if m_acct:
        record["account_number"] = m_acct.group(1).strip()
        if not record["parcel_number"]:
            record["parcel_number"] = record["account_number"]

    # 2. Owner
    m_owner = re.search(r"Owner:?\s*\n+([^\n]+)", body_text, re.I)
    if m_owner:
        record["owner_name"] = m_owner.group(1).strip()

    # 3. Situs / Address
    m_situs = re.search(r"Situs:?\s*\n+([^\n]+(?:\n+[^\n]+)?)", body_text, re.I)
    if m_situs:
        raw_addr = m_situs.group(1).replace("\n", ", ").strip()
        cleaned_addr = re.sub(r"\s*,\s*", ", ", raw_addr)
        record["property_address"] = cleaned_addr
        record["situs_address"] = cleaned_addr

    # 4. Total Amount Due
    m_total = re.search(r"Total\s*Amount\s*Due:?\s*\n*\$?\s*([\d,]+(?:\.\d{2})?)", body_text, re.I)
    if m_total:
        val = float(m_total.group(1).replace(",", ""))
        record["total_amount_due"] = val
        record["amount_due"] = val
        if val > 0:
            record["tax_status"] = "Unpaid"
            record["status"] = "Unpaid"
        else:
            record["tax_status"] = "PAID"
            record["status"] = "PAID"

    # 5. Account History parsing
    history_matches = re.finditer(
        r"(\d{4}\s+Annual\s+Bill)\s*\n+\s*\$?([\d,]+(?:\.\d{2})?)\s+(Paid(?:\s+\$[\d,]+(?:\.\d{2})?)?|Unpaid|Delinquent)([^\n]*)",
        body_text,
        re.I,
    )
    history = []
    for hm in history_matches:
        bill_title = hm.group(1).strip()
        due_amt = hm.group(2).strip()
        status_str = hm.group(3).strip()
        extra = hm.group(4).strip()

        m_date = re.search(r"(\d{2}/\d{2}/\d{4})", extra)
        p_date = m_date.group(1) if m_date else ""
        m_rec = re.search(r"Receipt\s*#?([A-Za-z0-9\-_]+)", extra)
        p_receipt = m_rec.group(1) if m_rec else ""

        history.append({
            "bill": bill_title,
            "year": bill_title.split()[0],
            "amount_due": f"${due_amt}",
            "status": status_str,
            "payment_date": p_date,
            "receipt_number": p_receipt,
        })

    record["account_history"] = history
    if history:
        top_bill = history[0]
        record["current_bill_year"] = top_bill.get("year", "2026")
        record["tax_year"] = int(top_bill.get("year", "2026")) if top_bill.get("year", "").isdigit() else 2026
        top_status = top_bill.get("status", "")
        if "Unpaid" in top_status:
            record["tax_status"] = "Unpaid"
            record["status"] = "Unpaid"
            # If prior year bills are also unpaid, it is delinquent
            if len(history) > 1 and any("Unpaid" in h.get("status", "") for h in history[1:]):
                record["is_delinquent"] = True
        elif "Paid" in top_status:
            record["tax_status"] = "PAID"
            record["status"] = "PAID"
            # Parse amount paid
            m_paid = re.search(r"\$([\d,]+(?:\.\d{2})?)", top_status)
            if m_paid:
                record["amount_paid"] = float(m_paid.group(1).replace(",", ""))

    return record


async def capture_tax_collector_screenshot(
    page: Page,
    parcel_id: str,
    workflow_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Capture full-page high-resolution screenshot of the tax portal account view."""
    try:
        clean_id = _clean_storage_key(parcel_id)
        dest_dir = get_tax_storage_dir(clean_id)
        screenshot_path = dest_dir / f"tax_collector_account_{clean_id}.png"

        await page.screenshot(path=str(screenshot_path), full_page=True)
        if screenshot_path.exists() and screenshot_path.stat().st_size > 5000:
            logger.info("Captured Tax Collector Screenshot: %s (%d bytes)", screenshot_path, screenshot_path.stat().st_size)
            return {
                "document_type": "Tax Collector Report",
                "document_title": f"Tax Collector Property Account - {clean_id}.png",
                "file_path": str(screenshot_path),
                "source_url": page.url,
                "document_url": page.url,
            }
    except Exception as exc:
        logger.warning("Could not capture tax collector screenshot: %s", exc)
    return None


async def download_official_tax_bill(
    page: Page,
    parcel_id: str,
    workflow_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Download official property tax bill PDF from the Print (PDF) link."""
    try:
        clean_id = _clean_storage_key(parcel_id)
        dest_dir = get_tax_storage_dir(clean_id)
        target_frame = await find_tax_frame(page)

        print_btn = target_frame.locator("a:has-text('Print (PDF)'), a[href*='/print'], a.print-link-with-icon").first
        if await print_btn.count() == 0:
            print_btn = page.locator("a:has-text('Print (PDF)'), a[href*='/print'], a.print-link-with-icon").first

        if await print_btn.count() > 0:
            logger.info("Found official Tax Bill Print (PDF) button, downloading...")
            try:
                async with page.expect_download(timeout=15000) as download_info:
                    await print_btn.evaluate("el => el.click()")
                download = await download_info.value
                filename = download.suggested_filename or f"tax_bill_{clean_id}.pdf"
                pdf_path = dest_dir / filename
                await download.save_as(str(pdf_path))

                if pdf_path.exists() and pdf_path.stat().st_size > 5000:
                    logger.info("Downloaded official Tax Bill PDF: %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
                    return {
                        "document_type": "Property Tax Bill",
                        "document_title": filename,
                        "file_path": str(pdf_path),
                        "source_url": page.url,
                        "document_url": f"{page.url}#/bill/print",
                    }
            except Exception as dl_err:
                logger.warning("expect_download error for tax bill: %s", dl_err)
    except Exception as exc:
        logger.warning("Could not download official tax bill PDF: %s", exc)
    return None


async def capture_and_register_tax_documents(
    page: Page,
    parcel_id: str,
    doc_service: Any,
    workflow_run_id: Optional[uuid.UUID] = None,
    source_configuration_id: Optional[uuid.UUID] = None,
) -> list[dict[str, Any]]:
    """Capture tax screenshot and tax bill PDF, and register them into persistent DocumentArtifacts."""
    docs_to_register = []

    # 1. Capture full-page screenshot
    shot_doc = await capture_tax_collector_screenshot(page, parcel_id)
    if shot_doc:
        docs_to_register.append(shot_doc)

    # 2. Download official Tax Bill PDF
    bill_doc = await download_official_tax_bill(page, parcel_id)
    if bill_doc:
        docs_to_register.append(bill_doc)

    if not docs_to_register:
        return []

    # Register into DB and persist
    registered_meta = await register_downloaded_documents(
        doc_service=doc_service,
        downloaded_docs=docs_to_register,
        workflow_run_id=workflow_run_id,
        source_configuration_id=source_configuration_id,
    )
    return registered_meta

