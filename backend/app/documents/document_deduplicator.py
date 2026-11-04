"""Document deduplication using SHA-256 checksums."""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.document_artifact import DocumentArtifact

logger = get_logger(__name__)


@dataclass
class DeduplicationResult:
    """Outcome of deduplication evaluation."""
    is_duplicate: bool
    checksum_sha256: str
    duplicate_of_id: uuid.UUID | None = None
    existing_storage_key: str | None = None


class DocumentDeduplicator:
    """
    Computes cryptographic checksums and checks for duplicate documents
    to avoid saving redundant binary files while preserving independent property linkages.
    """

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        """Computes hex digest of SHA-256 for document bytes."""
        hasher = hashlib.sha256()
        hasher.update(data)
        return hasher.hexdigest()

    @classmethod
    async def check_duplicate(
        cls,
        data: bytes,
        db: AsyncSession | None = None,
        workflow_run_id: uuid.UUID | None = None,
    ) -> DeduplicationResult:
        """
        Evaluates whether data matches an already stored document.
        """
        checksum = cls.compute_sha256(data)

        if not db:
            return DeduplicationResult(
                is_duplicate=False,
                checksum_sha256=checksum,
            )

        # Check if identical checksum already exists
        stmt = (
            select(DocumentArtifact)
            .where(
                DocumentArtifact.checksum_sha256 == checksum,
                DocumentArtifact.is_duplicate == False,
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            logger.info(
                "Document matches existing artifact %s (checksum: %s...)",
                existing.id,
                checksum[:12],
            )
            return DeduplicationResult(
                is_duplicate=True,
                checksum_sha256=checksum,
                duplicate_of_id=existing.id,
                existing_storage_key=existing.storage_key,
            )

        return DeduplicationResult(
            is_duplicate=False,
            checksum_sha256=checksum,
        )

