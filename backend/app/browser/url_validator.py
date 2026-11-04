"""URL validation and SSRF protection for browser automation navigation."""

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SSRFProtectionException(Exception):
    """Raised when an automation step attempts to navigate to a prohibited internal or private network destination."""
    pass


class URLValidator:
    """
    Validates automation URLs against SSRF (Server-Side Request Forgery) attacks.
    Ensures Playwright cannot access cloud metadata services (169.254.169.254),
    loopback addresses, or private internal enterprise subnets.
    """

    ALLOWED_SCHEMES = {"http", "https"}

    @classmethod
    def validate_url(cls, url: str, allow_local: bool | None = None) -> bool:
        """
        Validates target URL. Raises SSRFProtectionException if the destination is prohibited.
        """
        if not url:
            raise SSRFProtectionException("Empty navigation URL provided.")

        url_str = str(url).strip()

        # Handle data: URIs used in controlled testing environments
        if url_str.startswith("data:"):
            effective_allow_local = (
                allow_local
                if allow_local is not None
                else getattr(settings, "PLAYWRIGHT_ALLOW_LOCAL_URLS", True)
            )
            if effective_allow_local:
                return True
            raise SSRFProtectionException("Data URIs are prohibited in production mode.")

        parsed = urlparse(url_str)
        scheme = (parsed.scheme or "").lower()

        if scheme not in cls.ALLOWED_SCHEMES:
            raise SSRFProtectionException(
                f"Prohibited URL scheme '{scheme}'. Only HTTP and HTTPS are permitted."
            )

        hostname = parsed.hostname
        if not hostname:
            raise SSRFProtectionException(f"Invalid URL without hostname: '{url_str}'")

        effective_allow_local = (
            allow_local
            if allow_local is not None
            else getattr(settings, "PLAYWRIGHT_ALLOW_LOCAL_URLS", True)
        )

        # In local test/development mode, allow localhost/127.0.0.1
        if effective_allow_local:
            if hostname.lower() in {"localhost", "127.0.0.1", "::1", "test"}:
                return True

        # Resolve hostname to IP addresses
        try:
            addr_info = socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            logger.warning("DNS resolution failed for '%s': %s", hostname, e)
            raise SSRFProtectionException(f"Cannot resolve hostname: '{hostname}'") from e

        for item in addr_info:
            ip_str = item[4][0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)

                # Link-local (e.g. AWS/GCP cloud metadata 169.254.169.254) is ALWAYS strictly blocked
                if ip_obj.is_link_local:
                    logger.warning(
                        "Blocked prohibited cloud metadata destination %s (%s) for URL %s",
                        hostname,
                        ip_str,
                        url_str,
                    )
                    raise SSRFProtectionException(
                        f"Navigation to cloud metadata IP '{ip_str}' ({hostname}) is strictly forbidden."
                    )

                # Check private, loopback, reserved, multicast
                if (
                    ip_obj.is_loopback
                    or ip_obj.is_private
                    or ip_obj.is_reserved
                    or ip_obj.is_multicast
                ):
                    if not effective_allow_local:
                        logger.warning(
                            "Blocked prohibited internal destination %s (%s) for URL %s",
                            hostname,
                            ip_str,
                            url_str,
                        )
                        raise SSRFProtectionException(
                            f"Navigation to internal/private IP '{ip_str}' ({hostname}) is forbidden."
                        )
            except ValueError:
                continue

        return True

