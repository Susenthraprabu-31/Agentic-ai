"""Document downloader supporting direct HTTP requests, streaming to part files, and HTTP Range resumption."""

import asyncio
from dataclasses import dataclass
import hashlib
import os
import re
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse
import uuid

import httpx
from playwright.async_api import Page

from app.browser.evidence_collector import EvidenceCollector
from app.browser.security_detector import SecurityDetector
from app.browser.url_validator import SSRFProtectionException, URLValidator
from app.core.config import settings
from app.core.logging import get_logger
from app.resilience.exceptions import RetryExhaustedException
from app.resilience.retry_manager import retry_manager
from app.resilience.retry_policy import DOCUMENT_DOWNLOAD_POLICY

logger = get_logger(__name__)


@dataclass
class DownloadResult:
    """Outcome of a document download attempt with resilience metadata."""
    success: bool
    data: bytes | None = None
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    original_filename: str = "document.pdf"
    error_message: str | None = None
    is_security_challenge: bool = False
    security_event_type: str | None = None

    # Resilience Fields (Phase 3D.4)
    temp_filepath: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    range_resumed: bool = False
    checksum_sha256: str | None = None


class DocumentDownloader:
    """
    Downloads document binaries via direct HTTP/HTTPS requests or Playwright browser events.
    Streams directly to disk `.part` files, supports HTTP Range resumption, enforces size boundaries,
    and handles anti-bot challenge halts without tripping circuit breakers.
    """

    MAX_RETRIES = getattr(settings, "DOCUMENT_DOWNLOAD_MAX_ATTEMPTS", 3)
    TIMEOUT_SECONDS = getattr(settings, "DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS", 120)
    MAX_SIZE = getattr(settings, "DOCUMENT_MAX_FILE_SIZE_BYTES", 26214400)  # 25 MB

    def __init__(self, page: Page | None = None, run_id: str = "unknown", node_id: str = "document_node") -> None:
        self.page = page
        self.run_id = run_id
        self.node_id = node_id

    async def download_direct(
        self,
        url: str,
        referer: str | None = None,
        max_size: int | None = None,
        artifact_id: uuid.UUID | str | None = None,
        resume_temp_path: str | None = None,
        resume_etag: str | None = None,
    ) -> DownloadResult:
        """
        Direct download using an asynchronous HTTP client with streaming to a `.part` file and HTTP Range support.
        """
        effective_max_size = max_size or self.MAX_SIZE

        # Ensure temp download directory exists
        temp_dir = os.path.abspath(settings.DOCUMENT_TEMP_STORAGE_DIR)
        os.makedirs(temp_dir, exist_ok=True)

        target_artifact_id = str(artifact_id or uuid.uuid4())
        part_filepath = resume_temp_path or os.path.join(temp_dir, f"{target_artifact_id}.part")

        # 1. Handle synthetic data: URLs in test environments
        if url.startswith("data:"):
            URLValidator.validate_url(url)
            try:
                header, base64_data = url.split(",", 1)
                import base64
                data = base64.b64decode(base64_data)
                ct = "application/pdf"
                if "text/html" in header:
                    ct = "text/html"

                with open(part_filepath, "wb") as f:
                    f.write(data)

                checksum = hashlib.sha256(data).hexdigest()
                return DownloadResult(
                    success=True,
                    data=data,
                    temp_filepath=part_filepath,
                    downloaded_bytes=len(data),
                    total_bytes=len(data),
                    status_code=200,
                    content_type=ct,
                    final_url=url[:50],
                    original_filename="synthetic.pdf",
                    checksum_sha256=checksum,
                )
            except Exception as e:
                return DownloadResult(
                    success=False,
                    error_message=f"Failed decoding data URI: {e}",
                )

        # 2. SSRF Validation
        try:
            URLValidator.validate_url(url)
        except SSRFProtectionException as exc:
            return DownloadResult(
                success=False,
                error_message=f"SSRF violation: {str(exc)}",
                is_security_challenge=True,
                security_event_type="blocked",
            )

        # 3. Extract browser cookies if page is available
        cookies_dict: dict[str, str] = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/octet-stream,*/*",
        }
        if referer:
            headers["Referer"] = referer

        if self.page:
            try:
                pw_cookies = await self.page.context.cookies([url])
                for c in pw_cookies:
                    cookies_dict[c["name"]] = c["value"]
            except Exception:
                pass

        # 4. Check for existing partial file for Range resumption
        existing_bytes = 0
        if resume_temp_path and os.path.isfile(resume_temp_path):
            existing_bytes = os.path.getsize(resume_temp_path)

        # 5. Perform Download with Centralized Retry Engine
        async def _single_download_attempt() -> DownloadResult:
            request_headers = dict(headers)
            attempt_resumed = False
            start_byte = 0

            if existing_bytes > 0:
                request_headers["Range"] = f"bytes={existing_bytes}-"
                if resume_etag:
                    request_headers["If-Range"] = resume_etag

            async with httpx.AsyncClient(
                headers=request_headers,
                cookies=cookies_dict,
                follow_redirects=True,
                timeout=self.TIMEOUT_SECONDS,
            ) as client:
                async with client.stream("GET", url) as response:
                    final_url = str(response.url)
                    URLValidator.validate_url(final_url)

                    status = response.status_code
                    ct = response.headers.get("content-type", "")
                    etag = response.headers.get("etag")
                    last_mod = response.headers.get("last-modified")

                    # Handle 416 Range Not Satisfiable by discarding existing file and retrying without Range
                    if status == 416 and existing_bytes > 0:
                        logger.warning("HTTP 416 received for %s. Resetting partial file and restarting...", url)
                        if os.path.exists(part_filepath):
                            try:
                                os.remove(part_filepath)
                            except OSError:
                                pass
                        raise httpx.RequestError("HTTP 416 Range Not Satisfiable; restarting fresh")

                    # Anti-bot detection on response
                    if status in (403, 429):
                        if status == 429:
                            response.raise_for_status()
                        return DownloadResult(
                            success=False,
                            status_code=status,
                            content_type=ct,
                            final_url=final_url,
                            error_message=f"Access denied (HTTP {status}) downloading document.",
                            is_security_challenge=True,
                            security_event_type="cloudflare",
                        )

                    if status >= 500:
                        response.raise_for_status()

                    if status not in (200, 206):
                        return DownloadResult(
                            success=False,
                            status_code=status,
                            content_type=ct,
                            final_url=final_url,
                            error_message=f"HTTP download returned status {status}.",
                        )

                    # Determine file write mode and Range status
                    if status == 206:
                        attempt_resumed = True
                        file_mode = "ab"
                        hasher = hashlib.sha256()
                        if os.path.exists(part_filepath):
                            with open(part_filepath, "rb") as existing_f:
                                while chunk := existing_f.read(settings.DOCUMENT_DOWNLOAD_CHUNK_SIZE_BYTES):
                                    hasher.update(chunk)
                        total_bytes = existing_bytes
                    else:
                        attempt_resumed = False
                        file_mode = "wb"
                        hasher = hashlib.sha256()
                        total_bytes = 0

                    # Parse Content-Length or total bytes if provided
                    header_len = response.headers.get("content-length")
                    reported_total = None
                    if header_len and header_len.isdigit():
                        reported_total = int(header_len) + (existing_bytes if attempt_resumed else 0)

                    # Stream directly to temporary part file
                    with open(part_filepath, file_mode) as out_f:
                        async for chunk in response.aiter_bytes(chunk_size=settings.DOCUMENT_DOWNLOAD_CHUNK_SIZE_BYTES):
                            total_bytes += len(chunk)
                            if total_bytes > effective_max_size:
                                out_f.close()
                                if os.path.exists(part_filepath):
                                    try:
                                        os.remove(part_filepath)
                                    except OSError:
                                        pass
                                return DownloadResult(
                                    success=False,
                                    status_code=status,
                                    content_type=ct,
                                    final_url=final_url,
                                    error_message=f"File exceeds maximum allowed size ({effective_max_size} bytes).",
                                )
                            out_f.write(chunk)
                            hasher.update(chunk)

                    final_checksum = hasher.hexdigest()

                    # Determine filename
                    filename = "document.pdf"
                    cd = response.headers.get("content-disposition", "")
                    if "filename=" in cd:
                        match = re.search(r'filename=["\']?([^"\';]+)["\']?', cd)
                        if match:
                            filename = match.group(1).strip()
                    elif final_url.split("?")[0].endswith(".pdf"):
                        filename = os.path.basename(final_url.split("?")[0])

                    # Read binary for callers that need it in-memory
                    file_data = None
                    try:
                        with open(part_filepath, "rb") as final_f:
                            file_data = final_f.read()
                    except Exception:
                        pass

                    return DownloadResult(
                        success=True,
                        data=file_data,
                        temp_filepath=part_filepath,
                        downloaded_bytes=total_bytes,
                        total_bytes=reported_total or total_bytes,
                        status_code=status,
                        content_type=ct,
                        final_url=final_url,
                        original_filename=filename,
                        etag=etag,
                        last_modified=last_mod,
                        range_resumed=attempt_resumed,
                        checksum_sha256=final_checksum,
                    )

        host = urlparse(url).netloc or "external"
        circuit_key = f"document_endpoint:{host}"

        try:
            return await retry_manager.execute(
                _single_download_attempt,
                policy=DOCUMENT_DOWNLOAD_POLICY,
                operation_name="document_download_direct",
                operation_id=url,
                workflow_run_id=uuid.UUID(self.run_id) if self._is_uuid(self.run_id) else None,
                circuit_key=circuit_key,
                circuit_type="document_download",
            )
        except SSRFProtectionException as exc:
            return DownloadResult(
                success=False,
                error_message=f"SSRF violation on redirect: {str(exc)}",
                is_security_challenge=True,
                security_event_type="blocked",
            )
        except RetryExhaustedException as exc:
            return DownloadResult(
                success=False,
                error_message=f"Network failure downloading document: {exc.last_error or exc}",
            )
        except Exception as exc:
            return DownloadResult(
                success=False,
                error_message=f"Network failure downloading document: {str(exc)}",
            )

    def _is_uuid(self, val: str) -> bool:
        try:
            uuid.UUID(val)
            return True
        except Exception:
            return False

    async def download_via_browser_event(
        self,
        button_selector: str,
        timeout_ms: int = 15000,
        artifact_id: uuid.UUID | str | None = None,
    ) -> DownloadResult:
        """
        Triggers browser download by interacting with a button or link that initiates a file download.
        """
        if not self.page:
            return DownloadResult(success=False, error_message="No active browser page for browser-triggered download.")

        sec_dom = await SecurityDetector.inspect_page(self.page)
        if sec_dom.is_detected:
            await EvidenceCollector.capture_evidence(
                self.page, self.run_id, self.node_id, reason=sec_dom.reason
            )
            return DownloadResult(
                success=False,
                error_message=f"Security challenge encountered: {sec_dom.reason}",
                is_security_challenge=True,
                security_event_type=sec_dom.event_type,
            )

        try:
            button_el = self.page.locator(button_selector).first
            if not await button_el.is_visible(timeout=3000):
                return DownloadResult(
                    success=False,
                    error_message=f"Download trigger element '{button_selector}' not visible.",
                )

            async with self.page.expect_download(timeout=timeout_ms) as download_info:
                await button_el.click()

            download = await download_info.value
            temp_path = await download.path()
            if not temp_path or not os.path.exists(temp_path):
                return DownloadResult(
                    success=False,
                    error_message="Browser download completed but temporary file was not created.",
                )

            with open(temp_path, "rb") as f:
                data = f.read()

            checksum = hashlib.sha256(data).hexdigest()
            suggested_filename = download.suggested_filename or "document.pdf"

            # Copy to .part file in DOCUMENT_TEMP_STORAGE_DIR
            target_artifact_id = str(artifact_id or uuid.uuid4())
            temp_dir = os.path.abspath(settings.DOCUMENT_TEMP_STORAGE_DIR)
            os.makedirs(temp_dir, exist_ok=True)
            part_filepath = os.path.join(temp_dir, f"{target_artifact_id}.part")
            with open(part_filepath, "wb") as f_part:
                f_part.write(data)

            return DownloadResult(
                success=True,
                data=data,
                temp_filepath=part_filepath,
                downloaded_bytes=len(data),
                total_bytes=len(data),
                status_code=200,
                content_type="application/pdf",
                final_url=download.url,
                original_filename=suggested_filename,
                checksum_sha256=checksum,
            )

        except TimeoutError:
            return DownloadResult(
                success=False,
                error_message=f"Timed out waiting for browser download event ({timeout_ms}ms).",
            )
        except Exception as exc:
            return DownloadResult(
                success=False,
                error_message=f"Browser download interaction failed: {str(exc)}",
            )
