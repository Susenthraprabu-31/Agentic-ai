"""Open a real Chrome window for manual Cloudflare verification and save Playwright storage state."""

from __future__ import annotations

import json
from pathlib import Path

from app.browser.tax_browser_profile import (
    MIAMI_DADE_TAX_PORTAL_URL,
    open_tax_browser_for_manual_setup,
)


async def open_miami_dade_tax_browser_and_save_auth(
    *,
    portal_url: str = MIAMI_DADE_TAX_PORTAL_URL,
    wait_timeout_sec: float = 900.0,
    poll_sec: float = 2.0,
    output_path: Path | None = None,
) -> Path:
    """Launches persistent tax browser profile; returns path to auth.json."""
    _profile, auth_out = await open_tax_browser_for_manual_setup(
        portal_url=portal_url,
        wait_timeout_sec=wait_timeout_sec,
        poll_sec=poll_sec,
        output_path=output_path,
    )
    return auth_out


def validate_storage_state_bytes(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("auth file must be a JSON object")
    if "cookies" not in data or not isinstance(data["cookies"], list):
        raise ValueError("auth file must include a cookies array (Playwright storageState)")
    return data