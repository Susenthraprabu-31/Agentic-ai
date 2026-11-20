"""Pause automation for human Cloudflare/CAPTCHA resolution on the live Playwright session."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page

from app.browser.evidence_collector import EvidenceCollector
from app.browser.navigation import best_page_url, ensure_portal_page, page_needs_portal_navigation
from app.browser.security_detector import SecurityDetectionResult, SecurityDetector
from app.core.config import settings
from app.core.logging import get_logger
from app.services.execution_cancel import is_cancel_signalled

logger = get_logger(__name__)

_redis_client: Any | None = None
_redis_loop: Any | None = None
_INTERVENTION_TTL = 86_400


def _state_key(run_id: str) -> str:
    return f"execution:security_manual:{run_id}"


def _cleared_key(run_id: str) -> str:
    return f"execution:security_manual_cleared:{run_id}"


def _pref_key(run_id: str) -> str:
    return f"execution:manual_intervention_pref:{run_id}"


async def _redis() -> Any | None:
    global _redis_client, _redis_loop
    try:
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
        logger.debug("Redis unavailable for security manual intervention: %s", exc)
        _redis_client = None
        _redis_loop = None
        return None


def manual_intervention_default_enabled() -> bool:
    return bool(getattr(settings, "BROWSER_SECURITY_MANUAL_INTERVENTION_ENABLED", True))


def manual_intervention_enabled() -> bool:
    """Sync default only — prefer ``is_manual_intervention_enabled_for_run`` in async paths."""
    return manual_intervention_default_enabled()


async def init_run_manual_intervention_pref(run_id: str) -> None:
    """Seed per-run preference from env default when a workflow run starts."""
    client = await _redis()
    if not client:
        return
    key = _pref_key(str(run_id))
    try:
        if await client.get(key) is None:
            val = "1" if manual_intervention_default_enabled() else "0"
            await client.set(key, val, ex=_INTERVENTION_TTL)
    except Exception as exc:
        logger.debug("Init manual intervention pref: %s", exc)


async def _get_run_pref_bool(run_id: str) -> bool | None:
    client = await _redis()
    if not client:
        return None
    try:
        raw = await client.get(_pref_key(str(run_id)))
        if raw is None:
            return None
        return raw in ("1", "true", "True", "yes", "on")
    except Exception:
        return None


async def set_manual_intervention_pref(run_id: str, enabled: bool) -> None:
    client = await _redis()
    if not client:
        return
    try:
        await client.set(_pref_key(str(run_id)), "1" if enabled else "0", ex=_INTERVENTION_TTL)
    except Exception as exc:
        logger.warning("Failed to set manual intervention pref for %s: %s", run_id, exc)


async def get_manual_intervention_pref(run_id: str) -> dict[str, Any]:
    if await _get_run_pref_bool(str(run_id)) is None:
        await init_run_manual_intervention_pref(str(run_id))
    default = manual_intervention_default_enabled()
    override = await _get_run_pref_bool(str(run_id))
    effective = default if override is None else override
    return {
        "enabled": effective,
        "default_from_env": default,
        "overridden": override is not None,
    }


async def is_manual_intervention_enabled_for_run(run_id: str | None) -> bool:
    if not run_id:
        return manual_intervention_default_enabled()
    override = await _get_run_pref_bool(str(run_id))
    if override is not None:
        return override
    return manual_intervention_default_enabled()


async def set_manual_intervention_state(
    run_id: str,
    *,
    active: bool,
    reason: str | None = None,
    event_type: str | None = None,
    portal_url: str | None = None,
    url: str | None = None,
) -> None:
    client = await _redis()
    if not client:
        return
    key = _state_key(str(run_id))
    if not active:
        try:
            await client.delete(key)
        except Exception:
            pass
        return
    payload = {
        "active": True,
        "reason": reason or "Security verification required",
        "event_type": event_type or "cloudflare",
        "portal_url": portal_url or "",
        "url": url or "",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeout_sec": int(getattr(settings, "BROWSER_MANUAL_INTERVENTION_TIMEOUT_SEC", 600)),
    }
    try:
        await client.set(key, json.dumps(payload), ex=_INTERVENTION_TTL)
        await client.delete(_cleared_key(str(run_id)))
    except Exception as exc:
        logger.warning("Failed to set manual intervention state: %s", exc)


async def get_manual_intervention_state(run_id: str) -> dict[str, Any] | None:
    client = await _redis()
    if not client:
        return None
    try:
        raw = await client.get(_state_key(str(run_id)))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def signal_manual_intervention_complete(run_id: str) -> bool:
    client = await _redis()
    if not client:
        return False
    try:
        await client.set(_cleared_key(str(run_id)), "1", ex=3600)
        return True
    except Exception:
        return False


async def _user_signalled_cleared(run_id: str) -> bool:
    client = await _redis()
    if not client:
        return False
    try:
        return (await client.get(_cleared_key(str(run_id)))) == "1"
    except Exception:
        return False


async def _log_intervention_activity(
    *,
    workflow_run_id: str,
    node_id: str | None,
    description: str,
    status: str,
    url: str | None,
) -> None:
    try:
        from app.browser.browser_manager import get_browser_manager

        bm = await get_browser_manager()
        await bm.log_session_activity(
            action="security_manual",
            description=description,
            node_id=node_id,
            step_index=None,
            status=status,
            url=url,
        )
    except Exception:
        pass


async def _stabilize_page_on_portal(page: Page, portal_url: str | None, workflow_run_id: str, node_id: str | None) -> None:
    """Prevent about:blank drift without reloading an in-progress Cloudflare challenge."""
    target = (portal_url or "").strip()
    if not target:
        target = best_page_url(page)
    if not target or target.startswith("about:"):
        return
    curr = page.url or ""
    if curr.startswith("about:") or curr == "chrome-error://chromewebdata/":
        await ensure_portal_page(
            page,
            target,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            wait_for_mount=False,
        )
        return
    if not page_needs_portal_navigation(page, target):
        return
    try:
        sec = await SecurityDetector.inspect_page(page)
        if sec.is_detected:
            return
    except Exception:
        pass
    await ensure_portal_page(
        page,
        target,
        workflow_run_id=workflow_run_id,
        node_id=node_id,
        wait_for_mount=False,
    )


async def wait_for_manual_security_clearance(
    page: Page,
    sec: SecurityDetectionResult,
    *,
    workflow_run_id: str | None,
    node_id: str | None = None,
    portal_url: str | None = None,
) -> bool:
    """
    Block until the security barrier clears (detector), the user signals complete, or timeout.
    Returns True when automation may resume.
    """
    if not workflow_run_id:
        return False
    run_id = str(workflow_run_id)
    if not await is_manual_intervention_enabled_for_run(run_id):
        return False
    timeout_sec = int(getattr(settings, "BROWSER_MANUAL_INTERVENTION_TIMEOUT_SEC", 600))
    poll_sec = float(getattr(settings, "BROWSER_MANUAL_INTERVENTION_POLL_SEC", 2.0))
    portal = (portal_url or sec.url or best_page_url(page) or "").strip()

    try:
        await EvidenceCollector.capture_evidence(
            page, run_id, str(node_id or ""), reason=sec.reason or "Manual security intervention"
        )
    except Exception:
        pass

    await set_manual_intervention_state(
        run_id,
        active=True,
        reason=sec.reason,
        event_type=sec.event_type,
        portal_url=portal,
        url=page.url if not page.is_closed() else sec.url,
    )

    await _log_intervention_activity(
        workflow_run_id=run_id,
        node_id=node_id,
        description=(
            "Cloudflare/CAPTCHA detected — complete verification in the Playwright Chrome window "
            "on the worker, then click “I've completed verification” in Execution Results."
        ),
        status="AWAITING_MANUAL",
        url=best_page_url(page, portal),
    )

    logger.warning(
        "Security barrier on run %s — waiting up to %ss for manual resolution (%s)",
        run_id,
        timeout_sec,
        sec.reason,
    )

    deadline = time.time() + timeout_sec
    last_blank_fix = 0.0

    while time.time() < deadline:
        if not await is_manual_intervention_enabled_for_run(run_id):
            logger.info("Manual intervention disabled for run %s — stopping wait", run_id)
            await set_manual_intervention_state(run_id, active=False)
            return False

        if await is_cancel_signalled(run_id):
            logger.info("Manual intervention aborted — execution cancelled")
            break

        if await _user_signalled_cleared(run_id):
            logger.info("User signalled security verification complete for run %s", run_id)
            await page.wait_for_timeout(1500)
            break

        now = time.time()
        if now - last_blank_fix >= poll_sec:
            last_blank_fix = now
            try:
                await _stabilize_page_on_portal(page, portal, run_id, node_id)
            except Exception as stab_err:
                logger.debug("Portal stabilize during manual wait: %s", stab_err)

        try:
            from app.services.browser_live_input import apply_pending_live_inputs

            await apply_pending_live_inputs(page, run_id)
        except Exception as input_err:
            logger.debug("Live input drain: %s", input_err)

        try:
            from app.services.browser_live_view import capture_frame_from_page

            if getattr(settings, "BROWSER_LIVE_VIEW_ENABLED", True):
                await capture_frame_from_page(page, run_id)
        except Exception:
            pass

        try:
            check = await SecurityDetector.inspect_page(page)
            if not check.is_detected:
                logger.info("Security barrier cleared on run %s — resuming automation", run_id)
                await set_manual_intervention_state(run_id, active=False)
                await _log_intervention_activity(
                    workflow_run_id=run_id,
                    node_id=node_id,
                    description="Security verification cleared — resuming automation",
                    status="COMPLETED",
                    url=best_page_url(page, portal),
                )
                return True
        except Exception:
            pass

        if await _user_signalled_cleared(run_id):
            recheck = await SecurityDetector.inspect_page(page)
            if not recheck.is_detected:
                await set_manual_intervention_state(run_id, active=False)
                await _log_intervention_activity(
                    workflow_run_id=run_id,
                    node_id=node_id,
                    description="User confirmed verification — barrier cleared",
                    status="COMPLETED",
                    url=best_page_url(page, portal),
                )
                return True

        await asyncio.sleep(poll_sec)

    await set_manual_intervention_state(run_id, active=False)
    await _log_intervention_activity(
        workflow_run_id=run_id,
        node_id=node_id,
        description="Manual security intervention timed out or was cancelled",
        status="FAILED",
        url=best_page_url(page, portal),
    )
    return False


async def try_manual_security_resolution(
    page: Page,
    sec: SecurityDetectionResult,
    *,
    workflow_run_id: str | None,
    node_id: str | None = None,
    portal_url: str | None = None,
) -> bool:
    if not sec.is_detected:
        return True
    return await wait_for_manual_security_clearance(
        page,
        sec,
        workflow_run_id=workflow_run_id,
        node_id=node_id,
        portal_url=portal_url,
    )


async def raise_or_resolve_security(
    page: Page,
    sec: SecurityDetectionResult,
    *,
    workflow_run_id: str | None,
    node_id: str | None = None,
    portal_url: str | None = None,
) -> None:
    """Wait for human CAPTCHA resolution when enabled; otherwise raise SecurityChallengeException."""
    from app.browser.step_runner import SecurityChallengeException

    if not sec.is_detected:
        return
    cleared = await try_manual_security_resolution(
        page,
        sec,
        workflow_run_id=workflow_run_id,
        node_id=node_id,
        portal_url=portal_url,
    )
    if not cleared:
        raise SecurityChallengeException(sec)