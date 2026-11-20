"""Live Chromium viewport frames for Execution Results UI (Redis-backed, worker → API)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Any | None = None
_redis_loop: Any | None = None

_active_capture_tasks: dict[str, asyncio.Task[None]] = {}


def _frame_key(run_id: str) -> str:
    return f"browser:live_frame:{run_id}"


def _meta_key(run_id: str) -> str:
    return f"browser:live_frame_meta:{run_id}"


async def _get_redis() -> Any | None:
    global _redis_client, _redis_loop
    try:
        loop = asyncio.get_running_loop()
        if _redis_client is not None and _redis_loop == loop:
            return _redis_client
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        await client.ping()
        _redis_client = client
        _redis_loop = loop
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for live browser frames: %s", exc)
        _redis_client = None
        _redis_loop = None
        return None


async def publish_frame(
    run_id: str,
    image_bytes: bytes,
    *,
    url: str | None = None,
    viewport_width: int | None = None,
    viewport_height: int | None = None,
) -> None:
    """Store the latest JPEG frame and metadata for a workflow run."""
    client = await _get_redis()
    if not client or not image_bytes:
        return
    ttl = max(60, int(getattr(settings, "BROWSER_LIVE_FRAME_TTL_SECONDS", 3600)))
    captured_at = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {"url": url or "", "captured_at": captured_at, "content_type": "image/jpeg"}
    if viewport_width:
        meta["viewport_width"] = viewport_width
    if viewport_height:
        meta["viewport_height"] = viewport_height
    try:
        pipe = client.pipeline()
        pipe.set(_frame_key(run_id), image_bytes, ex=ttl)
        pipe.set(_meta_key(run_id), json.dumps(meta).encode("utf-8"), ex=ttl)
        await pipe.execute()
    except Exception as exc:
        logger.warning("Failed to publish live browser frame for %s: %s", run_id, exc)


async def get_latest_frame(run_id: str) -> tuple[bytes | None, dict[str, Any]]:
    client = await _get_redis()
    if not client:
        return None, {}
    try:
        frame, meta_raw = await client.mget(_frame_key(run_id), _meta_key(run_id))
        meta: dict[str, Any] = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw.decode("utf-8"))
            except Exception:
                meta = {}
        if frame:
            return bytes(frame), meta
    except Exception as exc:
        logger.debug("Failed to read live browser frame for %s: %s", run_id, exc)
    return None, {}


async def capture_frame_from_page(page: Page, run_id: str) -> bool:
    """Capture a single viewport JPEG from the active Playwright page."""
    try:
        if page.is_closed():
            return False
        vp = page.viewport_size or {"width": 1280, "height": 800}
        jpeg = await page.screenshot(
            type="jpeg",
            quality=int(getattr(settings, "BROWSER_LIVE_VIEW_JPEG_QUALITY", 72)),
            full_page=False,
            timeout=8000,
        )
        await publish_frame(
            run_id,
            jpeg,
            url=page.url or None,
            viewport_width=int(vp.get("width") or 1280),
            viewport_height=int(vp.get("height") or 800),
        )
        return bool(jpeg)
    except Exception as exc:
        logger.debug("Live Chromium frame capture for %s: %s", run_id, exc)
        return False


async def _capture_loop(page: Page, run_id: str, interval: float) -> None:
    from app.services.execution_cancel import is_cancel_signalled

    if await is_cancel_signalled(run_id):
        return

    async def _tick() -> None:
        try:
            from app.services.browser_live_input import apply_pending_live_inputs

            await apply_pending_live_inputs(page, run_id)
        except Exception:
            pass
        await capture_frame_from_page(page, run_id)

    await _tick()
    while True:
        try:
            await asyncio.sleep(interval)
            if await is_cancel_signalled(run_id) or page.is_closed():
                break
            await _tick()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Live Chromium capture loop for %s: %s", run_id, exc)


def start_live_capture(page: Page, workflow_run_id: str | None) -> None:
    """Start periodic viewport capture for the given workflow run."""
    if not workflow_run_id or not getattr(settings, "BROWSER_LIVE_VIEW_ENABLED", True):
        return
    run_id = str(workflow_run_id)
    prev = _active_capture_tasks.pop(run_id, None)
    if prev and not prev.done():
        prev.cancel()
    interval = float(getattr(settings, "BROWSER_LIVE_VIEW_INTERVAL_SEC", 1.25))
    task = asyncio.create_task(_capture_loop(page, run_id, interval))
    _active_capture_tasks[run_id] = task


async def stop_live_capture(workflow_run_id: str | None, page: Page | None = None) -> None:
    """Cancel capture task and optionally persist one final frame."""
    if not workflow_run_id:
        return
    run_id = str(workflow_run_id)
    task = _active_capture_tasks.pop(run_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    if page is not None:
        await capture_frame_from_page(page, run_id)


async def stop_live_capture_for_run(run_id: str) -> None:
    """Cancel the capture task for a run (no final frame)."""
    task = _active_capture_tasks.pop(run_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def clear_live_frames(run_id: str) -> None:
    client = await _get_redis()
    if not client:
        return
    try:
        await client.delete(_frame_key(run_id), _meta_key(run_id))
    except Exception:
        pass