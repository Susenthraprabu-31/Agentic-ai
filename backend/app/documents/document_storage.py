"""Persistent document storage abstraction supporting local filesystem and S3."""

import os
import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def sanitize_filename(name: str) -> str:
    """Sanitizes filename removing control characters and path separators."""
    if not name:
        return "document.pdf"
    clean = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', name).strip()
    return clean[:200] if clean else "document.pdf"


class DocumentStorageBackend(ABC):
    """Abstract interface for storing and retrieving document binary payloads."""

    @abstractmethod
    async def save_file(self, storage_key: str, data: bytes) -> str:
        """Stores binary bytes under storage_key and returns location identifier."""
        pass

    @abstractmethod
    async def get_file(self, storage_key: str) -> bytes | None:
        """Retrieves binary bytes for storage_key, or None if not found."""
        pass

    @abstractmethod
    async def file_exists(self, storage_key: str) -> bool:
        """Checks if file exists under storage_key."""
        pass

    @abstractmethod
    async def promote_temp_file(self, temp_filepath: str, storage_key: str) -> str:
        """Atomically promotes a temporary file into persistent storage."""
        pass


class LocalDocumentStorage(DocumentStorageBackend):
    """Local filesystem persistent storage backend with strict path traversal protection."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.DOCUMENT_STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, storage_key: str) -> Path:
        """Resolves storage_key against base_dir and blocks directory traversal attempts."""
        normalized_key = storage_key.strip().lstrip("/\\")
        target_path = (self.base_dir / normalized_key).resolve()

        # Prevent directory traversal attacks
        if not str(target_path).startswith(str(self.base_dir)):
            raise ValueError(f"Security error: storage key '{storage_key}' attempts path traversal.")

        return target_path

    async def save_file(self, storage_key: str, data: bytes) -> str:
        safe_path = self._resolve_safe_path(storage_key)
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        with open(safe_path, "wb") as f:
            f.write(data)

        logger.info("Saved %d bytes to local storage at '%s'", len(data), safe_path)
        return str(safe_path)

    async def get_file(self, storage_key: str) -> bytes | None:
        safe_path = self._resolve_safe_path(storage_key)
        if not safe_path.is_file():
            return None
        with open(safe_path, "rb") as f:
            return f.read()

    async def file_exists(self, storage_key: str) -> bool:
        safe_path = self._resolve_safe_path(storage_key)
        return safe_path.is_file()

    async def promote_temp_file(self, temp_filepath: str, storage_key: str) -> str:
        safe_path = self._resolve_safe_path(storage_key)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        try:
            os.replace(temp_filepath, safe_path)
        except OSError:
            shutil.move(temp_filepath, safe_path)
        logger.info("Promoted temp file '%s' atomically to '%s'", temp_filepath, safe_path)
        return str(safe_path)


class S3DocumentStorage(DocumentStorageBackend):
    """AWS S3 document backend (sync boto3 calls in thread pool)."""

    def __init__(self) -> None:
        self._bucket = settings.S3_BUCKET_NAME
        self._prefix = (settings.CUSTOMER_STORAGE_PREFIX or "default").strip("/")

    def _full_key(self, storage_key: str) -> str:
        clean = storage_key.strip().lstrip("/\\")
        return f"{self._prefix}/{clean}"

    def _client(self):
        import boto3

        kw: dict = {}
        if settings.S3_ENDPOINT_URL:
            kw["endpoint_url"] = settings.S3_ENDPOINT_URL
        if settings.S3_ACCESS_KEY and settings.S3_SECRET_KEY:
            kw["aws_access_key_id"] = settings.S3_ACCESS_KEY
            kw["aws_secret_access_key"] = settings.S3_SECRET_KEY
        return boto3.client("s3", **kw)

    async def save_file(self, storage_key: str, data: bytes) -> str:
        import asyncio

        key = self._full_key(storage_key)
        client = self._client()

        def _upload() -> str:
            client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType="application/pdf")
            return f"s3://{self._bucket}/{key}"

        uri = await asyncio.to_thread(_upload)
        logger.info("Uploaded %d bytes to %s", len(data), uri)
        return uri

    async def get_file(self, storage_key: str) -> bytes | None:
        import asyncio

        key = self._full_key(storage_key)
        client = self._client()

        def _download() -> bytes | None:
            try:
                obj = client.get_object(Bucket=self._bucket, Key=key)
                return obj["Body"].read()
            except Exception:
                return None

        return await asyncio.to_thread(_download)

    async def file_exists(self, storage_key: str) -> bool:
        import asyncio

        key = self._full_key(storage_key)
        client = self._client()

        def _head() -> bool:
            try:
                client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    async def promote_temp_file(self, temp_filepath: str, storage_key: str) -> str:
        with open(temp_filepath, "rb") as f:
            data = f.read()
        return await self.save_file(storage_key, data)


def resolve_document_storage_backend() -> DocumentStorageBackend:
    backend = (settings.DOCUMENT_STORAGE_BACKEND or "local").lower().strip()
    if backend == "s3":
        return S3DocumentStorage()
    if backend == "composite":
        return LocalDocumentStorage()
    return LocalDocumentStorage()


class DocumentStorage:
    """High-level document storage manager coordinating keys, backend, and persistence."""

    def __init__(self, backend: DocumentStorageBackend | None = None) -> None:
        self.backend = backend or resolve_document_storage_backend()

    @staticmethod
    def generate_storage_key(
        workflow_run_id: uuid.UUID | str | None,
        property_record_id: uuid.UUID | str | None,
        document_id: uuid.UUID | str,
        extension: str = "pdf",
    ) -> str:
        """
        Builds a safe, structured relative storage key:
        {workflow_run_id}/{property_record_id}/{document_id}.pdf
        """
        wf_part = str(workflow_run_id or "unassigned").strip()
        prop_part = str(property_record_id or "general").strip()
        doc_part = str(document_id).strip()
        clean_ext = extension.lstrip(".") or "pdf"

        return f"{wf_part}/{prop_part}/{doc_part}.{clean_ext}"

    async def save(self, storage_key: str, data: bytes) -> str:
        return await self.backend.save_file(storage_key, data)

    async def retrieve(self, storage_key: str) -> bytes | None:
        return await self.backend.get_file(storage_key)

    async def exists(self, storage_key: str) -> bool:
        return await self.backend.file_exists(storage_key)

    async def promote_file(self, temp_filepath: str, storage_key: str) -> str:
        return await self.backend.promote_temp_file(temp_filepath, storage_key)

