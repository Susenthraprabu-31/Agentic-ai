"""In-process IPC replacing Redis when API and automation share one Python process."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

_frames: dict[str, tuple[bytes, dict[str, Any], float]] = {}
_input_queues: dict[str, deque[str]] = {}
_cancel_flags: dict[str, bool] = {}
_kv_strings: dict[str, tuple[str, float | None]] = {}
_kv_bytes: dict[str, tuple[bytes, float | None]] = {}
_lock = asyncio.Lock()


def use_inprocess_ipc() -> bool:
    return bool(settings.DESKTOP_MODE or not settings.USE_REDIS)


def _expired(expires_at: float | None) -> bool:
    if expires_at is None:
        return False
    return time.time() > expires_at


async def set_cancel_flag(run_id: str, ttl_seconds: int = 86_400) -> None:
    async with _lock:
        _cancel_flags[str(run_id)] = True


async def is_cancel_flag_set(run_id: str) -> bool:
    return bool(_cancel_flags.get(str(run_id)))


async def clear_cancel_flag(run_id: str) -> None:
    async with _lock:
        _cancel_flags.pop(str(run_id), None)


async def publish_live_frame(
    run_id: str,
    image_bytes: bytes,
    meta: dict[str, Any],
    ttl_seconds: int,
) -> None:
    expires = time.time() + ttl_seconds if ttl_seconds > 0 else None
    async with _lock:
        _frames[str(run_id)] = (image_bytes, meta, expires or 0)


async def get_live_frame(run_id: str) -> tuple[bytes | None, dict[str, Any]]:
    rid = str(run_id)
    async with _lock:
        item = _frames.get(rid)
    if not item:
        return None, {}
    data, meta, expires = item
    if expires and time.time() > expires:
        async with _lock:
            _frames.pop(rid, None)
        return None, {}
    return data, meta


async def clear_live_frame(run_id: str) -> None:
    async with _lock:
        _frames.pop(str(run_id), None)


async def enqueue_live_input(run_id: str, payload_json: str, max_queue: int = 32) -> bool:
    rid = str(run_id)
    async with _lock:
        q = _input_queues.setdefault(rid, deque(maxlen=max_queue))
        q.append(payload_json)
    return True


async def drain_live_input(run_id: str) -> list[dict[str, Any]]:
    rid = str(run_id)
    async with _lock:
        q = _input_queues.get(rid)
        if not q:
            return []
        raw = list(q)
        q.clear()
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    return out


async def kv_set_str(key: str, value: str, ex: int | None = None) -> None:
    expires = (time.time() + ex) if ex else None
    async with _lock:
        _kv_strings[key] = (value, expires)


async def kv_get_str(key: str) -> str | None:
    async with _lock:
        item = _kv_strings.get(key)
    if not item:
        return None
    val, exp = item
    if _expired(exp):
        async with _lock:
            _kv_strings.pop(key, None)
        return None
    return val


async def kv_set_bytes(key: str, value: bytes, ex: int | None = None) -> None:
    expires = (time.time() + ex) if ex else None
    async with _lock:
        _kv_bytes[key] = (value, expires)


async def kv_get_bytes(key: str) -> bytes | None:
    async with _lock:
        item = _kv_bytes.get(key)
    if not item:
        return None
    val, exp = item
    if _expired(exp):
        async with _lock:
            _kv_bytes.pop(key, None)
        return None
    return val


async def kv_delete(*keys: str) -> None:
    async with _lock:
        for k in keys:
            _kv_strings.pop(k, None)
            _kv_bytes.pop(k, None)