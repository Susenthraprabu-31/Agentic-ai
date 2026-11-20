"""Queue pointer events from the API/UI to the Celery worker's active Playwright page."""

from __future__ import annotations

import json
from typing import Any

from playwright.async_api import Page

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Any | None = None
_redis_loop: Any | None = None
_INPUT_TTL = 3600
_MAX_QUEUE = 32


def _input_key(run_id: str) -> str:
    return f"execution:browser_input:{run_id}"


async def _redis() -> Any | None:
    global _redis_client, _redis_loop
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        if _redis_client is not None and _redis_loop == loop:
            return _redis_client
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis_client = client
        _redis_loop = loop
        return client
    except Exception as exc:
        logger.debug("Redis unavailable for browser live input: %s", exc)
        return None


async def enqueue_pointer_event(
    run_id: str,
    *,
    action: str,
    x: float,
    y: float,
    client_width: float,
    client_height: float,
) -> bool:
    client = await _redis()
    if not client:
        return False
    payload = {
        "action": (action or "click").lower(),
        "x": float(x),
        "y": float(y),
        "client_width": max(1.0, float(client_width)),
        "client_height": max(1.0, float(client_height)),
    }
    key = _input_key(str(run_id))
    try:
        await client.lpush(key, json.dumps(payload))
        await client.ltrim(key, 0, _MAX_QUEUE - 1)
        await client.expire(key, _INPUT_TTL)
        return True
    except Exception as exc:
        logger.warning("enqueue_pointer_event failed: %s", exc)
        return False


async def _click_cloudflare_iframe_at_point(page: Page, px: int, py: int) -> bool:
    """If (px, py) falls inside a challenge iframe, click coordinates within that frame."""
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        fu = (frame.url or "").lower()
        if "challenges.cloudflare.com" not in fu and "turnstile" not in fu:
            continue
        try:
            frame_el = await frame.frame_element()
            box = await frame_el.bounding_box()
            if not box:
                continue
            if not (
                box["x"] <= px <= box["x"] + box["width"]
                and box["y"] <= py <= box["y"] + box["height"]
            ):
                continue
            lx = px - box["x"]
            ly = py - box["y"]
            try:
                await frame.mouse.click(lx, ly)
            except Exception:
                await frame.locator("body").click(position={"x": lx, "y": ly}, timeout=2500)
            await page.wait_for_timeout(200)
            return True
        except Exception:
            continue
    return False


async def _perform_viewport_click(page: Page, px: int, py: int) -> None:
    """Click viewport coords; also targets Cloudflare/Turnstile iframes and widgets."""
    await page.mouse.click(px, py, delay=50)
    await _click_cloudflare_iframe_at_point(page, px, py)
    await page.wait_for_timeout(150)
    for sel in (
        "#verification-action",
        ".verification-action",
        "button:has-text('Verify you are human')",
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        ".cf-turnstile",
        "#cf-turnstile",
        "div.cf-turnstile",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            box = await loc.bounding_box(timeout=1200)
            if not box:
                continue
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            await page.mouse.click(cx, cy)
            await page.wait_for_timeout(200)
            try:
                await loc.click(force=True, timeout=2000)
            except Exception:
                pass
            break
        except Exception:
            continue
    for frame in page.frames:
        fu = (frame.url or "").lower()
        if "challenges.cloudflare.com" not in fu and "turnstile" not in fu:
            continue
        try:
            await frame.mouse.click(px, py)
        except Exception:
            try:
                body = frame.locator("body")
                if await body.count() > 0:
                    await body.click(position={"x": px, "y": py}, timeout=2000)
            except Exception:
                pass


async def apply_pending_live_inputs(page: Page, run_id: str) -> int:
    """Drain queued UI clicks onto the worker Playwright page (viewport coordinates)."""
    client = await _redis()
    if not client or page.is_closed():
        return 0
    key = _input_key(str(run_id))
    applied = 0
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        vw = int(vp.get("width") or 1280)
        vh = int(vp.get("height") or 800)
        while applied < _MAX_QUEUE:
            raw = await client.rpop(key)
            if not raw:
                break
            try:
                data = json.loads(raw)
            except Exception:
                continue
            action = str(data.get("action") or "click").lower()
            cw = max(1.0, float(data.get("client_width") or vw))
            ch = max(1.0, float(data.get("client_height") or vh))
            px = int(float(data.get("x", 0)) / cw * vw)
            py = int(float(data.get("y", 0)) / ch * vh)
            px = max(0, min(vw - 1, px))
            py = max(0, min(vh - 1, py))
            if action == "click":
                await _perform_viewport_click(page, px, py)
                applied += 1
                logger.info("Live UI click at (%s, %s) for run %s", px, py, run_id)
            await page.wait_for_timeout(300)
    except Exception as exc:
        logger.debug("apply_pending_live_inputs: %s", exc)
    return applied