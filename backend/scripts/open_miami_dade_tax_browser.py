#!/usr/bin/env python3
"""
Open Google Chrome for Miami-Dade county-taxes.net, pass Cloudflare manually, save auth.json.

Run on a machine WITH a desktop (your Windows PC — not headless EC2):

  cd backend
  set PLAYWRIGHT_CHANNEL=chrome
  .venv\\Scripts\\python.exe scripts\\open_miami_dade_tax_browser.py

When the tax search page appears, the script saves backend/auth.json automatically.
Copy auth.json to your EC2 worker (same path) so Celery reuses your Cloudflare session.
"""

from __future__ import annotations

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

from app.browser.tax_browser_profile import MIAMI_DADE_TAX_PORTAL_URL  # noqa: E402


async def main() -> None:
    print()
    print("=" * 72)
    print("Miami-Dade tax portal — manual Cloudflare browser")
    print("=" * 72)
    print(f"URL: {MIAMI_DADE_TAX_PORTAL_URL}")
    print()
    print("1. Chrome will open.")
    print("2. Complete Cloudflare (Verify you are human / Turnstile).")
    print("3. Wait until the property tax SEARCH page is visible.")
    print("4. This script saves a persistent profile + backend/auth.json and closes Chrome.")
    print("   (Max wait 15 minutes.)")
    print()
    from app.browser.tax_browser_profile import open_tax_browser_for_manual_setup

    profile_dir, auth_path = await open_tax_browser_for_manual_setup(wait_timeout_sec=900.0)
    print()
    print(f"Profile dir: {profile_dir}")
    print(f"Saved auth: {auth_path}")
    print("Upload to EC2: scp backend/auth.json ubuntu@<host>:~/Agentic-ai/backend/auth.json")
    print("Restart celery_worker after upload.")
    print()


if __name__ == "__main__":
    asyncio.run(main())