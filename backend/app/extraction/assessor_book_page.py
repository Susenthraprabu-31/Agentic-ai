"""Extract book/page from assessor sales information for recorder handoff."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.extraction.book_page import parse_book_page

_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%m-%d-%Y",
)


def _parse_sale_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_valid_sale_date(value: Any) -> bool:
    """Return True when value parses as a valid assessor sale date."""
    return _parse_sale_date(value) is not None


def _entry_date(entry: dict[str, Any]) -> Optional[datetime]:
    return _parse_sale_date(entry.get("recording_date")) or _parse_sale_date(entry.get("sale_date"))


def entries_with_book_page(assessor_record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = assessor_record.get("raw_json") or {}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_entry(entry: dict[str, Any]) -> None:
        book_page = str(entry.get("book_page") or "").strip()
        if not book_page:
            return
        key = f"{book_page}|{entry.get('recording_date')}|{entry.get('sale_date')}"
        if key in seen:
            return
        seen.add(key)
        entries.append(entry)

    for source_list in (raw.get("chain_of_title") or [], raw.get("sales_history") or []):
        if not isinstance(source_list, list):
            continue
        for entry in source_list:
            if not isinstance(entry, dict):
                continue
            if not is_valid_sale_date(entry.get("recording_date") or entry.get("sale_date")):
                continue
            _add_entry(entry)

    return entries

