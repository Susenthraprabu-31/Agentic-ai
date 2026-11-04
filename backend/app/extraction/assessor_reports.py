"""Official assessor property card and appraisal report PDF generation and document registration."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Frame, Page
from app.extraction.florida_extractors import extract_valid_miami_dade_folio

logger = logging.getLogger(__name__)


def _clean_storage_key(identifier: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", (identifier or "record").strip())
    return cleaned or "property_record"


def get_assessor_storage_dir(identifier: str) -> Path:
    key = _clean_storage_key(identifier)
    dest_dir = Path("tmp") / "downloads" / "assessor" / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir


async def download_collier_assessor_card(
    page: Page,
    parcel_id: str,
    target_frame: Frame | None = None,
) -> Optional[dict[str, Any]]:
    """Capture Collier County Assessor property summary card as an official PDF."""
    clean_id = _clean_storage_key(parcel_id)
    dest_dir = get_assessor_storage_dir(clean_id)
    pdf_path = dest_dir / f"collier_assessor_{clean_id}.pdf"

    detail_url = f"https://www.collierappraiser.com/Main_Search/ParcelDetail.html?FolioID={clean_id}"

    if pdf_path.exists() and pdf_path.stat().st_size > 5000:
        return {
            "document_type": "Assessor Property Card",
            "document_title": f"Collier Assessor Property Card - {clean_id}.pdf",
            "file_path": str(pdf_path),
            "source_url": detail_url,
            "document_url": detail_url,
        }

    try:
        browser = page.context.browser
        print_ctx = await browser.new_context() if browser else page.context
        print_page = await print_ctx.new_page()
        try:
            await print_page.goto(detail_url, wait_until="networkidle", timeout=25000)
            await print_page.pdf(
                path=str(pdf_path),
                format="Letter",
                print_background=True,
                margin={"top": "0.25in", "right": "0.25in", "bottom": "0.25in", "left": "0.25in"},
            )
        finally:
            if browser:
                await print_ctx.close()
            else:
                await print_page.close()

        if pdf_path.exists() and pdf_path.stat().st_size > 5000:
            logger.info("Saved Collier Assessor Property Card PDF: %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
            return {
                "document_type": "Assessor Property Card",
                "document_title": f"Collier Assessor Property Card - {clean_id}.pdf",
                "file_path": str(pdf_path),
                "source_url": detail_url,
                "document_url": detail_url,
            }
    except Exception as exc:
        logger.warning("Failed to generate Collier Assessor PDF: %s", exc)

    return None


async def download_miami_dade_assessor_reports(
    page: Page,
    folio: str,
) -> list[dict[str, Any]]:
    """Capture Miami-Dade Assessor Summary and Detailed reports as official PDFs."""
    norm_folio = extract_valid_miami_dade_folio(folio) or folio.strip()
    digits = re.sub(r"\D", "", norm_folio)
    if not digits:
        return []

    dest_dir = get_assessor_storage_dir(digits)
    reports = [
        ("summary", "Assessor Summary Report", f"assessor_summary_{digits}.pdf"),
        ("detailed", "Assessor Detailed Report", f"assessor_detailed_{digits}.pdf"),
    ]

    results: list[dict[str, Any]] = []

    for report_type, doc_type, filename in reports:
        pdf_path = dest_dir / filename
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            results.append({
                "document_type": doc_type,
                "document_title": filename,
                "file_path": str(pdf_path),
                "source_url": page.url,
                "document_url": f"{page.url}#/report/{report_type}",
            })
            continue

        report_page = None
        report_hash = f"#/report/{report_type}"
        try:
            async with page.context.expect_page(timeout=15000) as page_info:
                await page.evaluate(
                    """([folioValue, reportType, reportHash]) => {
                      sessionStorage.setItem('currentFolio', folioValue);
                      sessionStorage.setItem('currentParentFolio', folioValue);
                      sessionStorage.setItem('report_type', reportType);
                      window.open(reportHash, 'Report', 'height=760,width=1020,location=0,resizable=yes,toolbar=yes');
                    }""",
                    [digits, report_type, report_hash],
                )
            report_page = await page_info.value
            await report_page.wait_for_load_state("domcontentloaded")
            await report_page.wait_for_timeout(2500)

            await report_page.emulate_media(media="print")
            await report_page.pdf(
                path=str(pdf_path),
                format="Letter",
                print_background=True,
                margin={"top": "0.25in", "right": "0.25in", "bottom": "0.25in", "left": "0.25in"},
            )

            if pdf_path.exists() and pdf_path.stat().st_size > 1000:
                logger.info("Saved Miami-Dade %s PDF: %s (%d bytes)", doc_type, pdf_path, pdf_path.stat().st_size)
                results.append({
                    "document_type": doc_type,
                    "document_title": filename,
                    "file_path": str(pdf_path),
                    "source_url": page.url,
                    "document_url": f"{page.url}#/report/{report_type}",
                })
        except Exception as exc:
            logger.debug("Miami-Dade report %s generation note: %s", report_type, exc)
        finally:
            if report_page:
                try:
                    await report_page.close()
                except Exception:
                    pass

    return results


async def download_universal_assessor_reports(
    page: Page,
    parcel_id: str,
    county_name: str = "County",
) -> list[dict[str, Any]]:
    """
    Capture official Assessor property card or summary report PDF dynamically for ANY county portal.
    Handles portal-specific print popups if present, or renders a high-fidelity print PDF of the
    active property details page.
    """
    clean_id = _clean_storage_key(parcel_id)
    dest_dir = get_assessor_storage_dir(clean_id)
    url_lower = (page.url or "").lower()

    # 1. Miami-Dade Angular report popups if on Miami portal
    if "miamidade" in url_lower:
        m_docs = await download_miami_dade_assessor_reports(page, parcel_id)
        if m_docs:
            return m_docs

    # 2. Collier detail card if on Collier portal
    if "collier" in url_lower:
        c_doc = await download_collier_assessor_card(page, parcel_id)
        if c_doc:
            return [c_doc]

    # 3. Universal County Assessor Card PDF generation for any county
    pdf_path = dest_dir / f"assessor_card_{clean_id}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return [{
            "document_type": "Assessor Property Card",
            "document_title": f"Assessor Property Card - {clean_id}.pdf",
            "file_path": str(pdf_path),
            "source_url": page.url,
            "document_url": page.url,
        }]

    try:
        await page.emulate_media(media="print")
        await page.pdf(
            path=str(pdf_path),
            format="Letter",
            print_background=True,
            margin={"top": "0.25in", "right": "0.25in", "bottom": "0.25in", "left": "0.25in"},
        )
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            logger.info("Generated universal Assessor Property Card PDF for %s: %s (%d bytes)", parcel_id, pdf_path, pdf_path.stat().st_size)
            return [{
                "document_type": "Assessor Property Card",
                "document_title": f"Assessor Property Card - {clean_id}.pdf",
                "file_path": str(pdf_path),
                "source_url": page.url,
                "document_url": page.url,
            }]
    except Exception as exc:
        logger.warning("Universal Assessor PDF generation note: %s", exc)

    return []


async def register_downloaded_documents(
    doc_service: Any,
    downloaded_docs: list[dict[str, Any]],
    workflow_run_id: uuid.UUID | None = None,
    node_run_id: uuid.UUID | None = None,
    property_record_id: uuid.UUID | None = None,
    source_configuration_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Register generated/downloaded PDFs into PostgreSQL DocumentArtifacts."""
    from app.documents.document_discovery import DiscoveredDocument
    from app.models.document_artifact import DocumentArtifact, DocumentStatus

    registered_meta: list[dict[str, Any]] = []

    for d in downloaded_docs:
        fpath = Path(d.get("file_path", ""))
        if not fpath.exists():
            continue

        file_bytes = fpath.read_bytes()
        file_size = len(file_bytes)
        title = d.get("document_title") or fpath.name
        doc_type = d.get("document_type") or "Assessor Property Card"
        doc_url = d.get("document_url") or d.get("source_url") or "assessor://card"

        # Check existing artifact in DB
        artifact = None
        if doc_service and doc_service.db and workflow_run_id:
            from sqlalchemy import select
            existing_q = select(DocumentArtifact).where(
                DocumentArtifact.workflow_run_id == workflow_run_id,
                DocumentArtifact.original_filename == title,
            )
            existing_res = await doc_service.db.execute(existing_q)
            artifact = existing_res.scalar_one_or_none()

            if not artifact:
                import hashlib
                from app.documents.document_storage import DocumentStorage
                doc_id = uuid.uuid4()
                sha256 = hashlib.sha256(file_bytes).hexdigest()
                stored_name = f"{doc_id}_{fpath.name}"
                ext = fpath.suffix.lstrip(".").lower() or "pdf"
                mime = "image/png" if ext == "png" else ("image/jpeg" if ext in ["jpg", "jpeg"] else "application/pdf")
                persistent_key = DocumentStorage.generate_storage_key(
                    workflow_run_id, property_record_id, doc_id, extension=ext
                )
                if hasattr(doc_service, "storage") and doc_service.storage:
                    try:
                        await doc_service.storage.save(persistent_key, file_bytes)
                    except Exception as save_err:
                        logger.warning("Could not save to persistent storage: %s", save_err)

                artifact = DocumentArtifact(
                    id=doc_id,
                    workflow_run_id=workflow_run_id,
                    node_run_id=node_run_id,
                    property_record_id=property_record_id,
                    source_configuration_id=source_configuration_id,
                    document_type=doc_type,
                    original_filename=title,
                    stored_filename=stored_name,
                    source_url=doc_url,
                    source_page_url=d.get("source_url") or doc_url,
                    storage_key=persistent_key,
                    mime_type=mime,
                    file_size=file_size,
                    checksum_sha256=sha256,
                    page_count=1,
                    status=DocumentStatus.completed.value,
                    is_duplicate=False,
                )
                doc_service.db.add(artifact)
                try:
                    await doc_service.db.commit()
                    await doc_service.db.refresh(artifact)
                except Exception as commit_err:
                    logger.debug("Document artifact commit note: %s", commit_err)

        if artifact:
            registered_meta.append({
                "id": str(artifact.id),
                "filename": artifact.original_filename,
                "document_type": artifact.document_type,
                "status": artifact.status,
                "file_size": artifact.file_size,
                "page_count": artifact.page_count,
                "is_duplicate": False,
                "error_message": None,
            })
        else:
            registered_meta.append({
                "id": str(uuid.uuid4()),
                "filename": title,
                "document_type": doc_type,
                "status": "completed",
                "file_size": file_size,
                "page_count": 1,
                "is_duplicate": False,
                "error_message": None,
            })

    return registered_meta
