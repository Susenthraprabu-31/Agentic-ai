"""
Desktop dev server entrypoint.

Uvicorn --reload on Windows calls asyncio_setup(use_subprocess=True), which forces
WindowsSelectorEventLoopPolicy and breaks Playwright subprocess launch.
Programmatic uvicorn.run(..., loop=\"none\") skips that override.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("TITLESEARCH_BACKEND_PORT", "18765"))
    host = os.environ.get("TITLESEARCH_BACKEND_HOST", "127.0.0.1")
    reload = os.environ.get("TITLESEARCH_UVICORN_RELOAD", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["app"] if reload else None,
        loop="none",
    )


if __name__ == "__main__":
    main()