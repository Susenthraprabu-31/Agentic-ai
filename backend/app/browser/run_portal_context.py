"""Per workflow run portal URL for blank-frame recovery and live viewport metadata."""

from __future__ import annotations

_run_portal_urls: dict[str, str] = {}


def set_run_portal_url(run_id: str | None, portal_url: str | None) -> None:
    if not run_id or not (portal_url or "").strip():
        return
    _run_portal_urls[str(run_id)] = portal_url.strip()


def get_run_portal_url(run_id: str | None) -> str | None:
    if not run_id:
        return None
    return _run_portal_urls.get(str(run_id))


def clear_run_portal_url(run_id: str | None) -> None:
    if run_id:
        _run_portal_urls.pop(str(run_id), None)