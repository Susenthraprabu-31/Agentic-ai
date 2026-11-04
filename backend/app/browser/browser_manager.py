"""Playwright browser lifecycle and isolated context manager with resilience, health monitoring, and recovery."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from typing import AsyncGenerator
import uuid

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.core.config import settings
from app.core.logging import get_logger
from app.models.browser_session import BrowserLifecycleState
from app.services.browser_session_service import BrowserSessionService

logger = get_logger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def check_display_available() -> tuple[bool, str | None]:
    """
    Checks if a graphical desktop display is available for visible browser execution.
    Returns (True, None) if supported, or (False, diagnostic_message) if headless/non-interactive.
    """
    import sys
    import os

    if sys.platform.startswith("win"):
        try:
            import ctypes
            session_id = ctypes.c_uint32()
            if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
                if session_id.value == 0:
                    return False, (
                        "Visible browser mode requested (PLAYWRIGHT_HEADLESS=false / local_visible), "
                        "but the process is running in Windows Session 0 (non-interactive service session). "
                        "Visible desktop windows cannot open from Session 0. Run the Celery worker interactively in a normal desktop shell."
                    )
        except Exception:
            pass
        return True, None

    elif sys.platform.startswith("linux"):
        display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        if not display:
            return False, (
                "Visible browser mode requested (PLAYWRIGHT_HEADLESS=false / local_visible), "
                "but no graphical display environment variable found (neither DISPLAY nor WAYLAND_DISPLAY is set). "
                "If running inside Docker, WSL without WSLg, or remote SSH, visible windows cannot open. "
                "Set PLAYWRIGHT_HEADLESS=true or start an X11 display / virtual frame buffer (Xvfb)."
            )
        return True, None

    return True, None


class BrowserManager:
    """Manages the Playwright runtime and Browser instance lifecycle with health tracking and recovery."""

    _instance: "BrowserManager | None" = None

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

        # Resilience & Lifecycle State
        self._state: BrowserLifecycleState = BrowserLifecycleState.CLOSED
        self._current_session_id: str | None = None
        self._session_started_at: datetime | None = None
        self._active_contexts_count: int = 0
        self._total_pages_created: int = 0
        self._total_contexts_created: int = 0
        self._failure_count: int = 0
        self._last_failure_reason: str | None = None
        self._session_service = BrowserSessionService()

    def _get_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._lock is None or self._loop != current_loop:
            self._lock = asyncio.Lock()
        return self._lock

    @classmethod
    async def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def state(self) -> BrowserLifecycleState:
        return self._state

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    @property
    def is_visible_mode(self) -> bool:
        """Determines if the browser is configured to launch with a visible GUI window."""
        return (
            getattr(settings, "PLAYWRIGHT_BROWSER_MODE", "headless") == "local_visible"
            or not getattr(settings, "PLAYWRIGHT_HEADLESS", True)
        )

    def check_display_available(self) -> tuple[bool, str | None]:
        """Checks if a graphical desktop display is available for visible browser execution."""
        return check_display_available()

    def is_browser_healthy(self) -> bool:
        """Verifies if the current browser instance is alive, connected, and in an operable state."""
        if self._browser is None:
            return False
        if not self._browser.is_connected():
            return False
        if self._state in (
            BrowserLifecycleState.CRASHED,
            BrowserLifecycleState.DISCONNECTED,
            BrowserLifecycleState.CLOSING,
            BrowserLifecycleState.CLOSED,
        ):
            return False
        return True

    def is_context_healthy(self, context: BrowserContext | None) -> bool:
        """Verifies if the context belongs to a healthy browser and has not been closed."""
        if not self.is_browser_healthy() or context is None:
            return False
        try:
            _ = context.pages
            return True
        except Exception:
            return False

    def is_page_healthy(self, page: Page | None) -> bool:
        """Verifies page liveness and readiness for interactions."""
        if not self.is_browser_healthy() or page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def _on_browser_disconnected(self) -> None:
        """Callback invoked by Playwright when the browser process terminates unexpectedly."""
        logger.error(
            "Playwright browser disconnected unexpectedly! Session ID: %s",
            self._current_session_id,
        )
        self._state = BrowserLifecycleState.DISCONNECTED
        self._failure_count += 1
        self._last_failure_reason = "Playwright browser disconnected unexpectedly"

        if self._current_session_id:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._session_service.update_state(
                        self._current_session_id,
                        state=BrowserLifecycleState.DISCONNECTED.value,
                        is_healthy=False,
                        failure_reason="Browser disconnected unexpectedly",
                    )
                )
            except Exception:
                pass

    async def get_browser(self, workflow_run_id: uuid.UUID | None = None) -> Browser:
        """Lazily initializes Playwright and the browser instance, or re-initializes on loop mismatch or crash."""
        current_loop = asyncio.get_running_loop()
        lock = self._get_lock()

        async with lock:
            now = datetime.now(timezone.utc)
            lifetime_exceeded = False
            if self._session_started_at is not None:
                elapsed = (now - self._session_started_at).total_seconds()
                if elapsed > settings.BROWSER_MAX_SESSION_LIFETIME_SECONDS:
                    logger.info("Browser session %s exceeded max lifetime (%ss). Recycling...", self._current_session_id, elapsed)
                    lifetime_exceeded = True

            needs_init = (
                self._browser is None
                or not self._browser.is_connected()
                or self._loop != current_loop
                or self._state in (BrowserLifecycleState.CRASHED, BrowserLifecycleState.DISCONNECTED, BrowserLifecycleState.CLOSED)
                or lifetime_exceeded
            )

            if needs_init:
                await self._force_cleanup_locked()

                self._state = BrowserLifecycleState.INITIALIZING
                self._loop = current_loop
                self._current_session_id = f"sess_{uuid.uuid4().hex[:12]}"
                self._session_started_at = now
                self._active_contexts_count = 0

                logger.info("Initializing Playwright runtime (Session: %s)...", self._current_session_id)
                self._playwright = await async_playwright().start()

                is_visible_mode = (
                    getattr(settings, "PLAYWRIGHT_BROWSER_MODE", "headless") == "local_visible"
                    or not getattr(settings, "PLAYWRIGHT_HEADLESS", True)
                )

                if is_visible_mode:
                    display_ok, diagnostic = check_display_available()
                    if not display_ok:
                        logger.error(diagnostic)
                        raise RuntimeError(diagnostic)

                is_headless = not is_visible_mode
                slow_mo = getattr(settings, "PLAYWRIGHT_SLOW_MO_MS", 0) if is_visible_mode else 0

                browser_type_name = getattr(settings, "PLAYWRIGHT_BROWSER", "chromium")
                browser_type = getattr(self._playwright, browser_type_name, self._playwright.chromium)
                launch_args = [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
                if is_visible_mode:
                    launch_args.extend(["--start-maximized", "--window-size=1280,800"])

                logger.info(
                    "Launching %s browser (headless=%s, slow_mo=%dms, mode=%s, timeout=%dms)",
                    browser_type_name,
                    is_headless,
                    slow_mo,
                    getattr(settings, "PLAYWRIGHT_BROWSER_MODE", "headless"),
                    settings.BROWSER_LAUNCH_TIMEOUT_MS,
                )
                self._browser = await browser_type.launch(
                    headless=is_headless,
                    slow_mo=slow_mo,
                    args=launch_args,
                    timeout=settings.BROWSER_LAUNCH_TIMEOUT_MS,
                )

                self._browser.on("disconnected", lambda: self._on_browser_disconnected())
                self._state = BrowserLifecycleState.READY

                await self._session_service.register_session(
                    session_id=self._current_session_id,
                    workflow_run_id=workflow_run_id,
                    browser_type=browser_type_name,
                    state=BrowserLifecycleState.READY.value,
                )

        return self._browser

    async def recover_browser(self, reason: str = "Unspecified crash", workflow_run_id: uuid.UUID | None = None) -> Browser:
        """Recovers an inoperable or crashed browser without disrupting running worker execution."""
        logger.warning("Initiating browser recovery. Reason: %s", reason)
        lock = self._get_lock()
        async with lock:
            if self._current_session_id:
                await self._session_service.update_state(
                    self._current_session_id,
                    state=BrowserLifecycleState.CRASHED.value,
                    is_healthy=False,
                    failure_reason=reason,
                )
            self._state = BrowserLifecycleState.CRASHED
            self._failure_count += 1
            self._last_failure_reason = reason
            await self._force_cleanup_locked()

        return await self.get_browser(workflow_run_id=workflow_run_id)

    async def _force_cleanup_locked(self) -> None:
        """Internal helper to shut down browser and playwright with timeout protection."""
        if self._browser is not None:
            try:
                await asyncio.wait_for(
                    self._browser.close(),
                    timeout=settings.BROWSER_CONTEXT_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning("Error forcing browser close during cleanup: %s", e)
            self._browser = None

        if self._playwright is not None:
            try:
                await asyncio.wait_for(
                    self._playwright.stop(),
                    timeout=settings.BROWSER_CONTEXT_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning("Error stopping Playwright during cleanup: %s", e)
            self._playwright = None

    @asynccontextmanager
    async def new_isolated_context(
        self,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        locale: str = "en-US",
        timezone_id: str = "America/New_York",
        workflow_run_id: uuid.UUID | str | None = None,
    ) -> AsyncGenerator[tuple[BrowserContext, Page], None]:
        """
        Creates an isolated browser context for a workflow step or search.
        Enforces worker context limits, tracks session health, and guarantees timeout-bounded cleanup.
        """
        effective_wf_id = None
        if workflow_run_id:
            try:
                effective_wf_id = uuid.UUID(str(workflow_run_id))
            except Exception:
                pass

        if self._active_contexts_count >= settings.BROWSER_MAX_CONTEXTS_PER_WORKER:
            logger.warning(
                "Active contexts count (%d) reached worker limit (%d). Waiting briefly...",
                self._active_contexts_count,
                settings.BROWSER_MAX_CONTEXTS_PER_WORKER,
            )
            await asyncio.sleep(0.5)

        if not self.is_browser_healthy():
            browser = await self.recover_browser(reason="Context creation requested with unhealthy browser", workflow_run_id=effective_wf_id)
        else:
            browser = await self.get_browser(workflow_run_id=effective_wf_id)

        self._active_contexts_count += 1
        self._total_contexts_created += 1
        self._state = BrowserLifecycleState.BUSY

        context: BrowserContext | None = None
        page: Page | None = None

        try:
            context = await browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                user_agent=DEFAULT_USER_AGENT,
                locale=locale,
                timezone_id=timezone_id,
                ignore_https_errors=True,
                accept_downloads=False,
            )

            context.set_default_navigation_timeout(settings.BROWSER_NAVIGATION_TIMEOUT_MS)
            context.set_default_timeout(settings.PLAYWRIGHT_ACTION_TIMEOUT_MS)

            # Route interceptor to protect Playwright evaluation from portal scripts that declare lexical `let Map`
            import re
            async def _fix_broken_map_scripts(route):
                try:
                    response = await route.fetch()
                    text = await response.text()
                    if "let Map" in text or "let Map;" in text or "let Map\n" in text or "let Map\r" in text:
                        fixed = re.sub(r'\blet\s+Map\b', 'let CollierMap', text)
                        fixed = re.sub(r'\bMap\b', 'CollierMap', fixed)
                        await route.fulfill(response=response, body=fixed)
                    else:
                        await route.fulfill(response=response, body=text)
                except Exception:
                    await route.continue_()

            try:
                await context.route("**/*Detail.js*", _fix_broken_map_scripts)
                await context.route("**/*detail.js*", _fix_broken_map_scripts)
            except Exception:
                pass

            page = await context.new_page()
            self._total_pages_created += 1

            # Always register or refresh session record for this specific execution context
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            self._current_session_id = session_id
            try:
                await self._session_service.register_session(
                    session_id=session_id,
                    workflow_run_id=effective_wf_id,
                    browser_type=getattr(settings, "PLAYWRIGHT_BROWSER", "chromium"),
                    state=BrowserLifecycleState.BUSY.value,
                )
            except Exception as reg_err:
                logger.debug("Browser session registration note: %s", reg_err)

            yield context, page

        except Exception as exc:
            err_msg = str(exc)
            if "Target closed" in err_msg or "Browser closed" in err_msg or "Connection closed" in err_msg:
                logger.error("Browser crashed or closed during context execution: %s", exc)
                self._state = BrowserLifecycleState.CRASHED
                if self._current_session_id:
                    await self._session_service.update_state(
                        self._current_session_id,
                        state=BrowserLifecycleState.CRASHED.value,
                        is_healthy=False,
                        failure_reason=err_msg,
                    )
            raise

        finally:
            # If visible mode is active, optionally keep browser visible briefly before cleanup
            is_visible_mode = (
                getattr(settings, "PLAYWRIGHT_BROWSER_MODE", "headless") == "local_visible"
                or not getattr(settings, "PLAYWRIGHT_HEADLESS", True)
            )
            keep_open_secs = getattr(settings, "PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS", 0)
            if is_visible_mode and keep_open_secs > 0:
                try:
                    await asyncio.sleep(keep_open_secs)
                except (asyncio.CancelledError, Exception):
                    pass

            if page is not None:
                try:
                    if not page.is_closed():
                        await asyncio.wait_for(
                            page.close(),
                            timeout=settings.BROWSER_CONTEXT_CLEANUP_TIMEOUT_SECONDS,
                        )
                except Exception as e:
                    logger.warning("Error closing browser page: %s", e)

            if context is not None:
                try:
                    await asyncio.wait_for(
                        context.close(),
                        timeout=settings.BROWSER_CONTEXT_CLEANUP_TIMEOUT_SECONDS,
                    )
                except Exception as e:
                    logger.warning("Error closing isolated browser context: %s", e)

            self._active_contexts_count = max(0, self._active_contexts_count - 1)
            if self._active_contexts_count == 0 and self._state == BrowserLifecycleState.BUSY:
                self._state = BrowserLifecycleState.READY
                if self._current_session_id:
                    await self._session_service.update_state(
                        self._current_session_id,
                        state=BrowserLifecycleState.READY.value,
                    )

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    @property
    def is_visible_mode(self) -> bool:
        """Determines if the browser is configured to launch with a visible GUI window."""
        return (
            getattr(settings, "PLAYWRIGHT_BROWSER_MODE", "headless") == "local_visible"
            or not getattr(settings, "PLAYWRIGHT_HEADLESS", True)
        )

    def check_display_available(self) -> tuple[bool, str | None]:
        """Checks if a graphical desktop display is available for visible browser execution."""
        return check_display_available()

    async def log_session_activity(
        self,
        action: str,
        description: str | None = None,
        node_id: str | None = None,
        step_index: int | None = None,
        status: str = "RUNNING",
        url: str | None = None,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Records an execution step or milestone in the browser session's activity log."""
        if self._current_session_id:
            await self._session_service.record_activity(
                session_id=self._current_session_id,
                node_id=node_id,
                step_index=step_index,
                action=action,
                description=description,
                status=status,
                url=url,
                error=error,
                duration_ms=duration_ms,
            )

    async def close(self) -> None:
        """Cleanly closes browser and Playwright runtime, marking session closed."""
        lock = self._get_lock()
        async with lock:
            self._state = BrowserLifecycleState.CLOSING
            if self._current_session_id:
                await self._session_service.update_state(
                    self._current_session_id,
                    state=BrowserLifecycleState.CLOSED.value,
                    is_healthy=False,
                )
            await self._force_cleanup_locked()
            self._state = BrowserLifecycleState.CLOSED


async def get_browser_manager() -> BrowserManager:
    """Helper to retrieve the BrowserManager singleton."""
    return await BrowserManager.get_instance()


@asynccontextmanager
async def get_browser_context(
    workflow_run_id: uuid.UUID | str | None = None,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
) -> AsyncGenerator[tuple[BrowserContext, Page], None]:
    """Helper context manager to acquire an isolated browser context and page."""
    manager = await BrowserManager.get_instance()
    async with manager.new_isolated_context(
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        locale=locale,
        timezone_id=timezone_id,
        workflow_run_id=workflow_run_id,
    ) as (context, page):
        yield context, page
