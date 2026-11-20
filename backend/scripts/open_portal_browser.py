#!/usr/bin/env python3
"""
Open Google Chrome for any county or tax portal, complete Cloudflare / CAPTCHA manually,
and automatically save the session cookies into backend/auth.json.

Usage on Windows Server (RDP session) or local PC:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\\open_portal_browser.py --url "https://county-taxes.net/fl-miamidade/property-tax"

    # Or any other county portal:
    .\\.venv\\Scripts\\python.exe scripts\\open_portal_browser.py --url "https://www.libertycountytaxcollector.com/Property/SearchSelect?Accept=true&ClearData=True"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

os.environ.setdefault("PLAYWRIGHT_HEADLESS", "false")
os.environ.setdefault("PLAYWRIGHT_BROWSER_MODE", "local_visible")
os.environ.setdefault("PLAYWRIGHT_CHANNEL", "chrome")

from playwright.async_api import async_playwright  # noqa: E402
from app.browser.miami_dade_tax_auth import resolve_miami_dade_tax_auth_path  # noqa: E402
from app.browser.security_clearance import automation_may_resume  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

DEFAULT_URL = "https://county-taxes.net/fl-miamidade/property-tax"


async def run_manual_portal_browser(portal_url: str, wait_timeout_sec: float = 600.0) -> Path:
    out = resolve_miami_dade_tax_auth_path()
    if out is None:
        rel = getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "auth.json")
        out = backend_dir / rel

    channel = (getattr(settings, "PLAYWRIGHT_CHANNEL", "") or "chrome").strip() or "chrome"

    print("=" * 72)
    print("Manual Portal Browser (Cloudflare / CAPTCHA Clearer)")
    print("=" * 72)
    print(f"Target URL: {portal_url}")
    print(f"Browser:    Google Chrome (channel={channel})")
    print(f"Output:     {out}")
    print()
    print("Instructions:")
    print("  1. Chrome window will open.")
    print("  2. Complete the Cloudflare verification ('Verify you are human', Turnstile, etc.).")
    print("  3. The script will automatically detect when the barrier is passed,")
    print("     OR you can press ENTER in this terminal when you are done.")
    print("=" * 72)

    async with async_playwright() as pw:
        launch_kw: dict = {
            "headless": False,
            "slow_mo": int(getattr(settings, "PLAYWRIGHT_SLOW_MO_MS", 0) or 200),
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if channel:
            launch_kw["channel"] = channel

        browser = None
        try:
            browser = await pw.chromium.launch(**launch_kw)
        except Exception as e:
            print(f"[Warning] Failed launching with channel={channel} ({e}). Retrying bundled Chromium...")
            launch_kw.pop("channel", None)
            browser = await pw.chromium.launch(**launch_kw)

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        print(f"\nNavigating to {portal_url}...")
        try:
            await page.goto(portal_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as nav_err:
            print(f"[Note] Navigation event note: {nav_err}")

        # Wait loop: poll for clearance or user ENTER key
        poll_interval = 2.0
        elapsed = 0.0
        passed = False

        print("\nWaiting for Cloudflare verification to clear...")
        while elapsed < wait_timeout_sec:
            if page.is_closed():
                print("\nBrowser page was closed.")
                break

            try:
                # Check if security barrier cleared
                if elapsed > 3.0 and await automation_may_resume(page, portal_url):
                    print("\n[SUCCESS] Cloudflare / Security barrier cleared successfully!")
                    passed = True
                    break
            except Exception:
                pass

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        await asyncio.sleep(1.0)
        await context.storage_state(path=str(out))
        print(f"\n[OK] Session cookies & storage state saved to: {out}")
        print("Celery workers will now reuse these session cookies automatically!")
        await browser.close()

    return out


def parse_args():
    parser = argparse.ArgumentParser(description="Open Chrome to pass Cloudflare and save auth cookies.")
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_URL,
        help="Target portal URL to open (default: Miami-Dade tax portal)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Maximum seconds to wait (default: 600s / 10 min)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_manual_portal_browser(portal_url=args.url, wait_timeout_sec=args.timeout))
