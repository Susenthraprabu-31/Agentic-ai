"""Resolve per-install data directories for the Windows desktop application."""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    explicit = (os.environ.get("TITLESEARCH_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (Path(local) / "TitleSearch").resolve()
    return (Path.home() / ".titlesearch").resolve()


def ensure_data_subdirs(base: Path) -> dict[str, Path]:
    """Create standard layout under the install data root."""
    layout = {
        "root": base,
        "database": base / "database",
        "documents": base / "documents",
        "downloads": base / "downloads",
        "cache": base / "cache",
        "logs": base / "logs",
        "exports": base / "exports",
        "evidence": base / "evidence",
        "config": base / "config",
        "recipes": base / "recipes",
    }
    for key, path in layout.items():
        if key != "root":
            path.mkdir(parents=True, exist_ok=True)
    return layout