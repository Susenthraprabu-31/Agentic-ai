"""Parse and format recorder book/page search values."""
from __future__ import annotations

import re
from typing import Optional

BOOK_PAGE_SPLIT_RE = re.compile(r"^\s*(\d+)\s*[/\-]\s*(\d+)\s*$")
BOOK_PAGE_LABEL_RE = re.compile(
    r"(?i)^\s*book\s*:?\s*(\d+)\s*(?:[/\-]|page\s*:?\s*)(\d+)\s*$"
)
BOOK_PAGE_INLINE_RE = re.compile(r"(?i)book\s*:?\s*(\d+)\s+page\s*:?\s*(\d+)")


def parse_book_page(
    query_value: str = "",
    book_number: Optional[str] = None,
    page_number: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    book = (book_number or "").strip()
    page = (page_number or "").strip()
    if book and page:
        return book, page

    value = (query_value or "").strip()
    if not value:
        return None

    for pattern in (BOOK_PAGE_SPLIT_RE, BOOK_PAGE_LABEL_RE):
        match = pattern.match(value)
        if match:
            return match.group(1), match.group(2)

    inline = BOOK_PAGE_INLINE_RE.search(value)
    if inline:
        return inline.group(1), inline.group(2)

    return None


def format_book_page(book_number: str, page_number: str) -> str:
    return f"{book_number.strip()}/{page_number.strip()}"


def format_book_page_label(book_number: str, page_number: str) -> str:
    return f"{book_number.strip()} / {page_number.strip()}"

