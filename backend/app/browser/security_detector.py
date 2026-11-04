"""Centralized security and anti-bot challenge detection engine."""

from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page, Response

from app.core.logging import get_logger
from app.models.security_event import SecurityEventType

logger = get_logger(__name__)


@dataclass
class SecurityDetectionResult:
    """Outcome of automated security challenge detection."""
    is_detected: bool
    event_type: str = SecurityEventType.other.value
    url: str | None = None
    title: str | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class SecurityDetector:
    """
    Detects Cloudflare, Turnstile, CAPTCHA, and rate limiting barriers on public records portals.
    Adheres strictly to the architectural rule:
    Never defeat or bypass access controls or security mechanisms. Detect and stop for authorized verification.
    """

    CLOUDFLARE_TITLES = [
        "just a moment...",
        "attention required! | cloudflare",
        "cloudflare - access denied",
        "ddos protection by cloudflare",
    ]

    CLOUDFLARE_SELECTORS = [
        "#challenge-running",
        "#cf-challenge-running",
        ".cf-browser-verification",
        "#cf-content",
        "div[id*='cf-wrapper']",
        "#challenge-form",
    ]

    TURNSTILE_SELECTORS = [
        "iframe[src*='challenges.cloudflare.com']",
        ".cf-turnstile",
        "input[name='cf-turnstile-response']",
        "div.cf-turnstile",
    ]

    CAPTCHA_SELECTORS = [
        "iframe[src*='google.com/recaptcha']",
        "iframe[src*='recaptcha/api2']",
        ".g-recaptcha",
        "#g-recaptcha",
        "iframe[src*='hcaptcha.com']",
        ".h-captcha",
        "#captcha",
        "div[id*='captcha']",
    ]

    @classmethod
    async def inspect_response(cls, response: Response | None, url: str) -> SecurityDetectionResult | None:
        """Inspects HTTP status code and response headers for bot protection barriers."""
        if not response:
            return None

        status_code = response.status
        headers = response.headers

        # Check HTTP 429 Rate Limit
        if status_code == 429:
            return SecurityDetectionResult(
                is_detected=True,
                event_type=SecurityEventType.rate_limit.value,
                url=url,
                reason="HTTP 429 Too Many Requests detected.",
                details={"status_code": 429, "headers": dict(headers)},
            )

        # Check HTTP 403 Forbidden from Cloudflare or WAF
        if status_code == 403:
            server = headers.get("server", "").lower()
            cf_ray = headers.get("cf-ray")
            if "cloudflare" in server or cf_ray:
                return SecurityDetectionResult(
                    is_detected=True,
                    event_type=SecurityEventType.cloudflare.value,
                    url=url,
                    reason="Cloudflare 403 Access Denied / WAF barrier.",
                    details={"status_code": 403, "cf_ray": cf_ray, "server": server},
                )
            return SecurityDetectionResult(
                is_detected=True,
                event_type=SecurityEventType.blocked.value,
                url=url,
                reason="HTTP 403 Forbidden access barrier.",
                details={"status_code": 403, "headers": dict(headers)},
            )

        return None

    @classmethod
    async def inspect_page(cls, page: Page) -> SecurityDetectionResult:
        """
        Inspects the live Playwright Page DOM and title for active challenge screens.
        """
        url = page.url
        try:
            title = (await page.title()).lower().strip()
        except Exception:
            title = ""

        # 1. Check Cloudflare Page Titles
        for cf_title in cls.CLOUDFLARE_TITLES:
            if cf_title in title:
                logger.warning("Cloudflare challenge title detected on %s: '%s'", url, title)
                return SecurityDetectionResult(
                    is_detected=True,
                    event_type=SecurityEventType.cloudflare.value,
                    url=url,
                    title=title,
                    reason=f"Cloudflare verification title detected: '{title}'",
                    details={"matched_title": cf_title},
                )

        # 2. Check Cloudflare DOM elements
        for selector in cls.CLOUDFLARE_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    logger.warning("Cloudflare challenge element visible on %s: %s", url, selector)
                    return SecurityDetectionResult(
                        is_detected=True,
                        event_type=SecurityEventType.cloudflare.value,
                        url=url,
                        title=title,
                        reason=f"Cloudflare verification element visible: '{selector}'",
                        details={"matched_selector": selector},
                    )
            except Exception:
                pass

        # 3. Check Turnstile widgets
        for selector in cls.TURNSTILE_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    logger.warning("Cloudflare Turnstile challenge detected on %s: %s", url, selector)
                    return SecurityDetectionResult(
                        is_detected=True,
                        event_type=SecurityEventType.turnstile.value,
                        url=url,
                        title=title,
                        reason=f"Cloudflare Turnstile widget detected: '{selector}'",
                        details={"matched_selector": selector},
                    )
            except Exception:
                pass

        # 4. Check reCAPTCHA / hCaptcha widgets
        for selector in cls.CAPTCHA_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    logger.warning("CAPTCHA challenge detected on %s: %s", url, selector)
                    return SecurityDetectionResult(
                        is_detected=True,
                        event_type=SecurityEventType.captcha.value,
                        url=url,
                        title=title,
                        reason=f"CAPTCHA challenge detected: '{selector}'",
                        details={"matched_selector": selector},
                    )
            except Exception:
                pass

        return SecurityDetectionResult(
            is_detected=False,
            url=url,
            title=title,
        )
