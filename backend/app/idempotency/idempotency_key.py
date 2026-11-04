"""Deterministic key hashing, payload serialization, and format validation."""

import hashlib
import json
import uuid
from datetime import date, datetime
from typing import Any


class CanonicalJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder producing deterministic, sorted outputs for hashing."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "dict"):
            return obj.dict()
        return super().default(obj)


def validate_key_format(key: str | None) -> bool:
    """
    Validates user-supplied Idempotency-Key.
    Must be a non-empty string of 4 to 256 printable ASCII characters without control characters.
    """
    if not key or not isinstance(key, str):
        return False
    key_str = key.strip()
    if not (4 <= len(key_str) <= 256):
        return False
    # Only allow printable ASCII (codes 32 to 126)
    return all(32 <= ord(c) <= 126 for c in key_str)


def generate_key_hash(raw_key: str) -> str:
    """Computes SHA-256 hex digest of raw idempotency key."""
    hasher = hashlib.sha256()
    hasher.update(str(raw_key).encode("utf-8"))
    return hasher.hexdigest()


def generate_payload_hash(payload: Any) -> str:
    """
    Computes deterministic SHA-256 hex digest of request payload.
    Uses canonical JSON sorting to ensure dict key ordering does not alter the hash.
    """
    if payload is None:
        payload = {}
    elif hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif hasattr(payload, "dict"):
        payload = payload.dict()

    canonical_json = json.dumps(
        payload,
        cls=CanonicalJSONEncoder,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    hasher = hashlib.sha256()
    hasher.update(canonical_json.encode("utf-8"))
    return hasher.hexdigest()


def generate_node_idempotency_key(
    workflow_run_id: uuid.UUID | str,
    node_id: str,
    workflow_version_id: uuid.UUID | str | None,
    input_data: Any,
) -> str:
    """Generates a deterministic logical key for node execution."""
    input_hash = generate_payload_hash(input_data)
    v_id = str(workflow_version_id or "unspecified")
    return f"node:{workflow_run_id}:{node_id}:{v_id}:{input_hash}"


def generate_document_idempotency_key(
    workflow_run_id: uuid.UUID | str | None,
    source_url: str,
) -> str:
    """Generates a deterministic logical key for document retrieval operation."""
    wf_id = str(workflow_run_id or "global")
    clean_url = str(source_url).strip()
    return f"doc:{wf_id}:{clean_url}"


def generate_task_idempotency_key(
    task_name: str,
    resource_id: uuid.UUID | str,
) -> str:
    """Generates a deterministic logical key for background Celery task execution."""
    return f"task:{task_name}:{resource_id}"
